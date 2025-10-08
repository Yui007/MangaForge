"""MangaKakalot provider implementation for MangaForge."""
import ast
import logging
import re
from typing import List, Optional, Tuple, Type
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


class MangakakalotProvider(BaseProvider):
    """Provider that scrapes data from https://mangakakalot.gg."""

    provider_id = "mangakakalot"
    provider_name = "MangaKakalot"
    base_url = "https://www.mangakakalot.gg"

    def __init__(self) -> None:
        """Initialise provider with configuration-driven HTTP setup."""
        self.config = Config()
        self.retry_attempts = self.config.get("network.retry_attempts", 3)
        self.timeout = self.config.get("network.timeout", 30)
        super().__init__()

        # Replace default session with config aware client (close original first).
        self.session.close()
        self.session = httpx.Client(
            headers=self.get_headers(),
            timeout=self.timeout,
            follow_redirects=True,
        )

    def get_headers(self) -> dict:
        """Return headers that respect configured user-agent settings."""
        headers = super().get_headers()
        user_agent = self.config.get("network.user_agent")
        if user_agent:
            headers["User-Agent"] = user_agent
        headers.setdefault("Referer", self.base_url)
        return headers

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        logger.debug("Searching MangaKakalot for '%s' (page %s)", query, page)

        if not query.strip():
            return [], False

        search_slug = re.sub(r"\s+", "_", query.strip())
        search_url = f"{self.base_url}/search/story/{search_slug}"
        params = {"page": page} if page > 1 else None

        soup = self._fetch_soup(search_url, params=params)

        results: List[MangaSearchResult] = []
        seen_urls = set()

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
        logger.info("MangaKakalot search returned %d results (has_next=%s)", len(results), has_next)
        return results, has_next

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        target_url = self._ensure_absolute_url(url or manga_id)
        logger.debug("Fetching MangaKakalot manga info from %s", target_url)

        soup = self._fetch_soup(target_url, not_found_exc=MangaNotFoundError)

        title = self._extract_title(soup)
        if not title:
            raise MangaNotFoundError(f"Could not extract title for URL: {target_url}")

        extracted_manga_id = self._extract_id_from_url(target_url)
        cover_url = self._extract_cover_url(soup)
        alternative_titles = self._extract_alternative_titles(soup, title)
        description = self._extract_description(soup)
        authors = self._extract_person_list(soup, ["Author", "Authors"])
        artists = self._extract_person_list(soup, ["Artist", "Artists"]) or authors
        genres = self._extract_genres(soup)
        status = self._extract_status(soup)
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

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        if not manga_id:
            raise ValueError("manga_id is required")

        series_url = self._ensure_absolute_url(manga_id)
        logger.debug("Fetching MangaKakalot chapters for %s", series_url)

        soup = self._fetch_soup(series_url, not_found_exc=MangaNotFoundError)

        chapters: List[Chapter] = []
        chapter_elements = soup.select(".chapter-list .row, ul.row-content-chapter li")

        for element in chapter_elements:
            link = element.find("a", href=True)
            if not link:
                continue

            chapter_url = self._ensure_absolute_url(link["href"])
            chapter_id = self._extract_id_from_url(chapter_url)
            chapter_title = link.get("title") or link.get_text(strip=True)

            if not chapter_title:
                continue

            chapter_number = self._extract_chapter_number(chapter_title)
            volume = self._extract_volume(chapter_title)
            release_date = self._extract_release_date(element)

            chapter = Chapter(
                chapter_id=chapter_id,
                manga_id=manga_id,
                title=chapter_title,
                chapter_number=chapter_number,
                volume=volume,
                url=chapter_url,
                release_date=release_date,
                language="en",
            )
            chapters.append(chapter)

        chapters.sort(key=lambda chapter: chapter.sort_key)
        logger.info("Found %d MangaKakalot chapters for %s", len(chapters), manga_id)
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        if not chapter_id:
            raise ValueError("chapter_id is required")

        chapter_url = self._ensure_absolute_url(chapter_id)
        logger.debug("Fetching MangaKakalot chapter images from %s", chapter_url)

        soup = self._fetch_soup(chapter_url, not_found_exc=ChapterNotFoundError)

        image_urls = self._extract_image_urls(soup)
        if not image_urls:
            raise ProviderError(f"Failed to extract image URLs for chapter: {chapter_id}")

        logger.info("Extracted %d image URLs for MangaKakalot chapter %s", len(image_urls), chapter_id)
        return image_urls

    def _fetch_soup(
        self,
        url: str,
        params: Optional[dict] = None,
        not_found_exc: Optional[Type[Exception]] = None,
    ) -> BeautifulSoup:
        response = self._request(url, params=params, not_found_exc=not_found_exc)
        return self._parse_html(response.text)

    def _request(
        self,
        url: str,
        params: Optional[dict] = None,
        not_found_exc: Optional[Type[Exception]] = None,
    ) -> httpx.Response:
        last_exception: Optional[Exception] = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = self.session.get(url, params=params)
                if response.status_code == 404 and not_found_exc:
                    raise not_found_exc(f"Resource not found: {url}")
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404 and not_found_exc:
                    raise not_found_exc(f"Resource not found: {url}") from exc
                last_exception = exc
                logger.warning("HTTP error fetching %s (attempt %d/%d): %s", url, attempt, self.retry_attempts, exc)
            except httpx.RequestError as exc:
                last_exception = exc
                logger.warning("Request error fetching %s (attempt %d/%d): %s", url, attempt, self.retry_attempts, exc)

        raise ProviderError(f"Failed to fetch URL after {self.retry_attempts} attempts: {url}") from last_exception

    def _parse_html(self, html: str) -> BeautifulSoup:
        try:
            return BeautifulSoup(html, "lxml")
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def _ensure_absolute_url(self, url_or_path: Optional[str]) -> str:
        if not url_or_path:
            return self.base_url
        if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
            return url_or_path
        return urljoin(f"{self.base_url}/", url_or_path.lstrip("/"))

    def _extract_id_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.lstrip("/")
        return path or parsed.netloc

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        title_element = soup.select_one(".manga-info-content h1, .story-info-right h1, .story-info-right h1.title")
        if title_element:
            return title_element.get_text(strip=True)
        title_element = soup.find("h1")
        return title_element.get_text(strip=True) if title_element else None

    def _extract_cover_url(self, soup: BeautifulSoup) -> str:
        cover = soup.select_one(".manga-info-pic img, .story-info-left img, .manga-info-img img")
        if cover:
            return cover.get("data-src") or cover.get("src") or ""
        return ""

    def _extract_alternative_titles(self, soup: BeautifulSoup, main_title: str) -> List[str]:
        detail_text = self._extract_detail_text(soup, ["Alternative", "Other name", "Alternative name"])
        alternatives = []
        if detail_text:
            for raw in re.split(r"[;,/]|\s{2,}", detail_text):
                alt = raw.strip()
                if alt and alt.lower() != main_title.lower():
                    alternatives.append(alt)
        return alternatives

    def _extract_description(self, soup: BeautifulSoup) -> str:
        description_container = soup.select_one(
            "#panel-story-info-description .panel-body, "
            "#panel-story-info-description, "
            ".panel-story-info-description, "
            ".story-info-right .description"
        )
        if description_container:
            paragraphs = [
                p.get_text(" ", strip=True)
                for p in description_container.select("p")
                if p.get_text(strip=True)
            ]
            if paragraphs:
                return "\n\n".join(paragraphs)
            return description_container.get_text(" ", strip=True)
        return ""

    def _extract_person_list(self, soup: BeautifulSoup, labels: List[str]) -> List[str]:
        detail_element = self._extract_detail_element(soup, labels)
        if not detail_element:
            return []

        values = [a.get_text(strip=True) for a in detail_element.find_all("a") if a.get_text(strip=True)]
        if values:
            return values

        text = detail_element.get_text(" ", strip=True)
        for label in labels:
            if text.lower().startswith(label.lower()):
                parts = text.split(":", 1)
                if len(parts) == 2:
                    text = parts[1].strip()
                break

        return [value.strip() for value in re.split(r"[;,/]|\s{2,}", text) if value.strip()]

    def _extract_genres(self, soup: BeautifulSoup) -> List[str]:
        detail_element = self._extract_detail_element(soup, ["Genre", "Genres"])
        if detail_element:
            genres = [a.get_text(strip=True) for a in detail_element.find_all("a") if a.get_text(strip=True)]
            if genres:
                return genres
            text = detail_element.get_text(" ", strip=True)
            if ":" in text:
                text = text.split(":", 1)[1]
            return [genre.strip() for genre in text.split(",") if genre.strip()]
        return []

    def _extract_status(self, soup: BeautifulSoup) -> str:
        status_text = self._extract_detail_text(soup, ["Status"])
        if status_text:
            if "ongoing" in status_text.lower():
                return "Ongoing"
            if "completed" in status_text.lower():
                return "Completed"
            if "hiatus" in status_text.lower():
                return "Hiatus"
        return "Unknown"

    def _extract_year(self, soup: BeautifulSoup) -> Optional[int]:
        release_text = self._extract_detail_text(soup, ["Released", "Release", "Year"])
        if release_text:
            match = re.search(r"(19|20)\d{2}", release_text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    return None
        return None

    def _extract_detail_text(self, soup: BeautifulSoup, labels: List[str]) -> str:
        detail_element = self._extract_detail_element(soup, labels)
        if not detail_element:
            return ""

        text = detail_element.get_text(" ", strip=True)
        for label in labels:
            if text.lower().startswith(label.lower()):
                parts = text.split(":", 1)
                if len(parts) == 2:
                    return parts[1].strip()
        return text.strip()

    def _extract_detail_element(self, soup: BeautifulSoup, labels: List[str]):
        info_section = soup.select_one(".story-info-right")
        if info_section:
            title_elements = info_section.select(".story-info-right-title")
            detail_elements = info_section.select(".story-info-right-detail")
            if len(title_elements) == len(detail_elements):
                for title_el, detail_el in zip(title_elements, detail_elements):
                    label_text = title_el.get_text(" ", strip=True).rstrip(":").lower()
                    for label in labels:
                        if label.lower() in label_text:
                            return detail_el

        fallback_section = soup.select_one(".manga-info-text, .manga-info-content")
        if fallback_section:
            for element in fallback_section.find_all(["li", "p", "span", "div"], recursive=True):
                text = element.get_text(" ", strip=True)
                if not text:
                    continue
                lower_text = text.lower()
                for label in labels:
                    label_lower = label.lower()
                    if lower_text.startswith(label_lower) or f"{label_lower}:" in lower_text:
                        return element
        return None

    def _extract_chapter_number(self, chapter_title: str) -> str:
        match = re.search(r"(?:chapter|ch\.?|cap\.)\s*(\d+(?:\.\d+)?)", chapter_title, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"(\d+(?:\.\d+)?)", chapter_title)
        if match:
            return match.group(1)
        return chapter_title.strip()

    def _extract_volume(self, chapter_title: str) -> Optional[str]:
        match = re.search(r"vol(?:ume)?\.?\s*(\d+)", chapter_title, re.IGNORECASE)
        return match.group(1) if match else None

    def _extract_release_date(self, element) -> Optional[str]:
        date_element = element.select_one(".chapter-time, .time, .chapter-date, .chapter-time.text-nowrap")
        if date_element and date_element.get_text(strip=True):
            return date_element.get_text(strip=True)
        for node in element.find_all(string=True):
            text = node.strip()
            if text and any(keyword in text.lower() for keyword in ["ago", "day", "hour", "year", "month"]):
                return text
        return None

    def _extract_image_urls(self, soup: BeautifulSoup) -> List[str]:
        script_tags = soup.find_all("script")
        raw_images: List[str] = []
        cdn_root = ""

        for script in script_tags:
            content = script.string or str(script)
            if not content or "chapterImages" not in content:
                continue

            images_match = re.search(r"chapterImages\s*=\s*(\[.*?\]);", content, re.DOTALL)
            if images_match:
                raw_images = self._safe_eval_array(images_match.group(1))

            cdn_match = re.search(r"cdns\s*=\s*(\[.*?\]);", content, re.DOTALL)
            if cdn_match:
                cdn_values = self._safe_eval_array(cdn_match.group(1))
                if cdn_values:
                    cdn_root = cdn_values[0].replace("\\/", "/")

            if raw_images:
                break

        images: List[str] = []
        for path in raw_images:
            if not isinstance(path, str):
                continue
            cleaned = path.replace("\\/", "/").strip()
            if not cleaned:
                continue
            if cleaned.startswith("http://") or cleaned.startswith("https://"):
                images.append(cleaned)
            elif cdn_root:
                images.append(urljoin(f"{cdn_root.rstrip('/')}/", cleaned.lstrip("/")))

        return images

    def _safe_eval_array(self, array_literal: str) -> List[str]:
        try:
            value = ast.literal_eval(array_literal)
            if isinstance(value, list):
                return [str(item) for item in value]
        except (ValueError, SyntaxError):
            pass

        # Fallback: split manually on commas outside quotes is complex; use simple replacement.
        cleaned = array_literal.strip().strip("[]")
        items = []
        for raw in cleaned.split(","):
            item = raw.strip().strip("'\"")
            if item:
                items.append(item)
        return items

    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        next_link = soup.select_one("a.page_next, a.next, .pagination a[rel='next']")
        if next_link:
            return True
        for anchor in soup.select(".pagination a, a"):
            text = anchor.get_text(strip=True).lower()
            if text in {"next", ">", ">>", "more"}:
                return True
        return False
