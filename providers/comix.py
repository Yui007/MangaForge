"""
Comix.to provider for MangaForge.

Uses Playwright DOM scraping and canvas extraction to bypass protections.
"""
import base64
import json
import logging
import re
from typing import List, Optional, Tuple

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False

logger = logging.getLogger(__name__)


class ComixProvider(BaseProvider):
    """Provider for comix.to manga website.

    Uses Playwright to render pages and extract images.
    """

    provider_id = "comix"
    provider_name = "Comix"
    base_url = "https://comix.to"

    def __init__(self) -> None:
        self.config = Config()
        super().__init__()

    # ──────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_manga_code(url_or_id: str) -> str:
        """Extract the short manga code from a URL or slug.

        Examples:
            https://comix.to/title/93q1r-the-summoner → 93q1r
            93q1r-the-summoner                        → 93q1r
            93q1r                                     → 93q1r
        """
        if not url_or_id:
            return ""
        # Strip to last path segment
        segment = url_or_id.rstrip("/").split("/")[-1]
        # Code is the part before the first hyphen
        code = segment.split("-")[0]
        return code

    # ──────────────────────────────────────────────────────────────────
    #  Search
    # ──────────────────────────────────────────────────────────────────

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        """Search Comix.to.
        
        Since direct text search is not supported, we allow users to pass the full series URL
        directly.
        """
        query = query.strip()
        if "comix.to/title/" in query:
            try:
                info = self.get_manga_info(url=query)
                return [MangaSearchResult(
                    provider_id=self.provider_id,
                    manga_id=info.manga_id,
                    title=info.title,
                    cover_url=info.cover_url,
                    url=info.url
                )], False
            except Exception as e:
                logger.error("Failed to resolve URL in search: %s", e)

        logger.debug("Comix search called (query=%s) — text search not supported", query)
        return [], False

    # ──────────────────────────────────────────────────────────────────
    #  Manga info
    # ──────────────────────────────────────────────────────────────────

    def get_manga_info(
        self, manga_id: Optional[str] = None, url: Optional[str] = None
    ) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        if not PLAYWRIGHT_AVAILABLE:
            raise ProviderError("Playwright is not installed. Run 'pip install playwright' and 'playwright install'")

        target_url = url or f"https://comix.to/title/{manga_id}"
        code = self._extract_manga_code(target_url)
        logger.debug("Comix get_manga_info: target_url=%s, code=%s", target_url, code)

        headless = self.config.get("providers.comix.headless", False)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                )
                page = context.new_page()
                
                # Navigate to the page
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                
                # Wait for the initial-data script tag to be present in the DOM
                page.wait_for_selector('script#initial-data', state="attached", timeout=10000)
                
                # Get initial data contents
                initial_data_str = page.locator('script#initial-data').inner_html()
                json_data = json.loads(initial_data_str)
                
                browser.close()
        except Exception as e:
            logger.error(f"Playwright failed to fetch manga info for {code}: {e}")
            raise ProviderError(f"Playwright failed to fetch manga info for {code}: {e}")

        # Find the manga detail query in the json_data
        manga_detail = None
        queries = json_data.get("queries", {})
        for key, val in queries.items():
            if "manga" in key and "detail" in key and code in key:
                manga_detail = val
                break
                
        if not manga_detail:
            raise MangaNotFoundError(f"Could not find manga detail in initial-data for {code}")
            
        # Get alt titles safely
        alt_titles = manga_detail.get("altTitles", [])
        if not isinstance(alt_titles, list):
            alt_titles = [alt_titles] if alt_titles else []
            
        # Poster URL
        poster = manga_detail.get("poster") or {}
        poster_url = ""
        if isinstance(poster, dict):
            poster_url = poster.get("large") or poster.get("medium") or poster.get("small") or ""

        # Status
        status_raw = manga_detail.get("status")
        if isinstance(status_raw, str):
            status = status_raw.capitalize() if status_raw else "Unknown"
        else:
            status = "Unknown"

        # Year
        year = manga_detail.get("year")
        if year is not None:
            try:
                year = int(year)
            except (ValueError, TypeError):
                year = None

        # Genres
        genres = []
        for g in manga_detail.get("genres", []):
            if isinstance(g, dict) and "title" in g:
                genres.append(g["title"])
            elif isinstance(g, str):
                genres.append(g)

        # Synopsis
        description = manga_detail.get("synopsis", "")

        slug = manga_detail.get("url", "").split("/")[-1] if manga_detail.get("url") else code

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=slug,
            title=manga_detail.get("title", "Unknown"),
            alternative_titles=alt_titles,
            cover_url=poster_url,
            url=f"{self.base_url}/title/{slug}",
            description=description,
            authors=[],
            artists=[],
            genres=genres,
            status=status,
            year=year,
        )

    # ──────────────────────────────────────────────────────────────────
    #  Chapters (Playwright DOM scraping)
    # ──────────────────────────────────────────────────────────────────

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """Fetch all chapters using Playwright DOM scraping."""
        if not PLAYWRIGHT_AVAILABLE:
            raise ProviderError("Playwright is not installed. Run 'pip install playwright' and 'playwright install'")

        code = self._extract_manga_code(manga_id)
        preferred_scan = self.config.get("providers.preferred_scanlator", "")
        logger.debug("Comix get_chapters code=%s, manga_id=%s, preferred_scan=%s", code, manga_id, preferred_scan)

        target_url = f"https://comix.to/title/{manga_id}"
        headless = self.config.get("providers.comix.headless", False)

        scrape_js = """() => {
            return Array.from(document.querySelectorAll('.mchap-item')).map(li => {
                const a = li.querySelector('.mchap-row__primary');
                const ch = li.querySelector('.mchap-row__ch');
                const ti = li.querySelector('.mchap-row__title');
                const gp = li.querySelector('.mchap-row__group');
                return {
                    href: a ? a.getAttribute('href') : null,
                    chap_label: ch ? ch.textContent.trim() : null,
                    title: ti ? ti.textContent.trim() : null,
                    group: gp ? (gp.querySelector('span') ? gp.querySelector('span').textContent.trim() : gp.textContent.trim()) : null,
                    group_official: gp ? gp.classList.contains('is-official') : false,
                };
            });
        }"""

        all_rows = []
        seen_ids = set()

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                )
                page = context.new_page()
                
                prev_first_href = None
                consecutive_dup_pages = 0
                max_pages = 200
                
                for page_n in range(1, max_pages + 1):
                    page_url = f"{target_url}?page={page_n}"
                    page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                    
                    if prev_first_href is None:
                        try:
                            page.wait_for_selector(".mchap-row__primary", timeout=10000)
                        except Exception:
                            # If page 1 doesn't render any chapter links, there are none
                            logger.warning(f"No chapters found on page 1 for {code}")
                            break
                    else:
                        # Wait for React to swap the page content
                        import json as std_json
                        js_predicate = (
                            "(() => { const a = document.querySelector('.mchap-row__primary'); "
                            f"return a && a.getAttribute('href') !== {std_json.dumps(prev_first_href)}; }})"
                        )
                        try:
                            page.wait_for_function(js_predicate, timeout=5000)
                        except Exception:
                            # If it didn't change, we likely hit the end or it failed to render new content
                            pass
                            
                    rows = page.evaluate(scrape_js) or []
                    if not rows:
                        break
                        
                    prev_first_href = rows[0].get("href")
                    page_added = 0
                    
                    for row in rows:
                        href = row.get("href")
                        if not href:
                            continue
                        
                        # Parse `/title/{slug}/{chap_id}-chapter-{chap_num}`
                        m = re.match(r".*/title/[^/]+/(\d+)-chapter-(.+)$", href)
                        if not m:
                            continue
                        
                        chap_id_str, chap_num_str = m.group(1), m.group(2)
                        if chap_id_str in seen_ids:
                            continue
                            
                        seen_ids.add(chap_id_str)
                        all_rows.append(row)
                        page_added += 1
                        
                    if page_added == 0:
                        consecutive_dup_pages += 1
                        if consecutive_dup_pages >= 2:
                            break
                    else:
                        consecutive_dup_pages = 0
                        
                browser.close()
        except Exception as e:
            logger.error(f"Playwright failed to fetch chapters for {manga_id}: {e}")
            raise ProviderError(f"Playwright failed to fetch chapters for {manga_id}: {e}")

        # Flatten all items and group by chapter number
        from collections import defaultdict
        by_number = defaultdict(list)
        for row in all_rows:
            href = row.get("href")
            if not href:
                continue
            m = re.match(r".*/title/([^/]+)/(\d+)-chapter-(.+)$", href)
            if not m:
                continue
            
            slug = m.group(1)
            chap_id_val = m.group(2)
            chap_num_str = m.group(3)
            
            row["slug"] = slug
            row["chapter_id"] = chap_id_val
            row["number"] = chap_num_str
            
            by_number[chap_num_str].append(row)

        chapters: List[Chapter] = []
        for number, entries in by_number.items():
            selected = entries[0]  # default

            if preferred_scan and len(entries) > 1:
                for entry in entries:
                    group_name = entry.get("group") or ""
                    if group_name and preferred_scan.lower() == group_name.lower():
                        selected = entry
                        break

            slug = selected["slug"]
            chapter_id_val = selected["chapter_id"]
            ch_title = selected.get("title") or ""
            group_name = selected.get("group")

            display_title = f"Chapter {number}"
            if ch_title:
                display_title += f": {ch_title}"
            if group_name:
                display_title += f" [{group_name}]"

            # Construct a compound chapter_id so get_chapter_images knows the slug and chapter number
            compound_chapter_id = f"{slug}|{chapter_id_val}|{number}"
            chapter_url = f"{self.base_url}/title/{slug}/{chapter_id_val}-chapter-{number}"

            chapters.append(
                Chapter(
                    chapter_id=compound_chapter_id,
                    manga_id=manga_id,
                    title=display_title,
                    chapter_number=number,
                    volume=None,
                    url=chapter_url,
                    release_date=None,
                    language="en",
                )
            )

        chapters.sort(key=lambda c: c.sort_key)
        logger.info("Comix get_chapters returned %d chapters", len(chapters))
        return chapters

    # ──────────────────────────────────────────────────────────────────
    #  Chapter images
    # ──────────────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """Fetch image URLs / data URLs for a chapter using Playwright."""
        if not PLAYWRIGHT_AVAILABLE:
            raise ProviderError("Playwright is not installed. Run 'pip install playwright' and 'playwright install'")

        parts = chapter_id.split("|")
        if len(parts) == 3:
            manga_slug, real_chapter_id, chapter_number = parts
        else:
            manga_slug, real_chapter_id, chapter_number = "manga", chapter_id, "1"

        chapter_url = f"{self.base_url}/title/{manga_slug}/{real_chapter_id}-chapter-{chapter_number}"
        logger.info(f"Fetching chapter images via Playwright DOM for {chapter_url}...")

        headless = self.config.get("providers.comix.headless", False)
        image_urls = []
        page_count = 0

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                )
                context.add_init_script("window.__origToDataURL = HTMLCanvasElement.prototype.toDataURL;")
                page = context.new_page()

                # Preload all the images
                try:
                    page.goto(f"{self.base_url}/", wait_until="domcontentloaded", timeout=15000)
                    page.evaluate("""() => {
                        try {
                            const k = 'reader.default';
                            const cur = JSON.parse(localStorage.getItem(k) || '{}');
                            cur.preload = 'all';
                            localStorage.setItem(k, JSON.stringify(cur));
                        } catch (e) {}
                    }""")
                except Exception as e:
                    logger.warning(f"Failed to set preload settings: {e}")

                # Navigate to the chapter page
                page.goto(chapter_url, wait_until="domcontentloaded", timeout=30000)

                # Wait for reader page elements to load
                page_count = 0
                for _ in range(60):
                    try:
                        page_count = page.evaluate("() => document.querySelectorAll('.rpage-page').length") or 0
                    except Exception:
                        page_count = 0
                    if page_count > 0:
                        break
                    page.wait_for_timeout(500)

                if page_count == 0:
                    logger.error(f"Chapter page had no pages in DOM: {chapter_url}")
                    browser.close()
                    return []

                # Wait for the first page to begin rendering to avoid cold-start timeouts
                try:
                    page.wait_for_selector('.rpage-page[data-page="1"] canvas, .rpage-page[data-page="1"] img', timeout=15000)
                except Exception:
                    pass

                logger.info(f"Chapter has {page_count} pages. Extracting content...")

                # Iterate and capture each page
                for page_num in range(1, page_count + 1):
                    # Scroll page element into view to trigger render/decryption
                    try:
                        page.evaluate(
                            "(n) => { const el = document.querySelector('.rpage-page[data-page=\"' + n + '\"]'); if (el) el.scrollIntoView({behavior: 'instant', block: 'center'}); }",
                            page_num
                        )
                    except Exception:
                        pass

                    # Wait for image element or canvas element to be ready
                    ready = None
                    for _attempt in range(40):
                        try:
                            ready = page.evaluate(
                                """(n) => {
                                    const el = document.querySelector('.rpage-page[data-page="' + n + '"]');
                                    if (!el) return null;
                                    const isLoading = el.classList.contains('is-loading');
                                    
                                    // Check canvas
                                    const c = el.querySelector('canvas');
                                    if (c && c.width > 10 && c.height > 10) {
                                        if (isLoading) return null; // Wait if still loading
                                        const toDataURL = window.__origToDataURL || c.toDataURL;
                                        const data = toDataURL.call(c, 'image/webp', 0.95);
                                        if (data.length < 20000) {
                                            return {type: 'skip'}; // Blank/Ad canvas
                                        }
                                        return {type: 'canvas_data', data: data};
                                    }
                                    
                                    // Check image
                                    const i = el.querySelector('img');
                                    if (i && i.src) {
                                        if (i.complete) {
                                            if (i.naturalWidth > 10 && i.naturalHeight > 10) {
                                                return {type: 'img', src: i.src};
                                            }
                                            if (i.naturalWidth > 0 && i.naturalWidth <= 10) {
                                                return {type: 'skip'}; // 1x1 placeholder
                                            }
                                        }
                                    }
                                    return null;
                                }""",
                                page_num
                            )
                        except Exception:
                            ready = None
                        if ready:
                            break
                        page.wait_for_timeout(250)

                    if not ready:
                        logger.error(f"Page {page_num} timed out waiting for render.")
                        continue

                    if ready.get('type') == 'skip':
                        logger.debug(f"Page {page_num} is an ad/placeholder page. Skipping.")
                        continue

                    if ready.get('type') == 'canvas_data':
                        image_urls.append(ready.get('data'))
                        continue

                    # Extract the image data or URL from image (handling blobs via canvas)
                    try:
                        extracted_url = page.evaluate(
                            """(n) => {
                                try {
                                    const el = document.querySelector('.rpage-page[data-page="' + n + '"]');
                                    if (!el) return null;
                                    
                                    const c = el.querySelector('canvas');
                                    if (c && c.width > 0 && c.height > 0) {
                                        const toDataURL = window.__origToDataURL || c.toDataURL;
                                        return toDataURL.call(c, 'image/webp', 0.95);
                                    }
                                    
                                    const i = el.querySelector('img');
                                    if (i && i.src) {
                                        if (i.src.startsWith('blob:')) {
                                            try {
                                                const canvas = document.createElement('canvas');
                                                canvas.width = i.naturalWidth || i.width;
                                                canvas.height = i.naturalHeight || i.height;
                                                const ctx = canvas.getContext('2d');
                                                ctx.drawImage(i, 0, 0);
                                                const toDataURL = window.__origToDataURL || canvas.toDataURL;
                                                return toDataURL.call(canvas, 'image/webp', 0.95);
                                            } catch (e) {
                                                return null;
                                            }
                                        }
                                        return i.src;
                                    }
                                    return null;
                                } catch (e) {
                                    return null;
                                }
                            }""",
                            page_num
                        )
                    except Exception as e:
                        logger.error(f"Page {page_num} extraction failed: {e}")
                        continue

                    if extracted_url:
                        image_urls.append(extracted_url)
                    else:
                        logger.error(f"Page {page_num} failed to extract valid URL or data.")

                browser.close()
        except Exception as e:
            logger.error(f"Playwright failed to fetch images for chapter {real_chapter_id}: {e}")
            raise ProviderError(f"Playwright failed to fetch images: {e}")

        logger.info(f"Retrieved {len(image_urls)} / {page_count} page images.")
        return image_urls

    # ──────────────────────────────────────────────────────────────────
    #  Image Download / Decoding
    # ──────────────────────────────────────────────────────────────────

    def download_image(self, url: str) -> bytes:
        """Download or decode image data."""
        if url.startswith("data:image/"):
            try:
                header, b64_data = url.split(",", 1)
                return base64.b64decode(b64_data)
            except Exception as e:
                logger.error(f"Failed to decode base64 image data: {e}")
                raise ProviderError(f"Failed to decode base64 image data: {e}")

        # Fallback to standard HTTP download using httpx.Client from BaseProvider
        try:
            logger.debug(f"Downloading image: {url}")
            response = self.session.get(url)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"HTTP error downloading image {url}: {e}")
            raise ProviderError(f"Failed to download image: {e}")

    # ──────────────────────────────────────────────────────────────────
    #  Headers
    # ──────────────────────────────────────────────────────────────────

    def get_headers(self) -> dict:
        return {
            "User-Agent": self.config.get(
                "network.user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            ),
            "Accept": "application/json",
            "Referer": self.base_url,
        }
