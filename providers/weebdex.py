"""
WeebDex provider for MangaForge.

Uses the public JSON API at https://api.weebdex.org.
Chapter image URLs are **constructed** from a node base URL + chapter ID + filename.

API endpoints:
    GET /manga/{id}                     → manga info
    GET /manga/{id}/chapters?limit=500  → chapter list
    GET /chapter/{id}                   → chapter image data
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)

API_BASE = "https://api.weebdex.org"


class WeebDexProvider(BaseProvider):
    """Provider for weebdex.org.

    Fully API-driven — no HTML scraping.
    """

    provider_id = "weebdex"
    provider_name = "WeebDex"
    base_url = "https://weebdex.org"

    def __init__(self) -> None:
        self.config = Config()
        self.timeout = self.config.get("network.timeout", 30)
        self.retry_attempts = self.config.get("network.retry_attempts", 3)
        self._client: Optional[httpx.Client] = None
        super().__init__()  # creates self.session for image downloads

    # ──────────────────────────────────────────────────────────────────
    #  HTTP client
    # ──────────────────────────────────────────────────────────────────

    @property
    def client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={
                    "User-Agent": "MangaForge/1.0",
                    "Accept": "application/json",
                },
            )
        return self._client

    def _api_get(
        self, path: str, params: Optional[dict] = None
    ) -> Dict[str, Any]:
        """GET with retry logic."""
        url = f"{API_BASE}/{path.lstrip('/')}"
        last_exc: Optional[Exception] = None
        retry_delays = [2, 4, 6]

        for attempt in range(self.retry_attempts):
            try:
                resp = self.client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                code = exc.response.status_code
                logger.warning("WeebDex %s → HTTP %d (attempt %d)", url, code, attempt + 1)
                if 400 <= code < 500 and code != 429:
                    raise ProviderError(f"API error {code}: {url}") from exc
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning("WeebDex request error: %s", exc)

            if attempt < self.retry_attempts - 1:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                time.sleep(delay)

        raise ProviderError(
            f"WeebDex API failed after {self.retry_attempts} attempts"
        ) from last_exc

    # ──────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────

    _URL_RE = re.compile(
        r"(?:https?://)?(?:www\.)?weebdex\.org/title/([a-zA-Z0-9]+)"
    )

    @classmethod
    def _extract_id(cls, url_or_id: str) -> str:
        """Extract manga ID from URL or return as-is."""
        m = cls._URL_RE.search(url_or_id)
        return m.group(1) if m else url_or_id.rstrip("/").split("/")[-1]

    # ──────────────────────────────────────────────────────────────────
    #  Search (not supported by API)
    # ──────────────────────────────────────────────────────────────────

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        logger.debug("WeebDex search not supported by API (query=%s)", query)
        return [], False

    # ──────────────────────────────────────────────────────────────────
    #  Manga info
    # ──────────────────────────────────────────────────────────────────

    def get_manga_info(
        self, manga_id: Optional[str] = None, url: Optional[str] = None
    ) -> MangaInfo:
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        mid = self._extract_id(url or manga_id or "")
        logger.debug("WeebDex get_manga_info: id=%s", mid)

        data = self._api_get(f"manga/{mid}")
        relationships = data.get("relationships", {})

        title = data.get("title", "Unknown")

        # Alt titles
        alt_map = data.get("alt_titles", {})
        alt_titles: List[str] = []
        if isinstance(alt_map, dict):
            for lang_list in alt_map.values():
                if isinstance(lang_list, list):
                    alt_titles.extend(lang_list)
        elif isinstance(alt_map, list):
            alt_titles = alt_map

        # Cover URL
        cover_url = ""
        cover_data = relationships.get("cover")
        if cover_data:
            cid = cover_data.get("id", "")
            ext = cover_data.get("ext", "")
            cover_url = f"https://srv.notdelta.xyz/covers/{mid}/{cid}{ext}"

        # Authors / Artists
        authors = [a["name"] for a in relationships.get("authors", []) if "name" in a]
        artists = [a["name"] for a in relationships.get("artists", []) if "name" in a]

        # Tags → genres
        genres = [
            t["name"]
            for t in relationships.get("tags", [])
            if t.get("group") == "genre" and "name" in t
        ]

        status_raw = data.get("status", "")
        status = status_raw.capitalize() if isinstance(status_raw, str) and status_raw else "Unknown"

        year = data.get("year")
        try:
            year = int(year) if year else None
        except (ValueError, TypeError):
            year = None

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=mid,
            title=title,
            alternative_titles=alt_titles,
            cover_url=cover_url,
            url=f"{self.base_url}/title/{mid}",
            description=data.get("description", ""),
            authors=authors,
            artists=artists or authors,
            genres=genres,
            status=status,
            year=year,
        )

    # ──────────────────────────────────────────────────────────────────
    #  Chapters
    # ──────────────────────────────────────────────────────────────────

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        mid = self._extract_id(manga_id)
        logger.debug("WeebDex get_chapters: id=%s", mid)

        preferred_lang = self.config.get("providers.preferred_language", "en") or ""
        preferred_scan = self.config.get("providers.preferred_scanlator", "") or ""

        data = self._api_get(
            f"manga/{mid}/chapters", params={"limit": 500, "order": "desc"}
        )

        all_chapters: List[dict] = data.get("data", [])

        # --- Filter by language (skip if empty) ---
        if preferred_lang:
            lang_filtered = [
                ch for ch in all_chapters
                if ch.get("language", "en") == preferred_lang
            ]
            if not lang_filtered:
                logger.warning(
                    "WeebDex: no chapters in '%s', showing all languages", preferred_lang
                )
                lang_filtered = all_chapters
        else:
            lang_filtered = all_chapters

        # --- Build chapter list, preferring scanlator when duplicates ---
        # Group by chapter number so we can pick the preferred scanlator
        from collections import defaultdict
        by_number: dict[str, list[dict]] = defaultdict(list)
        for ch in lang_filtered:
            by_number[ch.get("chapter", "0")].append(ch)

        chapters: List[Chapter] = []
        for ch_num, entries in by_number.items():
            selected = entries[0]  # default: first entry

            if preferred_scan and len(entries) > 1:
                # Try to find the preferred scanlation group
                for entry in entries:
                    groups = entry.get("relationships", {}).get("groups", [])
                    group_names = [g["name"] for g in groups if "name" in g]
                    if preferred_scan.lower() in [g.lower() for g in group_names]:
                        selected = entry
                        break

            chapter_id = selected.get("id", "")
            volume = selected.get("volume") or None
            language = selected.get("language", "en")
            published = selected.get("published_at", "")

            groups = selected.get("relationships", {}).get("groups", [])
            group_names = [g["name"] for g in groups if "name" in g]
            group_str = ", ".join(group_names) if group_names else ""

            ch_title = f"Chapter {ch_num}"
            if group_str:
                ch_title += f" [{group_str}]"

            chapters.append(
                Chapter(
                    chapter_id=chapter_id,
                    manga_id=manga_id,
                    title=ch_title,
                    chapter_number=str(ch_num),
                    volume=volume,
                    url=f"{self.base_url}/chapter/{chapter_id}",
                    release_date=published or None,
                    language=language,
                )
            )

        chapters.sort(key=lambda c: c.sort_key)
        logger.info("WeebDex returned %d chapters for %s", len(chapters), mid)
        return chapters

    # ──────────────────────────────────────────────────────────────────
    #  Chapter images (constructed URLs)
    # ──────────────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """Fetch image metadata and construct full URLs.

        Response shape:
            {
                "id": "...",
                "node": "https://srv.notdelta.xyz",
                "data": [{"name": "1.png", ...}, ...],
                "data_optimized": [...]
            }

        Image URL = {node}/data/{chapter_id}/{name}
        """
        logger.debug("WeebDex get_chapter_images: id=%s", chapter_id)

        data = self._api_get(f"chapter/{chapter_id}")
        node = data.get("node", "")
        images = data.get("data", [])

        if not node or not images:
            logger.warning("WeebDex: no images for chapter %s", chapter_id)
            return []

        urls = [
            f"{node}/data/{data['id']}/{img['name']}"
            for img in images
            if "name" in img
        ]

        logger.info("WeebDex chapter %s has %d images", chapter_id, len(urls))
        return urls

    # ──────────────────────────────────────────────────────────────────
    #  Headers
    # ──────────────────────────────────────────────────────────────────

    def get_headers(self) -> dict:
        return {
            "User-Agent": "MangaForge/1.0",
            "Accept": "application/json",
            "Referer": self.base_url,
        }
