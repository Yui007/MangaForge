"""MangaKakalot provider implementation for MangaForge.

Uses FlareSolverr to bypass Cloudflare, then curl_cffi for all subsequent
requests (reusing the solved cookies + user-agent).
Images on the CDN use simple referer headers (no extra solve needed).

Requirements:
    pip install curl-cffi beautifulsoup4 requests
    FlareSolverr running at http://localhost:8191
"""
import logging
import re
from typing import Dict, List, Optional, Tuple, Type
from urllib.parse import urljoin, urlparse

import requests as plain_requests  # only for FlareSolverr (local call)
from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

from core.base_provider import (
    BaseProvider,
    ProviderError,
    MangaNotFoundError,
    ChapterNotFoundError,
)
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)

# FlareSolverr defaults
DEFAULT_FLARESOLVERR_URL = "http://localhost:8191/v1"
FLARESOLVERR_SESSION_ID = "mangaforge"


class MangakakalotProvider(BaseProvider):
    """Provider that scrapes data from https://mangakakalot.gg.

    Uses FlareSolverr for the initial Cloudflare challenge, then
    curl_cffi with the solved cookies for all subsequent requests.
    """

    provider_id = "mangakakalot"
    provider_name = "MangaKakalot"
    base_url = "https://www.mangakakalot.gg"

    def __init__(self) -> None:
        self.config = Config()
        self.retry_attempts = self.config.get("network.retry_attempts", 3)
        self.timeout = self.config.get("network.timeout", 30)
        self.flaresolverr_url = self.config.get(
            "network.flaresolverr_url", DEFAULT_FLARESOLVERR_URL
        )

        # We must call super().__init__() which creates self.session (httpx).
        # We immediately replace it with a curl_cffi session.
        super().__init__()

        # Close the default httpx session – we won't use it.
        self.session.close()

        # These will be populated after the first FlareSolverr solve.
        self._cf_user_agent: str = ""
        self._cffi_session: Optional[cffi_requests.Session] = None
        self._solved: bool = False

    # ══════════════════════════════════════════════════════════════════════
    #  FlareSolverr integration
    # ══════════════════════════════════════════════════════════════════════

    def _ensure_solved(self) -> None:
        """Lazily solve the Cloudflare challenge if not done yet."""
        if self._solved and self._cffi_session is not None:
            return
        solver_resp = self._flaresolverr_solve(self.base_url)
        self._build_cffi_session(solver_resp)
        self._solved = True

    def _flaresolverr_solve(self, url: str) -> dict:
        """Send a request through FlareSolverr to solve the CF challenge."""
        logger.info("[FlareSolverr] Solving: %s", url)
        payload = {
            "cmd": "request.get",
            "url": url,
            "session": FLARESOLVERR_SESSION_ID,
            "maxTimeout": 60000,
        }
        try:
            resp = plain_requests.post(
                self.flaresolverr_url, json=payload, timeout=90
            )
            resp.raise_for_status()
        except plain_requests.exceptions.ConnectionError as exc:
            raise ProviderError(
                f"Cannot connect to FlareSolverr at {self.flaresolverr_url}. "
                "Start it with: docker run -d --name=flaresolverr "
                "-p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest"
            ) from exc

        data = resp.json()
        if data.get("status") != "ok":
            raise ProviderError(
                f"FlareSolverr error: {data.get('message', data)}"
            )

        logger.info(
            "[FlareSolverr] Solved! cookies=%d",
            len(data["solution"]["cookies"]),
        )
        return data

    def _build_cffi_session(self, solver_response: dict) -> None:
        """Build a curl_cffi session using cookies from FlareSolverr."""
        solution = solver_response["solution"]
        self._cf_user_agent = solution["userAgent"]
        cookies = solution["cookies"]

        session = cffi_requests.Session(impersonate="chrome124")
        for ck in cookies:
            session.cookies.set(
                ck["name"],
                ck["value"],
                domain=ck.get("domain", "mangakakalot.gg"),
            )

        self._cffi_session = session
        logger.info("[Session] UA: %s...", self._cf_user_agent[:80])
        logger.info(
            "[Session] Cookies: %s", [c["name"] for c in cookies]
        )

    # ══════════════════════════════════════════════════════════════════════
    #  Internal HTTP helpers
    # ══════════════════════════════════════════════════════════════════════

    def _make_headers(
        self, referer: str = "", is_api: bool = False
    ) -> dict:
        """Build request headers matching the reference script."""
        if not referer:
            referer = self.base_url

        base: Dict[str, str] = {
            "user-agent": (
                self._cf_user_agent
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            ),
            "accept-language": "en-US,en;q=0.9",
            "accept-encoding": "gzip, deflate, br",
            "referer": referer,
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", '
            '"Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        if is_api:
            base.update(
                {
                    "accept": "application/json, text/plain, */*",
                    "sec-fetch-dest": "empty",
                    "sec-fetch-mode": "cors",
                    "sec-fetch-site": "same-origin",
                }
            )
        else:
            base.update(
                {
                    "accept": "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "same-origin",
                    "upgrade-insecure-requests": "1",
                }
            )
        return base

    def _cffi_get(
        self,
        url: str,
        *,
        headers: Optional[dict] = None,
        timeout: int = 30,
    ) -> cffi_requests.Response:
        """Perform a GET via the curl_cffi session with retries."""
        self._ensure_solved()
        assert self._cffi_session is not None

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = self._cffi_session.get(
                    url, headers=headers, timeout=timeout
                )
                resp.raise_for_status()
                return resp
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Request error %s (attempt %d/%d): %s",
                    url,
                    attempt,
                    self.retry_attempts,
                    exc,
                )
        raise ProviderError(
            f"Failed to fetch URL after {self.retry_attempts} attempts: {url}"
        ) from last_exc

    def _fetch_soup(
        self,
        url: str,
        referer: str = "",
        not_found_exc: Optional[Type[Exception]] = None,
    ) -> BeautifulSoup:
        """Fetch a page and return parsed HTML."""
        headers = self._make_headers(referer or self.base_url)
        try:
            resp = self._cffi_get(url, headers=headers, timeout=self.timeout)
        except ProviderError:
            if not_found_exc:
                raise not_found_exc(f"Resource not found: {url}")
            raise

        try:
            return BeautifulSoup(resp.text, "lxml")
        except Exception:
            return BeautifulSoup(resp.text, "html.parser")

    # ══════════════════════════════════════════════════════════════════════
    #  Public API — search
    # ══════════════════════════════════════════════════════════════════════

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        logger.debug("Searching MangaKakalot for '%s' (page %s)", query, page)

        if not query.strip():
            return [], False

        search_slug = re.sub(r"\s+", "_", query.strip())
        search_url = f"{self.base_url}/search/story/{search_slug}"
        if page > 1:
            search_url += f"?page={page}"

        soup = self._fetch_soup(search_url)

        results: List[MangaSearchResult] = []
        seen_urls: set[str] = set()

        for item in soup.select("div.story_item"):
            link = item.select_one("h3.story_name a")
            if not link:
                continue

            manga_url = self._ensure_absolute_url(link.get("href", ""))
            if not manga_url or manga_url in seen_urls:
                continue

            seen_urls.add(manga_url)
            title = link.get_text(strip=True)
            cover_el = item.select_one("img")
            cover_url = ""
            if cover_el:
                cover_url = cover_el.get("data-src") or cover_el.get("src") or ""

            manga_id = self._extract_id_from_url(manga_url)
            result = MangaSearchResult(
                provider_id=self.provider_id,
                manga_id=manga_id,
                title=title,
                cover_url=cover_url or "",
                url=manga_url,
            )
            results.append(result)

        has_next = self._has_next_page(soup)
        logger.info(
            "MangaKakalot search returned %d results (has_next=%s)",
            len(results),
            has_next,
        )
        return results, has_next

    # ══════════════════════════════════════════════════════════════════════
    #  Public API — manga info
    # ══════════════════════════════════════════════════════════════════════

    def get_manga_info(
        self, manga_id: Optional[str] = None, url: Optional[str] = None
    ) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        target_url = self._ensure_absolute_url(url or manga_id)
        logger.debug("Fetching MangaKakalot manga info from %s", target_url)

        soup = self._fetch_soup(
            target_url, not_found_exc=MangaNotFoundError
        )

        # ── Title (reference: ul.manga-info-text h1) ──
        title = self._extract_title(soup)
        if not title:
            raise MangaNotFoundError(
                f"Could not extract title for URL: {target_url}"
            )

        extracted_manga_id = self._extract_id_from_url(target_url)

        # ── Cover (reference: div.manga-info-pic img) ──
        cover_url = self._extract_cover_url(soup)

        # ── Alternative titles (reference: h2.story-alternative) ──
        alt_el = soup.select_one(
            "ul.manga-info-text h2.story-alternative, "
            ".story-info-right h2.story-alternative"
        )
        if alt_el:
            raw_alt = alt_el.get_text(strip=True)
            alternative_titles = [
                a.strip()
                for a in re.split(r"[;,/]|\s{2,}", raw_alt)
                if a.strip() and a.strip().lower() != title.lower()
            ]
        else:
            alternative_titles = self._extract_alternative_titles(soup, title)

        # ── Extract fields from ul.manga-info-text li (reference approach) ──
        authors: List[str] = []
        genres: List[str] = []
        status = "Unknown"
        description = ""

        for li in soup.select("ul.manga-info-text li"):
            text = li.get_text(" ", strip=True)
            if "Author" in text:
                authors = [
                    a.get_text(strip=True) for a in li.select("a")
                ] or []
            elif text.startswith("Status"):
                raw_status = text.replace("Status :", "").replace("Status:", "").strip()
                lower_s = raw_status.lower()
                if "ongoing" in lower_s:
                    status = "Ongoing"
                elif "completed" in lower_s:
                    status = "Completed"
                elif "hiatus" in lower_s:
                    status = "Hiatus"
                else:
                    status = raw_status or "Unknown"
            elif text.startswith("Genres") or text.startswith("Genre"):
                genres = [
                    a.get_text(strip=True) for a in li.select("a")
                ]

        # Fallback to generic detail extraction if the old-style selectors
        # didn't find anything (new-style layout).
        if not authors:
            authors = self._extract_person_list(soup, ["Author", "Authors"])
        if not genres:
            genres = self._extract_genres(soup)
        if status == "Unknown":
            status = self._extract_status(soup)

        artists = self._extract_person_list(
            soup, ["Artist", "Artists"]
        ) or authors

        # ── Description ──
        description = self._extract_description(soup)

        # ── Year ──
        year = self._extract_year(soup)

        manga_info = MangaInfo(
            provider_id=self.provider_id,
            manga_id=manga_id or extracted_manga_id,
            title=title,
            alternative_titles=alternative_titles,
            cover_url=cover_url,
            url=target_url,
            description=description,
            authors=authors,
            artists=artists,
            genres=genres,
            status=status,
            year=year,
        )

        logger.info("Fetched MangaKakalot manga info for '%s'", title)
        return manga_info

    # ══════════════════════════════════════════════════════════════════════
    #  Public API — chapters (API-based, from reference script)
    # ══════════════════════════════════════════════════════════════════════

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        if not manga_id:
            raise ValueError("manga_id is required")

        # Extract the slug from manga_id (could be a full URL or path)
        slug = self._extract_slug(manga_id)
        chapters_api = f"{self.base_url}/api/manga/{slug}/chapters?limit=-1"
        manga_page_url = f"{self.base_url}/manga/{slug}"

        logger.debug("Fetching MangaKakalot chapters API: %s", chapters_api)

        headers = self._make_headers(manga_page_url, is_api=True)
        resp = self._cffi_get(chapters_api, headers=headers, timeout=self.timeout)

        raw = resp.json()

        # Primary shape: {"success": true, "data": {"chapters": [...]}}
        chapter_list: list = []
        try:
            chapter_list = raw["data"]["chapters"]
        except (KeyError, TypeError):
            # Fallback: walk the response tree to find any list
            chapter_list = self._find_list(raw) or []

        logger.info("MangaKakalot API returned %d chapters", len(chapter_list))

        chapters: List[Chapter] = []
        for ch_data in chapter_list:
            ch_slug = ch_data.get("chapter_slug", "")
            ch_name = ch_data.get("chapter_name", ch_slug)
            chapter_url = f"{manga_page_url}/{ch_slug}"

            chapter_number = self._extract_chapter_number(ch_name)
            volume = self._extract_volume(ch_name)
            release_date = ch_data.get("updated_at", "")
            if release_date and len(release_date) > 10:
                release_date = release_date[:10]

            chapter = Chapter(
                chapter_id=self._extract_id_from_url(chapter_url),
                manga_id=manga_id,
                title=ch_name,
                chapter_number=chapter_number,
                volume=volume,
                url=chapter_url,
                release_date=release_date,
                language="en",
            )
            chapters.append(chapter)

        chapters.sort(key=lambda c: c.sort_key)
        logger.info(
            "Found %d MangaKakalot chapters for %s", len(chapters), manga_id
        )
        return chapters

    # ══════════════════════════════════════════════════════════════════════
    #  Public API — chapter images (HTML scraping via curl_cffi)
    # ══════════════════════════════════════════════════════════════════════

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        if not chapter_id:
            raise ValueError("chapter_id is required")

        chapter_url = self._ensure_absolute_url(chapter_id)
        logger.debug(
            "Fetching MangaKakalot chapter images from %s", chapter_url
        )

        # Derive the manga page URL for the referer
        parsed = urlparse(chapter_url)
        path_parts = parsed.path.strip("/").split("/")
        # path = /manga/<slug>/<chapter-slug> → manga URL = /manga/<slug>
        if len(path_parts) >= 2:
            manga_referer = f"{self.base_url}/{path_parts[0]}/{path_parts[1]}"
        else:
            manga_referer = self.base_url

        soup = self._fetch_soup(chapter_url, referer=manga_referer)

        # Try multiple selectors (same as reference script)
        selectors = [
            "div.container-chapter-reader img",
            "div.chapter-content img",
            "div.reader-content img",
            "div#chapter-content img",
            "div.pages-chapter-reader img",
            "div.vung-doc img",
            "div[class*='chapter'] img",
        ]

        images: List[str] = []
        for sel in selectors:
            imgs = soup.select(sel)
            if imgs:
                logger.debug("Selector '%s' → %d images", sel, len(imgs))
                images = [
                    img.get("src")
                    or img.get("data-src")
                    or img.get("data-lazy-src")
                    for img in imgs
                ]
                images = [
                    url for url in images if url and url.startswith("http")
                ]
                break

        # Fallback: scan all <img> tags for CDN-like URLs
        if not images:
            all_imgs = soup.find_all("img")
            images = [
                img.get("src") or img.get("data-src") or ""
                for img in all_imgs
                if any(
                    kw in (img.get("src") or img.get("data-src") or "")
                    for kw in ["cdn", "storage", "img", "/chapter"]
                )
            ]
            images = [u for u in images if u.startswith("http")]
            logger.debug("Fallback img scan → %d images", len(images))

        logger.info(
            "Extracted %d image URLs from chapter %s", len(images), chapter_id
        )
        return images

    # ══════════════════════════════════════════════════════════════════════
    #  Public API — image download (CDN-specific headers)
    # ══════════════════════════════════════════════════════════════════════

    def download_image(self, url: str) -> bytes:
        """Download a single image with MangaKakalot CDN headers.

        The CDN (img-r1.2xstorage.com) uses simple referer-based
        hotlink protection – no FlareSolverr solve needed.
        """
        self._ensure_solved()
        assert self._cffi_session is not None

        headers = {
            "Referer": "https://www.mangakakalot.gg/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        try:
            logger.debug("Downloading MangaKakalot image: %s", url)
            resp = self._cffi_session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.error("Failed to download image %s: %s", url, e)
            raise ProviderError(f"Failed to download image: {e}") from e

    # ══════════════════════════════════════════════════════════════════════
    #  Private helpers
    # ══════════════════════════════════════════════════════════════════════

    def _ensure_absolute_url(self, url_or_path: Optional[str]) -> str:
        if not url_or_path:
            return self.base_url
        if url_or_path.startswith("http://") or url_or_path.startswith(
            "https://"
        ):
            return url_or_path
        return urljoin(f"{self.base_url}/", url_or_path.lstrip("/"))

    def _extract_id_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")
        return path or parsed.netloc

    def _extract_slug(self, manga_id: str) -> str:
        """Extract the manga slug from a manga_id (which may be a URL or path).

        Examples:
            'https://www.mangakakalot.gg/manga/some-slug' → 'some-slug'
            'manga/some-slug'  → 'some-slug'
            'some-slug'        → 'some-slug'
        """
        if manga_id.startswith("http"):
            parsed = urlparse(manga_id)
            parts = parsed.path.strip("/").split("/")
            # /manga/<slug> → slug
            if len(parts) >= 2 and parts[0] == "manga":
                return parts[1]
            return parts[-1]

        parts = manga_id.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "manga":
            return parts[1]
        return parts[-1]

    @staticmethod
    def _find_list(obj: object) -> Optional[list]:
        """Walk a nested dict/list to find the first non-empty list."""
        if isinstance(obj, list) and obj:
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                result = MangakakalotProvider._find_list(v)
                if result is not None:
                    return result
        return None

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        selectors = [
            "ul.manga-info-text h1",
            ".manga-info-content h1",
            ".story-info-right h1",
            ".story-info-right h1.title",
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        el = soup.find("h1")
        return el.get_text(strip=True) if el else None

    def _extract_cover_url(self, soup: BeautifulSoup) -> str:
        selectors = [
            "div.manga-info-pic img",
            ".manga-info-pic img",
            ".story-info-left img",
            ".manga-info-img img",
        ]
        for sel in selectors:
            cover = soup.select_one(sel)
            if cover:
                return cover.get("data-src") or cover.get("src") or ""
        return ""

    def _extract_alternative_titles(
        self, soup: BeautifulSoup, main_title: str
    ) -> List[str]:
        detail_text = self._extract_detail_text(
            soup, ["Alternative", "Other name", "Alternative name"]
        )
        alternatives: List[str] = []
        if detail_text:
            for raw in re.split(r"[;,/]|\s{2,}", detail_text):
                alt = raw.strip()
                if alt and alt.lower() != main_title.lower():
                    alternatives.append(alt)
        return alternatives

    def _extract_description(self, soup: BeautifulSoup) -> str:
        container = soup.select_one(
            "#panel-story-info-description .panel-body, "
            "#panel-story-info-description, "
            ".panel-story-info-description, "
            ".story-info-right .description"
        )
        if container:
            paragraphs = [
                p.get_text(" ", strip=True)
                for p in container.select("p")
                if p.get_text(strip=True)
            ]
            if paragraphs:
                return "\n\n".join(paragraphs)
            return container.get_text(" ", strip=True)
        return ""

    def _extract_person_list(
        self, soup: BeautifulSoup, labels: List[str]
    ) -> List[str]:
        detail_element = self._extract_detail_element(soup, labels)
        if not detail_element:
            return []

        values = [
            a.get_text(strip=True)
            for a in detail_element.find_all("a")
            if a.get_text(strip=True)
        ]
        if values:
            return values

        text = detail_element.get_text(" ", strip=True)
        for label in labels:
            if text.lower().startswith(label.lower()):
                parts = text.split(":", 1)
                if len(parts) == 2:
                    text = parts[1].strip()
                break

        return [
            v.strip()
            for v in re.split(r"[;,/]|\s{2,}", text)
            if v.strip()
        ]

    def _extract_genres(self, soup: BeautifulSoup) -> List[str]:
        detail_element = self._extract_detail_element(
            soup, ["Genre", "Genres"]
        )
        if detail_element:
            genres = [
                a.get_text(strip=True)
                for a in detail_element.find_all("a")
                if a.get_text(strip=True)
            ]
            if genres:
                return genres
            text = detail_element.get_text(" ", strip=True)
            if ":" in text:
                text = text.split(":", 1)[1]
            return [g.strip() for g in text.split(",") if g.strip()]
        return []

    def _extract_status(self, soup: BeautifulSoup) -> str:
        status_text = self._extract_detail_text(soup, ["Status"])
        if status_text:
            lower = status_text.lower()
            if "ongoing" in lower:
                return "Ongoing"
            if "completed" in lower:
                return "Completed"
            if "hiatus" in lower:
                return "Hiatus"
        return "Unknown"

    def _extract_year(self, soup: BeautifulSoup) -> Optional[int]:
        release_text = self._extract_detail_text(
            soup, ["Released", "Release", "Year"]
        )
        if release_text:
            match = re.search(r"(19|20)\d{2}", release_text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    return None
        return None

    def _extract_detail_text(
        self, soup: BeautifulSoup, labels: List[str]
    ) -> str:
        el = self._extract_detail_element(soup, labels)
        if not el:
            return ""
        text = el.get_text(" ", strip=True)
        for label in labels:
            if text.lower().startswith(label.lower()):
                parts = text.split(":", 1)
                if len(parts) == 2:
                    return parts[1].strip()
        return text.strip()

    def _extract_detail_element(
        self, soup: BeautifulSoup, labels: List[str]
    ):
        # Try new-style layout first
        info_section = soup.select_one(".story-info-right")
        if info_section:
            title_elements = info_section.select(".story-info-right-title")
            detail_elements = info_section.select(".story-info-right-detail")
            if len(title_elements) == len(detail_elements):
                for title_el, detail_el in zip(title_elements, detail_elements):
                    label_text = (
                        title_el.get_text(" ", strip=True).rstrip(":").lower()
                    )
                    for label in labels:
                        if label.lower() in label_text:
                            return detail_el

        # Fallback: old-style layout
        fallback = soup.select_one(
            ".manga-info-text, .manga-info-content, ul.manga-info-text"
        )
        if fallback:
            for element in fallback.find_all(
                ["li", "p", "span", "div"], recursive=True
            ):
                text = element.get_text(" ", strip=True)
                if not text:
                    continue
                lower_text = text.lower()
                for label in labels:
                    ll = label.lower()
                    if lower_text.startswith(ll) or f"{ll}:" in lower_text:
                        return element
        return None

    def _extract_chapter_number(self, chapter_title: str) -> str:
        match = re.search(
            r"(?:chapter|ch\.?|cap\.)\s*(\d+(?:\.\d+)?)",
            chapter_title,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
        match = re.search(r"(\d+(?:\.\d+)?)", chapter_title)
        if match:
            return match.group(1)
        return chapter_title.strip()

    def _extract_volume(self, chapter_title: str) -> Optional[str]:
        match = re.search(
            r"vol(?:ume)?\.?\s*(\d+)", chapter_title, re.IGNORECASE
        )
        return match.group(1) if match else None

    def _has_next_page(
        self, soup: BeautifulSoup, current_page: int = 1
    ) -> bool:
        pagination_selectors = [
            ".pagination .next",
            ".pagination a[href*='page']",
            "a[href*='?page=']",
            ".page-nav .next",
            ".pager .next",
        ]
        for selector in pagination_selectors:
            if soup.select_one(selector):
                return True

        page_links = soup.select(
            ".pagination a, .page-nav a, a[href*='page=']"
        )
        for link in page_links:
            href = link.get("href", "")
            if "?page=" in href or "/page/" in href:
                page_match = re.search(r"[?&]page=(\d+)", href)
                if page_match:
                    try:
                        page_num = int(page_match.group(1))
                        if page_num > current_page:
                            return True
                    except ValueError:
                        continue

        for anchor in soup.select("a"):
            text = anchor.get_text(strip=True).lower()
            if text in {"next", ">", ">>", "more", "next page"}:
                return True

        result_items = soup.select("div.story_item")
        return len(result_items) > 0

    # Override get_headers so base class doesn't break on import
    def get_headers(self) -> dict:
        headers = super().get_headers()
        user_agent = self.config.get("network.user_agent")
        if user_agent:
            headers["User-Agent"] = user_agent
        headers.setdefault("Referer", self.base_url)
        return headers
