"""
Comix.to provider for MangaForge.

Fully API-based provider using the comix.to v2 JSON API.
Chapters may have multiple scanlation groups (different translations
of the same chapter number). The group name is embedded in the chapter
title so users can distinguish between them.

API base: https://comix.to/api/v2
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import requests

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)

API_BASE = "https://comix.to/api/v2"


class ComixProvider(BaseProvider):
    """Provider for comix.to manga website.

    Uses the public JSON API — no scraping or Cloudflare bypass needed.
    """

    provider_id = "comix"
    provider_name = "Comix"
    base_url = "https://comix.to"

    def __init__(self) -> None:
        self.config = Config()
        self.timeout = self.config.get("network.timeout", 30)
        self.retry_attempts = self.config.get("network.retry_attempts", 3)
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
        # Strip to last path segment
        segment = url_or_id.rstrip("/").split("/")[-1]
        # Code is the part before the first hyphen
        code = segment.split("-")[0]
        return code

    def _api_get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """GET from the Comix API with retry logic."""
        url = f"{API_BASE}/{endpoint.lstrip('/')}"
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Comix API %s attempt %d/%d failed: %s",
                    url, attempt, self.retry_attempts, exc,
                )

        raise ProviderError(
            f"Comix API request failed after {self.retry_attempts} attempts: {url}"
        ) from last_exc

    # ──────────────────────────────────────────────────────────────────
    #  Search
    # ──────────────────────────────────────────────────────────────────

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        """Search is not directly supported by the Comix API.

        For now, return empty results — users should use URL-based lookup.
        """
        logger.debug("Comix search called (query=%s) — not supported by API", query)
        return [], False

    # ──────────────────────────────────────────────────────────────────
    #  Manga info
    # ──────────────────────────────────────────────────────────────────

    def get_manga_info(
        self, manga_id: Optional[str] = None, url: Optional[str] = None
    ) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        code = self._extract_manga_code(url or manga_id or "")
        logger.debug("Comix get_manga_info code=%s", code)

        raw = self._api_get(f"manga/{code}/")
        data = raw.get("result", raw)

        title = data.get("title", "Unknown")
        alt_titles = data.get("alt_titles", [])
        if isinstance(alt_titles, str):
            alt_titles = [alt_titles]

        poster = data.get("poster", {})
        cover_url = poster.get("large") or poster.get("medium") or poster.get("small") or ""

        status_raw = data.get("status", "")
        if isinstance(status_raw, str):
            status = status_raw.capitalize() if status_raw else "Unknown"
        else:
            status = "Unknown"

        year = data.get("year")
        if year is not None:
            try:
                year = int(year)
            except (ValueError, TypeError):
                year = None

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=code,
            title=title,
            alternative_titles=alt_titles if isinstance(alt_titles, list) else [],
            cover_url=cover_url,
            url=f"{self.base_url}/title/{data.get('slug', code)}",
            description=data.get("synopsis", ""),
            authors=[],
            artists=[],
            genres=[],
            status=status,
            year=year,
        )

    # ──────────────────────────────────────────────────────────────────
    #  Chapters (paginated API, with scanlation groups)
    # ──────────────────────────────────────────────────────────────────

    def _fetch_chapter_page(self, code: str, page: int) -> Tuple[int, list]:
        """Fetch a single page of chapters from the API."""
        try:
            data = self._api_get(
                f"manga/{code}/chapters",
                params={"limit": 100, "page": page, "order[number]": "asc"},
            )
            items = data.get("result", {}).get("items", [])
            return page, items
        except Exception as exc:
            logger.warning("Comix chapter page %d failed: %s", page, exc)
            return page, []

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """Fetch all chapters using parallel page requests.

        Each chapter from the API may have a different scanlation_group.
        The same chapter number can appear multiple times with different
        translators — we deduplicate by chapter number, preferring the
        scanlator set in config (if any).
        """
        code = self._extract_manga_code(manga_id)
        preferred_scan = self.config.get("providers.preferred_scanlator", "")
        logger.debug("Comix get_chapters code=%s preferred_scan=%s", code, preferred_scan)

        all_items: Dict[int, list] = {}
        batch_size = 10
        batch_start = 1
        found_empty = False

        while not found_empty:
            pages = range(batch_start, batch_start + batch_size)
            with ThreadPoolExecutor(max_workers=batch_size) as pool:
                futures = {
                    pool.submit(self._fetch_chapter_page, code, p): p
                    for p in pages
                }
                for future in as_completed(futures):
                    page_num, items = future.result()
                    if items:
                        all_items[page_num] = items
                    else:
                        found_empty = True
            batch_start += batch_size

        # Flatten all items and group by chapter number
        from collections import defaultdict
        by_number: dict[str, list[dict]] = defaultdict(list)
        for page_num in sorted(all_items.keys()):
            for ch in all_items[page_num]:
                number = str(ch.get("number", ""))
                by_number[number].append(ch)

        chapters: List[Chapter] = []
        for number, entries in by_number.items():
            selected = entries[0]  # default

            if preferred_scan and len(entries) > 1:
                for entry in entries:
                    group = entry.get("scanlation_group")
                    group_name = group["name"] if group else ""
                    if group_name and preferred_scan.lower() == group_name.lower():
                        selected = entry
                        break

            chapter_id = str(selected["chapter_id"])
            ch_title = selected.get("name") or selected.get("title") or ""

            group = selected.get("scanlation_group")
            is_official = selected.get("is_official", 0)
            if group:
                group_name = group["name"]
            elif is_official:
                group_name = "Official"
            else:
                group_name = None

            display_title = f"Chapter {number}"
            if ch_title:
                display_title += f": {ch_title}"
            if group_name:
                display_title += f" [{group_name}]"

            chapter_url = f"{self.base_url}/title/{code}/{chapter_id}-chapter-{number}"

            chapters.append(
                Chapter(
                    chapter_id=chapter_id,
                    manga_id=manga_id,
                    title=display_title,
                    chapter_number=number,
                    volume=selected.get("volume"),
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
        """Fetch image URLs from the chapters API endpoint."""
        logger.debug("Comix get_chapter_images id=%s", chapter_id)

        data = self._api_get(f"chapters/{chapter_id}/")
        images = data.get("result", {}).get("images", [])
        urls = [img["url"] for img in images if "url" in img]

        logger.info("Comix chapter %s has %d images", chapter_id, len(urls))
        return urls

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
