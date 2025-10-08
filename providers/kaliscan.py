"""KaliScan provider implementation for MangaForge."""

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from core.base_provider import (
    BaseProvider,
    ProviderError,
    MangaNotFoundError,
    ChapterNotFoundError,
)
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)


class KaliscanProvider(BaseProvider):
    provider_id = "kaliscan"
    provider_name = "KaliScan"
    base_url = "https://kaliscan.io"

    _CHAPTER_NUMBER_PATTERNS = (
        re.compile(r"(?i)(?:chapter|ch\.)\s*([0-9]+(?:\.[0-9]+)?)"),
        re.compile(r"([0-9]+(?:\.[0-9]+)?)"),
    )

    def __init__(self) -> None:
        self.config = Config()
        self.timeout = float(self.config.get("network.timeout", 30) or 30)
        self.retry_attempts = int(self.config.get("network.retry_attempts", 3) or 3)
        super().__init__()
        self.session.timeout = httpx.Timeout(self.timeout)
        self.session.headers.setdefault("Cache-Control", "no-cache")

    def get_headers(self) -> Dict[str, str]:
        headers = super().get_headers()
        user_agent = self.config.get("network.user_agent", headers.get("User-Agent"))
        if user_agent:
            headers["User-Agent"] = user_agent
        headers["Referer"] = self.base_url
        headers["Accept"] = (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        )
        headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        return headers

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        logger.debug("Searching Kaliscan for '%s' (page %s)", query, page)
        params = {"q": query, "page": page}
        try:
            response = self._get(f"{self.base_url}/search", params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return [], False
            raise ProviderError(f"Search failed: {exc}") from exc
        soup = self._parse_html(response.text)
        results: List[MangaSearchResult] = []
        for item in soup.select("div.book-item"):
            result = self._parse_search_item(item)
            if result:
                results.append(result)
        has_next = self._has_next_page(soup, page)
        logger.info("Kaliscan search returned %s results (next=%s)", len(results), has_next)
        return results, has_next

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")
        target_url: str
        if url:
            target_url = url
            manga_id = self._extract_manga_id(url)
        else:
            target_url = urljoin(self.base_url, f"/manga/{manga_id}")
        logger.debug("Fetching Kaliscan manga info from %s", target_url)
        try:
            response = self._get(target_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {manga_id}") from exc
            raise ProviderError(f"Failed to fetch manga page: {exc}") from exc
        soup = self._parse_html(response.text)
        title = self._extract_title(soup)
        if not title:
            raise MangaNotFoundError(f"Unable to extract title for {manga_id}")
        cover_url = self._extract_cover(soup, target_url)
        authors = self._extract_authors(soup)
        genres = self._extract_genres(soup)
        status = self._extract_status(soup) or "Unknown"
        description = self._extract_description(soup)
        year = self._extract_year(soup)
        manga_info = MangaInfo(
            provider_id=self.provider_id,
            manga_id=manga_id or "",
            title=title,
            alternative_titles=[],
            cover_url=cover_url,
            url=target_url,
            description=description,
            authors=authors,
            artists=[],
            genres=genres,
            status=status,
            year=year,
        )
        return manga_info

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        logger.debug("Fetching Kaliscan chapters for %s", manga_id)
        manga_url = urljoin(self.base_url, f"/manga/{manga_id}")
        try:
            response = self._get(manga_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {manga_id}") from exc
            raise ProviderError(f"Failed to fetch manga page: {exc}") from exc
        soup = self._parse_html(response.text)
        items = soup.select("div#chapter-list-inner ul.chapter-list li")
        if not items:
            logger.warning("No chapters found in static list, attempting AJAX endpoint")
            items = self._fetch_chapter_list_via_api(manga_id, manga_url)
        chapters: List[Chapter] = []
        for item in items:
            chapter = self._parse_chapter_item(item, manga_id)
            if chapter:
                chapters.append(chapter)
        chapters.sort(key=self._chapter_sort_key)
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        chapter_url = self._normalise_chapter_url(chapter_id)
        logger.debug("Fetching Kaliscan chapter images using Playwright for %s", chapter_url)

        # Use the same approach as MangaKakalot for handling asyncio event loops
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.warning("Asyncio event loop already running, cannot use Playwright")
                return []
            else:
                return loop.run_until_complete(self._extract_images_playwright(chapter_url))
        except RuntimeError:
            return asyncio.run(self._extract_images_playwright(chapter_url))

    def _fetch_chapter_images_from_server(
        self, chapter_numeric_id: str, server_id: str, chapter_url: str
    ) -> List[str]:
        params = {"server_id": server_id, "chapter_id": chapter_numeric_id}
        response = self._get(
            urljoin(self.base_url, "/service/backend/chapterServer/"),
            params=params,
            referer=chapter_url,
            extra_headers={"X-Requested-With": "XMLHttpRequest"},
        )
        snippet = response.text
        if "chapter-image" not in snippet:
            raise ProviderError(
                f"Chapter server {server_id} returned unexpected payload"
            )
        soup = self._parse_html(snippet)
        image_urls: List[str] = []
        for container in soup.select("div.chapter-image"):
            url = container.get("data-src")
            if not url:
                img = container.find("img")
                if img:
                    url = img.get("data-src") or img.get("src")
            if url:
                image_urls.append(urljoin(self.base_url, url))
        return image_urls

    async def _extract_images_playwright(self, chapter_url: str) -> List[str]:
        """Extract image URLs using Playwright to handle dynamic loading."""
        logger.debug("Using Playwright to extract images from %s", chapter_url)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.config.get("network.user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
                viewport={"width": 1280, "height": 800}
            )

            page = await context.new_page()

            try:
                # Navigate to chapter URL
                await page.goto(chapter_url, wait_until="domcontentloaded", timeout=60000)

                # Handle the warning accept button if it appears (KaliScan specific)
                try:
                    await page.wait_for_selector("button.btn.btn-warning", timeout=10000)
                    await page.click("button.btn.btn-warning")
                    await page.wait_for_load_state("networkidle")
                    logger.info("Clicked Accept button on KaliScan chapter page")
                except Exception:
                    logger.debug("No warning button found on KaliScan chapter page, continuing...")

                # Wait for chapter images to be present and ensure they have started loading
                try:
                    await page.wait_for_function("""
                        () => {
                            const images = document.querySelectorAll('div.chapter-image img');
                            return Array.from(images).some(img => img.src && img.src.length > 0);
                        }
                    """, timeout=20000)
                    logger.debug("At least one chapter image has a non-empty src attribute.")
                except Exception:
                    logger.warning("Timed out waiting for images to have a src attribute. Scraping may fail.")

                # Extract image URLs from div.chapter-image elements
                image_divs = await page.query_selector_all("div.chapter-image")
                if not image_divs:
                    raise ProviderError(f"Unable to locate page images for chapter {chapter_url}")

                image_urls: List[str] = []
                for i, div in enumerate(image_divs, start=1):
                    # Try data-src first (lazy loading)
                    img_url = await div.get_attribute("data-src")
                    if not img_url:
                        # Fallback to src attribute
                        img_tag = await div.query_selector("img")
                        if img_tag:
                            img_url = await img_tag.get_attribute("src")

                    if img_url:
                        # Ensure absolute URL
                        if not img_url.startswith("http"):
                            img_url = urljoin(self.base_url, img_url)
                        image_urls.append(img_url)
                    else:
                        logger.warning("Could not extract image URL for page %d in chapter %s", i, chapter_url)

                logger.info("Extracted %d image URLs from KaliScan chapter", len(image_urls))
                return image_urls

            finally:
                await page.close()
                await context.close()
                await browser.close()

    def _fetch_chapter_list_via_api(
        self, manga_id: str, referer: str
    ) -> List[BeautifulSoup]:
        params = {"manga_id": manga_id.split("-")[0] if manga_id else manga_id}
        response = self._get(
            urljoin(self.base_url, "/service/backend/chapterList/"),
            params=params,
            referer=referer,
            extra_headers={"X-Requested-With": "XMLHttpRequest"},
        )
        soup = self._parse_html(response.text)
        return soup.select("li")

    def _parse_search_item(self, item: BeautifulSoup) -> Optional[MangaSearchResult]:
        title_node = item.select_one("div.title h3 a") or item.select_one("h3 a")
        if not title_node or not title_node.get("href"):
            return None
        title = title_node.get_text(" ", strip=True)
        url = urljoin(self.base_url, title_node["href"])
        manga_id = self._extract_manga_id(url)
        cover_node = item.select_one("div.thumb img")
        cover_url = ""
        if cover_node:
            cover_url = cover_node.get("data-src") or cover_node.get("src") or ""
            cover_url = urljoin(self.base_url, cover_url)
        return MangaSearchResult(
            provider_id=self.provider_id,
            manga_id=manga_id,
            title=title,
            cover_url=cover_url,
            url=url,
        )

    def _has_next_page(self, soup: BeautifulSoup, page: int) -> bool:
        next_marker = f"page={page + 1}"
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if next_marker in href:
                return True
        return False

    def _parse_chapter_item(self, item: BeautifulSoup, manga_id: str) -> Optional[Chapter]:
        anchor = item.find("a", href=True)
        if not anchor:
            return None
        chapter_url = urljoin(self.base_url, anchor["href"])
        title_text = anchor.get_text(" ", strip=True)
        chapter_number = self._extract_chapter_number(title_text) or title_text
        time_node = anchor.find("time", class_="chapter-update")
        release_date = time_node.get_text(strip=True) if time_node else None
        slug = self._extract_chapter_id_from_url(anchor["href"])
        return Chapter(
            chapter_id=slug,
            manga_id=manga_id,
            title=title_text,
            chapter_number=str(chapter_number),
            volume=None,
            url=chapter_url,
            release_date=release_date,
        )

    def _extract_chapter_number(self, text: str) -> Optional[str]:
        if not text:
            return None
        for pattern in self._CHAPTER_NUMBER_PATTERNS:
            match = pattern.search(text)
            if match:
                value = match.group(1)
                try:
                    number = float(value)
                    if number.is_integer():
                        return str(int(number))
                    return str(number)
                except ValueError:
                    cleaned = value.strip()
                    return cleaned or None
        return None

    def _chapter_sort_key(self, chapter: Chapter) -> Tuple[int, float, str]:
        try:
            number = float(chapter.chapter_number)
            return (0, number, chapter.title)
        except ValueError:
            return (1, float("inf"), chapter.title)

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        node = soup.select_one("div.book-info div.detail div.name h1")
        if node:
            text = node.get_text(strip=True)
            if text:
                return text
        title_node = soup.find("title")
        if title_node:
            return title_node.get_text(strip=True)
        return None

    def _extract_cover(self, soup: BeautifulSoup, base_url: str) -> str:
        node = soup.select_one("div.img-cover img")
        if node:
            src = node.get("data-src") or node.get("src")
            if src:
                return urljoin(base_url, src)
        meta = soup.select_one("meta[property='og:image']")
        if meta and meta.get("content"):
            return meta["content"].strip()
        return ""

    def _extract_authors(self, soup: BeautifulSoup) -> List[str]:
        block = self._find_meta_block(soup, "Authors")
        if not block:
            return []
        authors = [a.get_text(strip=True) for a in block.select("a") if a.get_text(strip=True)]
        return authors

    def _extract_genres(self, soup: BeautifulSoup) -> List[str]:
        block = self._find_meta_block(soup, "Genres")
        if not block:
            return []
        genres: List[str] = []
        for a in block.select("a"):
            text = a.get_text(strip=True).rstrip(",")
            if text:
                genres.append(text)
        seen = set()
        deduped: List[str] = []
        for genre in genres:
            key = genre.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(genre)
        return deduped

    def _extract_status(self, soup: BeautifulSoup) -> Optional[str]:
        block = self._find_meta_block(soup, "Status")
        if not block:
            return None
        span = block.find("span")
        if span and span.get_text(strip=True):
            return span.get_text(strip=True)
        anchor = block.find("a")
        if anchor and anchor.get_text(strip=True):
            return anchor.get_text(strip=True)
        return None

    def _extract_description(self, soup: BeautifulSoup) -> str:
        node = soup.select_one("div.summary p.content")
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
        summary = soup.select_one("div.summary")
        if summary:
            text = summary.get_text(" ", strip=True)
            text = text.replace("SHOW MORE", "").strip()
            if text:
                return text
        return "No description available."

    def _extract_year(self, soup: BeautifulSoup) -> Optional[int]:
        for label in ("Published", "Released", "Year"):
            block = self._find_meta_block(soup, label)
            if not block:
                continue
            text = block.get_text(" ", strip=True)
            match = re.search(r"(19|20)\\d{2}", text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    continue
        return None

    def _find_meta_block(self, soup: BeautifulSoup, label: str) -> Optional[BeautifulSoup]:
        for paragraph in soup.select("div.book-info div.meta p"):
            strong = paragraph.find("strong")
            if strong and label.lower() in strong.get_text(strip=True).lower():
                return paragraph
        return None

    def _parse_html(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def _extract_manga_id(self, url: str) -> str:
        path = urlparse(url).path
        if not path:
            return ""
        cleaned = path.lstrip("/")
        if cleaned.startswith("manga/"):
            cleaned = cleaned[len("manga/"):]
        return cleaned.strip("/")

    def _extract_chapter_id_from_url(self, href: str) -> str:
        path = href.lstrip("/")
        if path.startswith("manga/"):
            path = path[len("manga/"):]
        return path.strip("/")

    def _normalise_chapter_url(self, chapter_id: str) -> str:
        if chapter_id.startswith("http://") or chapter_id.startswith("https://"):
            return chapter_id
        slug = chapter_id.lstrip("/")
        if not slug.startswith("manga/"):
            slug = f"manga/{slug}"
        return urljoin(self.base_url, f"/{slug}")

    def _extract_chapter_numeric_id(self, html: str) -> Optional[str]:
        match = re.search(r"var\\s+chapterId\\s*=\\s*(\\d+)", html)
        if match:
            return match.group(1)
        return None

    def _extract_server_ids(self, soup: BeautifulSoup) -> List[str]:
        servers = [a.get("data-server") for a in soup.select("a.loadchapter") if a.get("data-server")]
        if not servers:
            return ["1"]
        return servers

    def _get(
        self,
        url: str,
        *,
        params: Optional[Dict[str, str]] = None,
        referer: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        allow_redirects: bool = True,
    ) -> httpx.Response:
        headers: Dict[str, str] = {}
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers or None,
                    follow_redirects=allow_redirects,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise
                last_error = exc
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.retry_attempts:
                time.sleep(min(1.0, 0.25 * attempt))
        raise ProviderError(f"Failed to fetch {url}: {last_error}") from last_error
