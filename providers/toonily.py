"""
Toonily provider for MangaForge.

This provider ports the standalone Toonily scraper into the MangaForge
provider interface.
"""
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple, Type
from urllib.parse import quote, urljoin, urlparse

import cloudscraper
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


class ToonilyProvider(BaseProvider):
    provider_id = "toonily"
    provider_name = "Toonily"
    base_url = "https://toonily.com"

    def __init__(self) -> None:
        self.config = Config()
        self.timeout = float(self.config.network_timeout or 30)
        self.retry_attempts = max(1, self.config.retry_attempts)
        super().__init__()
        try:
            self.session.close()
        except Exception:
            pass
        self.session = cloudscraper.create_scraper(
            browser={"custom": "Chrome/120"}
        )
        self.session.headers.update(self.get_headers())
        logger.info("Toonily provider initialized with CloudScraper")

    def get_headers(self) -> Dict[str, str]:
        return {
            "Referer": "https://toonily.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        logger.debug("Searching Toonily for '%s' (page %s)", query, page)
        query = query.strip()
        if not query:
            return [], False
        page = max(page, 1)
        try:
            if page == 1:
                search_url = f"{self.base_url}/?s={quote(query)}&post_type=wp-manga"
            else:
                search_url = f"{self.base_url}/page/{page}/?s={quote(query)}&post_type=wp-manga"
            html = self._request(search_url)
            soup = self._parse_html(html)
            results: List[MangaSearchResult] = []
            seen_urls = set()
            for item in soup.select("div.page-item-detail.manga"):
                link = item.select_one("h3.h5 a")
                if not link:
                    continue
                title = link.get_text(strip=True)
                manga_url = self._normalize_url(link.get("href"))
                if not manga_url or manga_url in seen_urls:
                    continue
                seen_urls.add(manga_url)
                cover_tag = item.find("img")
                cover_url = self._extract_image_source(cover_tag)
                manga_id = self._extract_manga_id_from_url(manga_url)
                results.append(
                    MangaSearchResult(
                        provider_id=self.provider_id,
                        manga_id=manga_id,
                        title=title,
                        cover_url=cover_url or "",
                        url=manga_url,
                    )
                )
            has_next = self._has_next_page(soup, page)
            logger.info("Toonily search returned %s results", len(results))
            return results, has_next
        except Exception as exc:
            logger.error("Toonily search failed: %s", exc)
            raise ProviderError(f"Search failed: {exc}") from exc

    def get_manga_info(
        self,
        manga_id: Optional[str] = None,
        url: Optional[str] = None,
    ) -> MangaInfo:
        soup, manga_url, resolved_id = self._fetch_manga_page(manga_id=manga_id, url=url)
        title = self._extract_title(soup)
        if not title:
            raise MangaNotFoundError(f"Could not parse manga title for URL: {manga_url}")
        cover_url = self._extract_cover_url(soup)
        description = self._extract_description(soup)
        alternative_titles = self._extract_alternative_titles(soup)
        authors = self._extract_people(soup, ["Author(s)", "Author"])
        artists = self._extract_people(soup, ["Artist(s)", "Artist"])
        genres = self._extract_people(soup, ["Genre(s)", "Genres"])
        status = self._extract_status(soup)
        year = self._extract_year(soup)
        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=resolved_id,
            title=title,
            alternative_titles=alternative_titles,
            cover_url=cover_url,
            url=manga_url,
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
        soup, _, resolved_id = self._fetch_manga_page(manga_id=manga_id)
        entries = self._build_chapter_entries(soup)
        if not entries:
            logger.info("No chapters found for Toonily manga %s", resolved_id)
            return []
        processed_entries = self._assign_chapter_numbers(entries)
        chapters: List[Chapter] = []
        for entry in processed_entries:
            chapters.append(
                Chapter(
                    chapter_id=entry["chapter_id"],
                    manga_id=resolved_id,
                    title=entry["title"],
                    chapter_number=entry["chapter_number"],
                    volume=None,
                    url=entry["url"],
                    release_date=entry.get("release_date"),
                    language="en",
                )
            )
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        if not chapter_id:
            raise ValueError("chapter_id is required")
        logger.debug("Fetching Toonily chapter images for %s", chapter_id)
        chapter_url = chapter_id
        if not chapter_url.startswith("http"):
            chapter_url = urljoin(f"{self.base_url}/", chapter_id.lstrip("/"))
        html = self._request(chapter_url, not_found_exception=ChapterNotFoundError)
        soup = self._parse_html(html)
        image_urls = self._extract_chapter_images(soup)
        if not image_urls:
            raise ProviderError("No images found for chapter")
        return image_urls

    def _request(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        not_found_exception: Optional[Type[Exception]] = None,
    ) -> str:
        last_exception: Optional[Exception] = None
        for attempt in range(self.retry_attempts):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 404 and not_found_exception:
                    raise not_found_exception(f"URL not found: {url}")
                response.raise_for_status()
                return response.text
            except Exception as exc:
                if not_found_exception and isinstance(exc, not_found_exception):
                    raise
                last_exception = exc
                wait_time = min(1.5 * (attempt + 1), 5.0)
                logger.debug(
                    "Request attempt %s/%s failed for %s: %s",
                    attempt + 1,
                    self.retry_attempts,
                    url,
                    exc,
                )
                if attempt < self.retry_attempts - 1:
                    time.sleep(wait_time)
        message = f"Failed to fetch {url}"
        if last_exception:
            message = f"{message}: {last_exception}"
        raise ProviderError(message)

    def _parse_html(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")

    def _normalize_url(self, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        return urljoin(f"{self.base_url}/", url)

    def _extract_image_source(self, tag) -> str:
        if not tag:
            return ""
        for attribute in ("data-src", "data-lazy-src", "data-original", "src"):
            value = tag.get(attribute)
            if value:
                value = value.strip()
                if value.startswith("//"):
                    value = f"https:{value}"
                elif value.startswith("/"):
                    value = urljoin(f"{self.base_url}/", value)
                return value
        return ""

    def _has_next_page(self, soup: BeautifulSoup, current_page: int) -> bool:
        navigation = soup.select_one("div.nav-links")
        if navigation and navigation.find("a", class_="next"):
            return True
        for anchor in soup.select("a.page-numbers"):
            if anchor.get_text(strip=True) == str(current_page + 1):
                return True
        return soup.find("a", rel="next") is not None

    def _extract_manga_id_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if "webtoon" in parts:
            index = parts.index("webtoon")
            if index + 1 < len(parts):
                return parts[index + 1]
        if parts:
            return parts[-1]
        return url

    def _extract_chapter_id_from_url(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        return path or url

    def _fetch_manga_page(
        self,
        manga_id: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Tuple[BeautifulSoup, str, str]:
        if url:
            manga_url = self._normalize_url(url)
        else:
            if not manga_id:
                raise ValueError("manga_id is required when url is not provided")
            slug = manga_id.strip("/")
            manga_url = urljoin(f"{self.base_url}/", f"webtoon/{slug}/")
        if not manga_url:
            raise MangaNotFoundError("Unable to resolve manga URL")
        html = self._request(manga_url, not_found_exception=MangaNotFoundError)
        soup = self._parse_html(html)
        resolved_id = self._extract_manga_id_from_url(manga_url)
        return soup, manga_url, resolved_id

    def _extract_title(self, soup: BeautifulSoup) -> str:
        title_element = soup.select_one("div.post-title h1")
        if not title_element:
            return ""
        for span in title_element.find_all("span"):
            span.decompose()
        return title_element.get_text(strip=True)

    def _extract_cover_url(self, soup: BeautifulSoup) -> str:
        image_tag = soup.select_one("div.summary_image img")
        return self._extract_image_source(image_tag)

    def _extract_description(self, soup: BeautifulSoup) -> str:
        selectors = [
            "div.description-summary div.summary__content",
            "div.summary__content",
            "div.post-content div.excerpt",
        ]
        for selector in selectors:
            element = soup.select_one(selector)
            if element:
                text = element.get_text(separator="\n", strip=True)
                if text:
                    return text
        return ""

    def _extract_alternative_titles(self, soup: BeautifulSoup) -> List[str]:
        content = self._get_summary_content(soup, ["Alternative", "Alternative Titles"])
        return self._extract_text_list(content)

    def _extract_people(self, soup: BeautifulSoup, labels: List[str]) -> List[str]:
        content = self._get_summary_content(soup, labels)
        return self._extract_text_list(content)

    def _extract_status(self, soup: BeautifulSoup) -> str:
        content = self._get_summary_content(soup, ["Status"])
        if not content:
            return "Unknown"
        status_text = content.get_text(separator=" ", strip=True).lower()
        if "ongoing" in status_text:
            return "Ongoing"
        if "completed" in status_text or "complete" in status_text:
            return "Completed"
        if "hiatus" in status_text:
            return "Hiatus"
        return status_text.title() if status_text else "Unknown"

    def _extract_year(self, soup: BeautifulSoup) -> Optional[int]:
        for label in ["Release", "Released", "Year", "Published"]:
            content = self._get_summary_content(soup, [label])
            if not content:
                continue
            text = content.get_text(separator=" ", strip=True)
            match = re.search(r"(19|20)\d{2}", text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    continue
        return None

    def _get_summary_content(
        self,
        soup: BeautifulSoup,
        labels: List[str],
    ):
        targets = {label.lower() for label in labels}
        for item in soup.select("div.post-content_item"):
            heading = item.select_one("div.summary-heading")
            if not heading:
                continue
            heading_text = heading.get_text(strip=True).lower()
            if heading_text in targets:
                return item.select_one("div.summary-content")
        return None

    def _extract_text_list(self, content) -> List[str]:
        if not content:
            return []
        values = [link.get_text(strip=True) for link in content.find_all("a")]
        if values:
            return [value for value in values if value]
        raw_text = content.get_text(separator=",", strip=True)
        parts = re.split(r"[;,]", raw_text)
        return [part.strip() for part in parts if part.strip()]

    def _build_chapter_entries(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for item in soup.select("li.wp-manga-chapter"):
            link = item.find("a")
            if not link:
                continue
            chapter_url = self._normalize_url(link.get("href"))
            if not chapter_url:
                continue
            title_text = link.get_text(strip=True)
            release_elem = item.select_one("span.chapter-release-date")
            release_date = release_elem.get_text(strip=True) if release_elem else None
            number_info = self._parse_chapter_number_info(title_text)
            entries.append(
                {
                    "title": title_text,
                    "url": chapter_url,
                    "chapter_id": self._extract_chapter_id_from_url(chapter_url),
                    "release_date": release_date,
                    **number_info,
                }
            )
        return entries

    def _parse_chapter_number_info(self, title: str) -> Dict[str, Any]:
        patterns = [
            r"Chapter\s*(\d+(?:\.\d+)?)",
            r"Ch\.?\s*(\d+(?:\.\d+)?)",
            r"Episode\s*(\d+(?:\.\d+)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                try:
                    number = float(match.group(1))
                except ValueError:
                    number = -1.0
                return {"number": number, "is_side_story": False, "side_story_index": None}
        side_story_match = re.search(r"Side\s*Story\s*(\d+)", title, re.IGNORECASE)
        if side_story_match:
            try:
                side_index = int(side_story_match.group(1))
            except ValueError:
                side_index = 0
            return {"number": -1.0, "is_side_story": True, "side_story_index": side_index}
        special_match = re.search(r"Special\s*(\d+)", title, re.IGNORECASE)
        if special_match:
            try:
                side_index = int(special_match.group(1))
            except ValueError:
                side_index = 0
            return {"number": -1.0, "is_side_story": True, "side_story_index": side_index}
        return {"number": -1.0, "is_side_story": False, "side_story_index": None}

    def _assign_chapter_numbers(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not entries:
            return []
        main = [entry for entry in entries if not entry["is_side_story"]]
        side = [entry for entry in entries if entry["is_side_story"]]
        main.sort(key=lambda entry: entry["number"])
        side.sort(key=lambda entry: entry.get("side_story_index") or 0)
        non_negative_main = [entry for entry in main if entry["number"] >= 0]
        if non_negative_main:
            max_number = non_negative_main[-1]["number"]
            start_number = math.ceil(max_number)
            if start_number <= max_number:
                start_number += 1.0
        else:
            start_number = 1.0
        for index, entry in enumerate(side):
            entry["number"] = start_number + index
        combined = main + side
        combined.sort(key=lambda entry: entry["number"])
        processed: List[Dict[str, Any]] = []
        for entry in combined:
            number = entry["number"]
            chapter_number = (
                self._format_chapter_number(number)
                if number >= 0
                else entry["title"]
            )
            processed.append(
                {
                    "title": entry["title"],
                    "url": entry["url"],
                    "chapter_id": entry["chapter_id"],
                    "release_date": entry.get("release_date"),
                    "chapter_number": chapter_number,
                }
            )
        return processed

    def _format_chapter_number(self, number: float) -> str:
        if number.is_integer():
            return str(int(number))
        return str(number).rstrip("0").rstrip(".")

    def _extract_chapter_images(self, soup: BeautifulSoup) -> List[str]:
        reading_content = soup.select_one("div.reading-content")
        if not reading_content:
            return []
        images: List[str] = []
        seen: Set[str] = set()
        image_tags = reading_content.find_all(
            "img", class_=re.compile("wp-manga-chapter-img")
        )
        if not image_tags:
            image_tags = reading_content.find_all("img")
        for img_tag in image_tags:
            src = self._extract_image_source(img_tag)
            if src and src not in seen:
                images.append(src)
                seen.add(src)
        return images
