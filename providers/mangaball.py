"""
MangaBall provider for MangaForge.

Uses CSRF-protected POST APIs at https://mangaball.net.
Chapters have multiple translations (languages) — defaults to English.
Chapter images are extracted from inline JS on the chapter page.

Requirements: requests, beautifulsoup4, lxml
"""
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)

BASE_URL = "https://mangaball.net"
API_SEARCH = f"{BASE_URL}/api/v1/title/search-advanced/"
API_CHAPTERS = f"{BASE_URL}/api/v1/chapter/chapter-listing-by-title-id/"


class MangaBallProvider(BaseProvider):
    """Provider for mangaball.net.

    Uses CSRF tokens for API calls (extracted from homepage meta tag).
    Chapters are multi-language — each chapter has a `translations[]`
    list. We default to English and fall back to the first available.
    """

    provider_id = "mangaball"
    provider_name = "MangaBall"
    base_url = BASE_URL

    def __init__(self) -> None:
        self.config = Config()
        self.timeout = self.config.get("network.timeout", 30)
        self.retry_attempts = self.config.get("network.retry_attempts", 3)

        # requests.Session for CSRF-authenticated API calls
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;"
                      "q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._csrf: Optional[str] = None
        super().__init__()  # creates self.session (httpx) for image downloads

    # ──────────────────────────────────────────────────────────────────
    #  CSRF management
    # ──────────────────────────────────────────────────────────────────

    def _ensure_csrf(self) -> None:
        """Lazily fetch the CSRF token from the homepage."""
        if self._csrf:
            return
        self._init_csrf()

    def _init_csrf(self) -> None:
        logger.info("MangaBall: fetching CSRF token …")
        resp = self._session.get(BASE_URL, timeout=self.timeout)
        resp.raise_for_status()

        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")

        meta = soup.find("meta", {"name": "csrf-token"})
        if not meta or not meta.get("content"):
            raise ProviderError("CSRF token not found on MangaBall homepage")

        self._csrf = str(meta["content"])
        logger.info("MangaBall: CSRF acquired")

    def _api_headers(self) -> dict:
        return {
            "X-CSRF-Token": self._csrf or "",
            "Referer": f"{BASE_URL}/",
            "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

    def _api_post(self, url: str, data: dict) -> requests.Response:
        """POST with auto-CSRF-refresh on 403/419."""
        self._ensure_csrf()

        resp = self._session.post(
            url, headers=self._api_headers(), data=data, timeout=self.timeout
        )

        if resp.status_code in (403, 419) or "csrf" in resp.text.lower():
            logger.warning("MangaBall: CSRF expired — refreshing")
            self._init_csrf()
            resp = self._session.post(
                url, headers=self._api_headers(), data=data, timeout=self.timeout
            )

        resp.raise_for_status()
        return resp

    # ──────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_slug(url: str) -> str:
        """Extract slug from URL like /title-detail/nano-machine-252/."""
        parts = url.rstrip("/").split("/")
        return parts[-1] if parts else url

    @staticmethod
    def _extract_title_id(slug: str) -> str:
        """Extract title ID from slug: nano-machine-252 → 252,
        revenge-of-the-bloodhound-6851547b... → 6851547b..."""
        return slug.rsplit("-", 1)[-1] if "-" in slug else slug

    # ──────────────────────────────────────────────────────────────────
    #  Search
    # ──────────────────────────────────────────────────────────────────

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        logger.debug("MangaBall search: query='%s' page=%d", query, page)

        data = {
            "search_input": query,
            "filters[page]": str(page),
            "filters[sort]": "6",
        }
        try:
            resp = self._api_post(API_SEARCH, data)
            body = resp.json()
        except Exception as exc:
            logger.error("MangaBall search failed: %s", exc)
            raise ProviderError(f"Search failed: {exc}") from exc

        results: List[MangaSearchResult] = []
        hits = body.get("data", [])

        for item in hits:
            name = item.get("name", "")
            item_url = item.get("url", "")
            thumbnail = item.get("thumbnail", "")

            slug = self._extract_slug(item_url)
            manga_id = slug

            results.append(
                MangaSearchResult(
                    provider_id=self.provider_id,
                    manga_id=manga_id,
                    title=name,
                    cover_url=thumbnail,
                    url=f"{BASE_URL}/title-detail/{slug}/",
                )
            )

        has_next = len(hits) >= 20
        logger.info("MangaBall search returned %d results", len(results))
        return results, has_next

    # ──────────────────────────────────────────────────────────────────
    #  Manga info
    # ──────────────────────────────────────────────────────────────────

    def get_manga_info(
        self, manga_id: Optional[str] = None, url: Optional[str] = None
    ) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        if url:
            slug = self._extract_slug(url)
        else:
            slug = manga_id or ""

        page_url = f"{BASE_URL}/title-detail/{slug}/"
        logger.debug("MangaBall get_manga_info: %s", page_url)

        try:
            resp = self._session.get(page_url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            raise MangaNotFoundError(f"Manga not found: {slug}") from exc

        try:
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception:
            soup = BeautifulSoup(resp.text, "html.parser")

        title_el = soup.select_one("#comicDetail h6")
        title = title_el.get_text(strip=True) if title_el else slug

        desc_el = soup.select_one("#descriptionContent")
        description = desc_el.get_text(strip=True) if desc_el else ""

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=slug,
            title=title,
            alternative_titles=[],
            cover_url="",
            url=page_url,
            description=description,
            authors=[],
            artists=[],
            genres=[],
            status="Unknown",
            year=None,
        )

    # ──────────────────────────────────────────────────────────────────
    #  Chapters (multi-language with translations[])
    # ──────────────────────────────────────────────────────────────────

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """Fetch chapters from the MangaBall API.

        Each chapter has `translations[]` — a list of available
        languages. We pick the preferred language (English) and use
        that translation's ID as the chapter_id.
        """
        slug = manga_id
        title_id = self._extract_title_id(slug)
        logger.debug("MangaBall get_chapters: slug=%s title_id=%s", slug, title_id)

        try:
            resp = self._api_post(API_CHAPTERS, {"title_id": title_id})
            body = resp.json()
        except Exception as exc:
            raise ProviderError(f"Failed to fetch chapters: {exc}") from exc

        if "ALL_CHAPTERS" not in body:
            raise ProviderError(
                f"Unexpected chapter response format for {manga_id}"
            )

        raw_chapters = body["ALL_CHAPTERS"]
        chapters: List[Chapter] = []
        preferred_lang = self.config.get("providers.preferred_language", "en") or ""

        for ch in raw_chapters:
            ch_number = str(ch.get("number", ""))
            ch_title = ch.get("title", "") or f"Chapter {ch_number}"
            translations = ch.get("translations", [])

            if not translations:
                continue

            # Pick the preferred-language translation, or first available
            selected = None
            for tr in translations:
                if tr.get("language") == preferred_lang:
                    selected = tr
                    break

            if selected is None:
                selected = translations[0]

            tr_id = str(selected["id"])
            language = selected.get("language", "en")

            chapter_url = f"{BASE_URL}/chapter-detail/{tr_id}/"

            chapters.append(
                Chapter(
                    chapter_id=tr_id,
                    manga_id=manga_id,
                    title=ch_title,
                    chapter_number=ch_number,
                    volume=None,
                    url=chapter_url,
                    release_date=None,
                    language=language,
                )
            )

        chapters.sort(key=lambda c: c.sort_key)
        chapters.reverse()
        logger.info("MangaBall returned %d chapters for %s", len(chapters), manga_id)
        return chapters
        
    # ──────────────────────────────────────────────────────────────────
    #  Chapter images (inline JS parsing)
    # ──────────────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """Parse image URLs from inline JS on the chapter page.

        The page contains:
            const chapterImages = JSON.parse(`["url1","url2",...]`)
        """
        chapter_url = f"{BASE_URL}/chapter-detail/{chapter_id}/"
        logger.debug("MangaBall get_chapter_images: %s", chapter_url)

        try:
            resp = self._session.get(chapter_url, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as exc:
            raise ProviderError(f"Failed to load chapter page: {exc}") from exc

        # Extract const chapterImages = JSON.parse(`[...]`)
        match = re.search(
            r"const\s+chapterImages\s*=\s*JSON\.parse\(`([^`]+)`\)",
            resp.text,
        )
        if not match:
            logger.warning("chapterImages not found for chapter %s", chapter_id)
            return []

        try:
            image_urls: list = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse chapterImages JSON: %s", exc)
            return []

        image_urls = [u for u in image_urls if isinstance(u, str) and u.startswith("http")]
        logger.info("MangaBall chapter %s has %d images", chapter_id, len(image_urls))
        return image_urls

    # ──────────────────────────────────────────────────────────────────
    #  Headers
    # ──────────────────────────────────────────────────────────────────

    def get_headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE_URL,
        }
