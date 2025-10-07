import logging
import re
from typing import List, Tuple, Optional
from urllib.parse import urljoin, urlparse, quote_plus

import httpx
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import (
        sync_playwright,
        TimeoutError as PlaywrightTimeoutError,
        Error as PlaywrightError,
    )
except ImportError:  # pragma: no cover - playwright might not be installed yet
    sync_playwright = None  # type: ignore
    PlaywrightTimeoutError = PlaywrightError = Exception  # type: ignore

from core.base_provider import (
    BaseProvider,
    ProviderError,
    MangaNotFoundError,
    ChapterNotFoundError,
)
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter


logger = logging.getLogger(__name__)


class AsuraComicProvider(BaseProvider):
    """Provider implementation for AsuraComic."""

    provider_id = "asuracomic"
    provider_name = "AsuraComic"
    base_url = "https://asuracomic.net"

    def __init__(self) -> None:
        self.config = Config()
        self._page_timeout_ms = max(int(self.config.network_timeout * 1000), 30000)
        super().__init__()

    def get_headers(self) -> dict:
        user_agent = self.config.get(
            "network.user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        )
        return {
            "User-Agent": user_agent,
            "Referer": self.base_url,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        query = query.strip()
        if not query:
            return [], False

        if sync_playwright is None:
            raise ProviderError("Playwright is required for AsuraComic search")

        search_url = f"{self.base_url}/series?page={max(page, 1)}&name={quote_plus(query)}"
        results: List[MangaSearchResult] = []
        has_next_page = False

        logger.debug("Searching AsuraComic: %s", search_url)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page_obj = browser.new_page()
            try:
                page_obj.goto(search_url, wait_until="networkidle", timeout=self._page_timeout_ms)
                cards = page_obj.query_selector_all("a[href^='series/']")
                seen_urls = set()

                for card in cards:
                    href = card.get_attribute("href")
                    if not href:
                        continue

                    manga_url = self._normalize_url(href)
                    if manga_url in seen_urls:
                        continue
                    seen_urls.add(manga_url)

                    title_el = card.query_selector("span.block.font-bold")
                    title = title_el.inner_text().strip() if title_el else card.inner_text().strip()
                    manga_id = self._extract_manga_id_from_url(manga_url)
                    results.append(
                        MangaSearchResult(
                            provider_id=self.provider_id,
                            manga_id=manga_id,
                            title=title,
                            cover_url="",
                            url=manga_url,
                        )
                    )

                has_next_page = self._has_next_search_page(page_obj, page)
            except (PlaywrightTimeoutError, PlaywrightError) as exc:
                logger.error("Playwright error during search: %s", exc)
                raise ProviderError(f"Search failed: {exc}") from exc
            finally:
                page_obj.close()
                browser.close()

        return results, has_next_page

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        target_url = url or self._build_manga_url(manga_id)
        soup = self._get_soup(target_url)

        title = self._extract_title(soup)
        alt_titles = []  # Remove alternative titles as requested
        cover_url = self._extract_cover_url(soup, target_url)
        description = self._extract_description_new(soup)
        authors = self._extract_authors(soup)
        artists = self._extract_artists(soup)
        genres = self._extract_genres(soup)
        status = self._extract_status_new(soup)
        year = self._extract_year(soup)

        manga_id = manga_id or self._extract_manga_id_from_url(target_url)

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=manga_id,
            title=title,
            alternative_titles=alt_titles,
            cover_url=cover_url,
            url=target_url,
            description=description,
            authors=authors,
            artists=artists,
            genres=genres,
            status=status,
            year=year,
        )

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        if not manga_id:
            raise ValueError("manga_id is required")

        target_url = self._build_manga_url(manga_id)
        soup = self._get_soup(target_url)

        chapter_elements = soup.select("div.pl-4.py-2.border.rounded-md.group.w-full.hover\\:bg-\\[\\#343434\\].cursor-pointer.border-\\[\\#A2A2A2\\]\\/20.relative")
        if not chapter_elements:
            raise ChapterNotFoundError("No chapters found")

        chapters: List[Chapter] = []
        for element in chapter_elements:
            link = element.find("a", href=True)
            if not link:
                continue

            chapter_url = self._normalize_url(link["href"])
            title_h3 = element.select_one("h3.text-sm.text-white.font-medium")
            title = self._clean_text(title_h3.get_text()) if title_h3 else self._clean_text(link.get_text())
            chapter_id = chapter_url
            chapter_number = self._extract_chapter_number(title)
            volume = self._extract_volume(title)
            release_date = self._extract_chapter_date_new(element)

            chapters.append(
                Chapter(
                    chapter_id=chapter_id,
                    manga_id=manga_id,
                    title=title,
                    chapter_number=chapter_number,
                    volume=volume,
                    url=chapter_url,
                    release_date=release_date,
                )
            )

        chapters.sort(key=lambda chapter: chapter.sort_key)
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        if sync_playwright is None:
            raise ProviderError("Playwright is required for AsuraComic chapter images")

        chapter_url = self._normalize_url(chapter_id)
        logger.debug("Fetching AsuraComic chapter images: %s", chapter_url)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page_obj = browser.new_page()
            try:
                page_obj.goto(chapter_url, wait_until="networkidle", timeout=self._page_timeout_ms)
                page_obj.wait_for_selector("img.object-cover.mx-auto", timeout=10000)
                image_urls = page_obj.eval_on_selector_all(
                    "img.object-cover.mx-auto",
                    "elements => elements.map(el => el.src)",
                )
                return [self._normalize_url(url) for url in image_urls if url]
            except (PlaywrightTimeoutError, PlaywrightError) as exc:
                logger.error("Failed to fetch images for %s: %s", chapter_url, exc)
                raise ProviderError(f"Failed to fetch chapter images: {exc}") from exc
            finally:
                page_obj.close()
                browser.close()

    # Helper methods

    def _get_soup(self, url: str) -> BeautifulSoup:
        try:
            response = self.session.get(url)
            if response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found at {url}")
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found at {url}") from exc
            raise ProviderError(f"HTTP error when requesting {url}: {exc}") from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Request failed for {url}: {exc}") from exc

    def _has_next_search_page(self, page_obj, current_page: int) -> bool:
        try:
            selector = f"a[href*='page={current_page + 1}']"
            handles = page_obj.query_selector_all(selector)
            return bool(handles)
        except Exception:
            return False

    def _extract_manga_id_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if "series" in parts:
            idx = parts.index("series")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return parts[-1] if parts else url

    def _build_manga_url(self, manga_id: Optional[str]) -> str:
        if not manga_id:
            return self.base_url
        if manga_id.startswith("http"):
            return manga_id
        return urljoin(self.base_url + "/", f"series/{manga_id}")

    def _extract_title(self, soup: BeautifulSoup) -> str:
        title_element = soup.select_one("div.text-center.sm\\:text-left span.text-xl.font-bold")
        if title_element:
            return self._clean_text(title_element.get_text())
        raise ProviderError("Unable to determine manga title")



    def _extract_cover_url(self, soup: BeautifulSoup, base: str) -> str:
        candidates = [
            soup.select_one("img[alt*='cover']"),
            soup.select_one("img.rounded"),
            soup.select_one("div img"),
        ]
        for candidate in candidates:
            if candidate and candidate.has_attr("src"):
                return self._normalize_url(candidate["src"], base)
        return ""

    def _extract_authors(self, soup: BeautifulSoup) -> List[str]:
        # Find all h3 elements with the specific color class
        h3_elements = soup.select("h3.text-sm.text-\\[\\#A2A2A2\\]")
        for h3 in h3_elements:
            # Check if this h3 contains "Author" in its parent structure
            parent_div = h3.find_parent("div")
            if parent_div:
                author_label = parent_div.select_one("h3.text-\\[\\#D9D9D9\\]")
                if author_label and "Author" in author_label.get_text():
                    return [self._clean_text(h3.get_text())]
        return []

    def _extract_artists(self, soup: BeautifulSoup) -> List[str]:
        # Find all h3 elements with the specific color class
        h3_elements = soup.select("h3.text-sm.text-\\[\\#A2A2A2\\]")
        for h3 in h3_elements:
            # Check if this h3 contains "Artist" in its parent structure
            parent_div = h3.find_parent("div")
            if parent_div:
                artist_label = parent_div.select_one("h3.text-\\[\\#D9D9D9\\]")
                if artist_label and "Artist" in artist_label.get_text():
                    return [self._clean_text(h3.get_text())]
        return []

    def _extract_genres(self, soup: BeautifulSoup) -> List[str]:
        genres = []
        genre_buttons = soup.select("div:has(h3:contains('Genres')) button.bg-\\[\\#343434\\]")
        for button in genre_buttons:
            genre_text = self._clean_text(button.get_text())
            if genre_text:
                genres.append(genre_text)
        return genres

    def _extract_status(self, soup: BeautifulSoup) -> str:
        status_element = soup.select_one("div:has(h3:contains('Status')) h3.text-sm.text-\\[\\#A2A2A2\\].capitalize")
        if status_element:
            return self._clean_text(status_element.get_text()).capitalize()
        return "Unknown"

    def _extract_description_new(self, soup: BeautifulSoup) -> str:
        desc_element = soup.select_one("span.font-medium.text-sm.text-\\[\\#A2A2A2\\] p")
        if desc_element:
            return self._clean_text(desc_element.get_text())
        return ""

    def _extract_status_new(self, soup: BeautifulSoup) -> str:
        status_element = soup.select_one("div:has(h3:contains('Status')) h3.text-sm.text-\\[\\#A2A2A2\\].capitalize")
        if status_element:
            return self._clean_text(status_element.get_text()).capitalize()
        return "Unknown"

    def _extract_metadata_list(self, soup: BeautifulSoup, labels: List[str]) -> List[str]:
        for label in labels:
            node = soup.find(
                lambda tag: tag
                and tag.name in {"span", "div", "dt", "th"}
                and label in tag.get_text(strip=True).lower()
            )
            if not node:
                continue
            container = node.parent if node.parent else node
            links = [self._clean_text(link.get_text()) for link in container.find_all("a")]
            if links:
                return [item for item in links if item]
            text = self._clean_text(container.get_text())
            text = re.sub(rf"(?i){label}\s*[:\uFF1A]?", "", text)
            items = [item.strip() for item in re.split(r",|/|;|\n", text) if item.strip()]
            if items:
                return items
        return []


    def _extract_year(self, soup: BeautifulSoup) -> Optional[int]:
        text_sources = self._extract_metadata_list(soup, ["year", "released", "published"])
        for text in text_sources:
            match = re.search(r"(\d{4})", text)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return None

    def _extract_chapter_number(self, title: str) -> str:
        match = re.search(r"(?:chapter|ch\.?)(\s*|\s*[:\-]?\s*)(\d+(?:\.\d+)?)", title, re.IGNORECASE)
        if match:
            return match.group(2)
        match = re.search(r"(\d+(?:\.\d+)?)", title)
        if match:
            return match.group(1)
        return title

    def _extract_volume(self, title: str) -> Optional[str]:
        match = re.search(r"vol(?:ume)?\.?\s*(\d+)", title, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _extract_chapter_date_new(self, element) -> Optional[str]:
        date_element = element.select_one("h3.text-xs.text-\\[\\#A2A2A2\\]")
        if date_element:
            text = self._clean_text(date_element.get_text())
            if text and any(keyword in text.lower() for keyword in ["ago", "202", "201", "yesterday", "today"]):
                return text
        return None

    def _normalize_url(self, url: str, base: Optional[str] = None) -> str:
        if not url:
            return ""
        candidate_base = base or self.base_url
        return urljoin(candidate_base + "/", url)

    @staticmethod
    def _clean_text(text: str) -> str:
        return text.strip() if text else ""
