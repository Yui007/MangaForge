import logging
import re
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from core.base_provider import (
    BaseProvider,
    ProviderError,
    MangaNotFoundError,
    ChapterNotFoundError,
)
from models import Chapter, MangaInfo, MangaSearchResult

logger = logging.getLogger(__name__)


class VymangaProvider(BaseProvider):
    """Provider implementation for https://vymanga.co."""

    provider_id = "vymanga"
    provider_name = "VyManga"
    base_url = "https://vymanga.co"

    def __init__(self) -> None:
        super().__init__()
        self._warning_cache: Set[str] = set()

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        """Search for manga titles on VyManga."""
        params = {"q": query, "page": page}
        search_url = f"{self.base_url}/search"
        logger.debug("Searching VyManga for '%s' (page %s)", query, page)

        try:
            _, soup = self._get_page(search_url, params=params)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Search failed: {exc}") from exc

        results: List[MangaSearchResult] = []
        seen_ids: Set[str] = set()

        for anchor in soup.select("div.comic-item > a"):
            href = anchor.get("href")
            if not href:
                continue
            url = self._ensure_absolute_url(href)
            manga_id = self._extract_manga_id(url)
            if not manga_id or manga_id in seen_ids:
                continue

            title_element = anchor.select_one(".comic-title")
            title = title_element.get_text(strip=True) if title_element else manga_id.replace("-", " ").title()

            cover_img = anchor.select_one(".comic-image img")
            cover_url = ""
            if cover_img:
                cover_url = self._select_image_source(cover_img)
                if cover_url:
                    cover_url = self._ensure_absolute_url(cover_url)
                else:
                    cover_url = ""

            results.append(
                MangaSearchResult(
                    provider_id=self.provider_id,
                    manga_id=manga_id,
                    title=title,
                    cover_url=cover_url,
                    url=url,
                )
            )
            seen_ids.add(manga_id)

        has_next_page = self._has_next_page(soup)
        logger.debug("VyManga search returned %d results (has_next=%s)", len(results), has_next_page)
        return results, has_next_page

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """Fetch detailed manga information."""
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        target_url = url or self._build_manga_url(manga_id)
        logger.debug("Fetching VyManga info for URL: %s", target_url)

        try:
            response, soup = self._get_page(target_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {target_url}") from exc
            raise ProviderError(f"Failed to fetch manga info: {exc}") from exc

        canonical_url = str(response.url)
        resolved_manga_id = self._extract_manga_id(canonical_url) or (manga_id or "")

        title_element = soup.select_one("h1.title")
        title = title_element.get_text(strip=True) if title_element else resolved_manga_id

        cover_url = ""
        cover_img = soup.select_one("div.img-manga img")
        if cover_img:
            extracted_cover = self._select_image_source(cover_img)
            if extracted_cover:
                cover_url = self._ensure_absolute_url(extracted_cover)

        info_block = soup.select_one("div.col-md-7")
        alternative_titles = self._extract_alternative_titles(info_block)
        authors = self._extract_people(info_block, "Authors")
        artists = self._extract_people(info_block, "Artists")
        genres = self._extract_genres(info_block)
        status = self._extract_status(info_block)
        year = self._extract_year(info_block)

        description = ""
        description_element = soup.select_one("p.content")
        if description_element:
            description = description_element.get_text(" ", strip=True)

        manga_info = MangaInfo(
            provider_id=self.provider_id,
            manga_id=resolved_manga_id,
            title=title,
            alternative_titles=alternative_titles,
            cover_url=cover_url,
            url=canonical_url,
            description=description,
            authors=authors,
            artists=artists,
            genres=genres,
            status=status,
            year=year,
        )
        logger.debug("Fetched VyManga info for '%s' (%s)", title, resolved_manga_id)
        return manga_info

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """Get chapter listing for a manga."""
        target_url = self._build_manga_url(manga_id)
        logger.debug("Fetching VyManga chapters for %s", target_url)

        try:
            response, soup = self._get_page(target_url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {target_url}") from exc
            raise ProviderError(f"Failed to fetch chapters: {exc}") from exc

        canonical_url = str(response.url)
        resolved_manga_id = self._extract_manga_id(canonical_url) or manga_id

        chapters: List[Chapter] = []
        chapter_links = soup.select("div.list a.list-group-item")
        logger.debug("Found %d chapter elements on VyManga", len(chapter_links))

        for index, link in enumerate(chapter_links, start=1):
            href = link.get("href")
            if not href:
                continue

            chapter_url = self._ensure_absolute_url(href)
            chapter_id = chapter_url

            title_span = link.select_one("span")
            chapter_title = title_span.get_text(strip=True) if title_span else link.get_text(" ", strip=True)
            chapter_number = self._extract_chapter_number(chapter_title, index)

            date_element = link.select_one("p.text-right")
            release_date = date_element.get_text(strip=True) if date_element else None

            chapter = Chapter(
                chapter_id=chapter_id,
                manga_id=resolved_manga_id,
                title=chapter_title,
                chapter_number=chapter_number,
                volume=None,
                url=chapter_url,
                release_date=release_date,
                language="en",
            )
            chapters.append(chapter)

        chapters.sort(key=lambda ch: self._chapter_sort_key(ch.chapter_number))
        logger.debug("Processed %d VyManga chapters", len(chapters))
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """Fetch image URLs for a chapter."""
        chapter_url = self._ensure_absolute_url(chapter_id)
        logger.debug("Fetching VyManga images for chapter: %s", chapter_url)

        try:
            _, soup = self._get_page(chapter_url, allow_warning=False)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ChapterNotFoundError(f"Chapter not found: {chapter_url}") from exc
            raise ProviderError(f"Failed to fetch chapter images: {exc}") from exc

        image_urls: List[str] = []
        for img in soup.select("img"):
            src = self._select_image_source(img)
            if not src:
                continue
            if "loading.gif" in src:
                continue
            normalized = self._ensure_absolute_url(src)
            if normalized not in image_urls:
                image_urls.append(normalized)

        if not image_urls:
            raise ProviderError("No images found for chapter")

        logger.debug("Found %d images for VyManga chapter", len(image_urls))
        return image_urls

    def _get_page(
        self,
        url: str,
        *,
        params: Optional[Dict[str, str]] = None,
        allow_warning: bool = True,
    ) -> Tuple[httpx.Response, BeautifulSoup]:
        """Make an HTTP GET request and return the parsed HTML."""
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(f"Request failed for {url}: {exc}") from exc

        html = response.text
        if allow_warning:
            html = self._maybe_bypass_warning(response, params=params, original_html=html)

        soup = BeautifulSoup(html, "lxml")
        return response, soup

    def _maybe_bypass_warning(
        self,
        response: httpx.Response,
        *,
        params: Optional[Dict[str, str]] = None,
        original_html: Optional[str] = None,
    ) -> str:
        """Handle the adult warning modal if it appears."""
        html = original_html if original_html is not None else response.text
        page_url = str(response.url)

        if "closeWarningContent" not in html:
            return html
        if page_url in self._warning_cache:
            return html

        self._warning_cache.add(page_url)
        logger.debug("Attempting to dismiss adult warning for %s", page_url)

        try:
            warn_response = self.session.post(
                page_url,
                data={"accept_warning": "1"},
                headers={"Referer": page_url},
            )
            warn_response.raise_for_status()
            if "closeWarningContent" not in warn_response.text:
                return warn_response.text

            follow_up = self.session.get(page_url, params=params)
            follow_up.raise_for_status()
            return follow_up.text
        except httpx.HTTPError as exc:
            logger.debug("Adult warning bypass failed for %s: %s", page_url, exc)
            return html

    def _ensure_absolute_url(self, url: str) -> str:
        if not url:
            return url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("//"):
            return f"https:{url}"
        return urljoin(self.base_url, url)

    def _build_manga_url(self, manga_id: Optional[str]) -> str:
        if not manga_id:
            raise ValueError("manga_id is required when url is not provided")
        if manga_id.startswith("http://") or manga_id.startswith("https://"):
            return manga_id
        return f"{self.base_url}/manga/{manga_id}"

    def _extract_manga_id(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) >= 2 and segments[0] == "manga":
            return segments[1]
        if segments and segments[-1] not in {"manga", ""}:
            return segments[-1]
        return None

    def _select_image_source(self, img_tag) -> Optional[str]:
        for attribute in ("data-src", "data-original", "src"):
            value = img_tag.get(attribute)
            if value:
                return value.strip()
        return None

    def _extract_alternative_titles(self, info_block) -> List[str]:
        if not info_block:
            return []
        titles: List[str] = []
        for paragraph in info_block.find_all("p", recursive=False):
            if paragraph.find("span", class_="pre-title"):
                continue
            text = paragraph.get_text(" ", strip=True)
            if not text or text.lower().startswith("followed by"):
                continue
            if text not in titles:
                titles.append(text)
        return titles

    def _extract_people(self, info_block, label: str) -> List[str]:
        if not info_block:
            return []
        people: List[str] = []
        for paragraph in info_block.select("p"):
            span = paragraph.find("span", class_="pre-title")
            if not span or span.get_text(strip=True).lower() != label.lower():
                continue
            links = [link.get_text(strip=True) for link in paragraph.select("a") if link.get_text(strip=True)]
            if links:
                people.extend(links)
            else:
                text = paragraph.get_text(" ", strip=True)
                parts = text.split(":", 1)
                if len(parts) == 2:
                    names = [name.strip() for name in parts[1].split(",") if name.strip()]
                    people.extend(names)
        return people

    def _extract_genres(self, info_block) -> List[str]:
        if not info_block:
            return []
        genres = [badge.get_text(strip=True) for badge in info_block.select("a.badge") if badge.get_text(strip=True)]
        return genres

    def _extract_status(self, info_block) -> str:
        if not info_block:
            return "Unknown"
        status_element = info_block.select_one("span[class*='text-']")
        if status_element:
            status_text = status_element.get_text(strip=True)
            if status_text:
                return status_text.title()
        return "Unknown"

    def _extract_year(self, info_block) -> Optional[int]:
        if not info_block:
            return None
        for paragraph in info_block.select("p"):
            span = paragraph.find("span", class_="pre-title")
            if not span:
                continue
            if "release" in span.get_text(strip=True).lower():
                text = paragraph.get_text(" ", strip=True)
                match = re.search(r"(\d{4})", text)
                if match:
                    try:
                        return int(match.group(1))
                    except ValueError:
                        return None
        return None

    def _extract_chapter_number(self, title: str, fallback_index: int) -> str:
        match = re.search(r"(\d+(?:\.\d+)?)", title)
        if match:
            return match.group(1)
        return str(fallback_index)

    def _chapter_sort_key(self, chapter_number: str) -> Tuple[int, float | str]:
        try:
            return (0, float(chapter_number))
        except ValueError:
            return (1, chapter_number.lower())

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        pagination = soup.select_one("ul.pagination")
        if not pagination:
            return False
        next_link = pagination.select_one("a[rel='next']")
        if next_link:
            return True
        for link in pagination.select("a.page-link"):
            aria = link.get("aria-label", "").lower()
            if "next" in aria:
                return True
        return False
