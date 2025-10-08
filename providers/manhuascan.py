"""ManhuaScan provider implementation for MangaForge."""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
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


class ManhuaScanProvider(BaseProvider):
    provider_id = "manhuascan"
    provider_name = "ManhuaScan"
    base_url = "https://manhuaplus.com"

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
        logger.debug("Searching ManhuaScan for '%s' (page %s)", query, page)

        if not query.strip():
            return [], False

        # ManhuaScan uses WordPress search with specific parameters (from working test.py)
        search_params = {
            "s": query.strip(),
            "post_type": "wp-manga",
            "op": "",
            "author": "",
            "artist": "",
            "release": "",
            "adult": ""
        }

        # Since there's no pagination, always return page 1 results
        if page > 1:
            logger.info("ManhuaScan doesn't have pagination, returning page 1 results")
            return [], False

        try:
            response = self._get(f"{self.base_url}/", params=search_params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return [], False
            raise ProviderError(f"Search failed: {exc}") from exc

        soup = self._parse_html(response.text)
        results: List[MangaSearchResult] = []

        # Use the working selector from test.py: .row.c-tabs-item__content
        for item in soup.select(".row.c-tabs-item__content"):
            result = self._parse_search_item(item)
            if result:
                results.append(result)

        # No pagination system
        has_next = False
        logger.info("ManhuaScan search returned %s results (no pagination)", len(results))
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

        logger.debug("Fetching ManhuaScan manga info from %s", target_url)

        try:
            response = self._get(target_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {manga_id}") from exc
            raise ProviderError(f"Failed to fetch manga page: {exc}") from exc

        soup = self._parse_html(response.text)

        # Extract title
        title = self._extract_title(soup)
        if not title:
            raise MangaNotFoundError(f"Unable to extract title for {manga_id}")

        # Extract all metadata
        cover_url = self._extract_cover(soup, target_url)
        alternative_titles = self._extract_alternative_titles(soup)
        authors = self._extract_authors(soup)
        artists = self._extract_artists(soup)
        genres = self._extract_genres(soup)
        manga_type = self._extract_type(soup)
        status = self._extract_status(soup)
        release_year = self._extract_release_year(soup)
        description = self._extract_description(soup)

        manga_info = MangaInfo(
            provider_id=self.provider_id,
            manga_id=manga_id or "",
            title=title,
            alternative_titles=alternative_titles,
            cover_url=cover_url,
            url=target_url,
            description=description,
            authors=authors,
            artists=artists,
            genres=genres,
            status=status,
            year=release_year,
        )

        return manga_info

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        logger.debug("Fetching ManhuaScan chapters for %s", manga_id)
        manga_url = urljoin(self.base_url, f"/manga/{manga_id}")

        try:
            response = self._get(manga_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {manga_id}") from exc
            raise ProviderError(f"Failed to fetch manga page: {exc}") from exc

        soup = self._parse_html(response.text)
        chapters: List[Chapter] = []

        # Extract chapters from the listing
        chapter_items = soup.select("div.listing-chapters_wrap ul.main.version-chap li.wp-manga-chapter")
        for item in chapter_items:
            chapter = self._parse_chapter_item(item, manga_id)
            if chapter:
                chapters.append(chapter)

        chapters.sort(key=self._chapter_sort_key)
        logger.info("Found %d ManhuaScan chapters for %s", len(chapters), manga_id)
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        chapter_url = self._normalise_chapter_url(chapter_id)
        logger.debug("Fetching ManhuaScan chapter images for %s", chapter_url)

        try:
            response = self._get(chapter_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ChapterNotFoundError(f"Chapter not found: {chapter_id}") from exc
            raise ProviderError(f"Failed to fetch chapter page: {exc}") from exc

        soup = self._parse_html(response.text)
        image_urls: List[str] = []

        # ManhuaScan uses simple structure: div.text-left img elements
        img_elements = soup.select("div.text-left img")

        for img in img_elements:
            img_url = img.get("src")
            if img_url and img_url not in image_urls:
                # Images are already absolute URLs from cdn.manhuaplus.com
                image_urls.append(img_url)

        if not image_urls:
            logger.warning("No images found for chapter %s", chapter_id)
            return []

        logger.info("Extracted %d image URLs from ManhuaScan chapter", len(image_urls))
        return image_urls

    def _parse_search_item(self, item: BeautifulSoup) -> Optional[MangaSearchResult]:
        # ManhuaScan search results structure: .row.c-tabs-item__content
        title_elem = item.select_one(".post-title h3 a")
        if not title_elem or not title_elem.get("href"):
            return None

        title = title_elem.get_text(" ", strip=True)
        url = urljoin(self.base_url, title_elem["href"])
        manga_id = self._extract_manga_id(url)

        # Look for cover image in .tab-thumb
        cover_elem = item.select_one(".tab-thumb img")
        cover_url = ""
        if cover_elem:
            cover_url = cover_elem.get("data-src") or cover_elem.get("src") or ""
            if cover_url:
                cover_url = urljoin(self.base_url, cover_url)

        return MangaSearchResult(
            provider_id=self.provider_id,
            manga_id=manga_id,
            title=title,
            cover_url=cover_url,
            url=url,
        )

    def _has_next_page(self, soup: BeautifulSoup, page: int) -> bool:
        # Look for pagination elements
        pagination_selectors = [
            ".pagination .next",
            ".pagination a[href*='page']",
            "a[href*='?page=']",
            ".page-nav .next"
        ]

        for selector in pagination_selectors:
            next_element = soup.select_one(selector)
            if next_element:
                return True

        return False

    def _parse_chapter_item(self, item: BeautifulSoup, manga_id: str) -> Optional[Chapter]:
        anchor = item.find("a", href=True)
        if not anchor:
            return None

        chapter_url = urljoin(self.base_url, anchor["href"])
        title_text = anchor.get_text(" ", strip=True)

        # Extract chapter number
        chapter_number = self._extract_chapter_number(title_text) or title_text

        # Extract release date
        date_elem = item.select_one("span.chapter-release-date i")
        release_date = date_elem.get_text(strip=True) if date_elem else None

        # Extract chapter ID from URL
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
        match = re.search(r"(?i)(?:chapter|ch\.?)\s*([0-9]+(?:\.[0-9]+)?)", text)
        if match:
            return match.group(1)
        return None

    def _chapter_sort_key(self, chapter: Chapter) -> Tuple[int, float, str]:
        try:
            number = float(chapter.chapter_number)
            return (0, number, chapter.title)
        except ValueError:
            return (1, float("inf"), chapter.title)

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        # Title from post-title h1
        title_elem = soup.select_one("div.post-title h1")
        if title_elem:
            return title_elem.get_text(strip=True)
        return None

    def _extract_cover(self, soup: BeautifulSoup, base_url: str) -> str:
        # Cover from summary_image
        cover_elem = soup.select_one("div.summary_image img")
        if cover_elem:
            src = cover_elem.get("data-src") or cover_elem.get("src")
            if src:
                return urljoin(base_url, src)
        return ""

    def _extract_alternative_titles(self, soup: BeautifulSoup) -> List[str]:
        # Alternative titles from summary_content
        alt_elem = soup.select_one("div.post-content_item:has(h5:contains('Alternative')) div.summary-content")
        if alt_elem:
            text = alt_elem.get_text(strip=True)
            if text:
                # Split by common separators
                alternatives = re.split(r"[•,;]", text)
                return [alt.strip() for alt in alternatives if alt.strip()]
        return []

    def _extract_authors(self, soup: BeautifulSoup) -> List[str]:
        # Authors from author-content
        authors = []
        author_elem = soup.select_one("div.post-content_item:has(h5:contains('Author')) div.author-content a")
        if author_elem:
            authors.append(author_elem.get_text(strip=True))

        # Also check for Author(s) section
        authors_section = soup.select_one("div.post-content_item:has(h5:contains('Author')) div.summary-content")
        if authors_section:
            for author_link in authors_section.select("a"):
                author_name = author_link.get_text(strip=True)
                if author_name:
                    authors.append(author_name)

        return authors

    def _extract_artists(self, soup: BeautifulSoup) -> List[str]:
        # Artists from artist-content
        artists = []
        artist_elem = soup.select_one("div.post-content_item:has(h5:contains('Artist')) div.artist-content a")
        if artist_elem:
            artists.append(artist_elem.get_text(strip=True))

        # Also check for Artist(s) section
        artists_section = soup.select_one("div.post-content_item:has(h5:contains('Artist')) div.summary-content")
        if artists_section:
            for artist_link in artists_section.select("a"):
                artist_name = artist_link.get_text(strip=True)
                if artist_name:
                    artists.append(artist_name)

        return artists

    def _extract_genres(self, soup: BeautifulSoup) -> List[str]:
        # Genres from genres-content
        genres = []
        genres_section = soup.select_one("div.post-content_item:has(h5:contains('Genre')) div.genres-content")
        if genres_section:
            for genre_link in genres_section.select("a"):
                genre_name = genre_link.get_text(strip=True)
                if genre_name:
                    genres.append(genre_name)

        return genres

    def _extract_type(self, soup: BeautifulSoup) -> str:
        # Type from summary_content
        type_elem = soup.select_one("div.post-content_item:has(h5:contains('Type')) div.summary-content")
        if type_elem:
            return type_elem.get_text(strip=True)
        return "Unknown"

    def _extract_status(self, soup: BeautifulSoup) -> str:
        # Status from post-status
        status_elem = soup.select_one("div.post-content_item:has(h5:contains('Status')) div.summary-content")
        if status_elem:
            status_text = status_elem.get_text(strip=True)
            if "ongoing" in status_text.lower():
                return "Ongoing"
            elif "completed" in status_text.lower():
                return "Completed"
            elif "hiatus" in status_text.lower():
                return "Hiatus"
            return status_text
        return "Unknown"

    def _extract_release_year(self, soup: BeautifulSoup) -> Optional[int]:
        # Release year from post-status
        release_elem = soup.select_one("div.post-content_item:has(h5:contains('Release')) div.summary-content a")
        if release_elem:
            year_text = release_elem.get_text(strip=True)
            match = re.search(r"(19|20)\d{2}", year_text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    pass
        return None

    def _extract_description(self, soup: BeautifulSoup) -> str:
        # Description from description-summary
        desc_elem = soup.select_one("div.description-summary div.summary__content")
        if desc_elem:
            text = desc_elem.get_text(" ", strip=True)
            if text:
                return text
        return "No description available."

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