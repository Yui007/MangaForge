"""
MangaPill provider for MangaForge.

Pure HTML scraping with httpx + BeautifulSoup.
mangapill.com uses static HTML rendering — no JS or Cloudflare.
"""

import re
import logging
from typing import List, Optional, Tuple
from urllib.parse import urljoin, quote_plus

from bs4 import BeautifulSoup

from core.base_provider import BaseProvider, ProviderError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)


class MangaPillProvider(BaseProvider):
    """Provider for mangapill.com.

    Static HTML site — no API, no Cloudflare. Chapters and images are
    extracted directly from page HTML with CSS selectors.
    """

    provider_id = "mangapill"
    provider_name = "MangaPill"
    base_url = "https://mangapill.com"

    def __init__(self) -> None:
        self.config = Config()
        super().__init__()

    # ──────────────────────────────────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────────────────────────────────

    def _get_soup(self, url: str) -> BeautifulSoup:
        """Fetch a page and return parsed soup."""
        resp = self.session.get(url)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")

    @staticmethod
    def _extract_chapter_number(text: str) -> str:
        """Extract chapter number from title like 'Chapter 10.5'."""
        m = re.search(r"Chapter\s*([\d.]+)", text, re.IGNORECASE)
        return m.group(1) if m else text

    # ──────────────────────────────────────────────────────────────────
    #  Search
    # ──────────────────────────────────────────────────────────────────

    def search(
        self, query: str, page: int = 1
    ) -> Tuple[List[MangaSearchResult], bool]:
        url = f"{self.base_url}/search?q={quote_plus(query)}&page={page}"
        logger.info("MangaPill search: %s", url)

        soup = self._get_soup(url)
        results: List[MangaSearchResult] = []

        # Each result card is a div with a link wrapping an image + title
        for card in soup.select("div.lg\\:flex a[href*='/manga/']"):
            href = card.get("href", "")
            if not href or not str(href).startswith("/manga/"):
                continue

            manga_url = urljoin(self.base_url, str(href))

            # Title
            title_elem = card.select_one("div.leading-tight")
            title = title_elem.get_text(strip=True) if title_elem else "Unknown"

            # Cover
            img = card.select_one("img")
            cover = None
            if img:
                cover = str(img.get("data-src") or img.get("src") or "")

            results.append(
                MangaSearchResult(
                    provider_id=self.provider_id,
                    manga_id=manga_url,
                    title=title,
                    url=manga_url,
                    cover_url=cover or "",
                )
            )

        # Check for next page
        has_next = bool(soup.select_one(f"a[href*='page={page + 1}']"))

        logger.info("MangaPill search returned %d results", len(results))
        return results, has_next

    # ──────────────────────────────────────────────────────────────────
    #  Manga info
    # ──────────────────────────────────────────────────────────────────

    def get_manga_info(
        self, manga_id: Optional[str] = None, url: Optional[str] = None
    ) -> MangaInfo:
        if not url and manga_id:
            url = manga_id if manga_id.startswith("http") else f"{self.base_url}/manga/{manga_id}"
        if not url:
            raise ValueError("Must provide url or manga_id")

        logger.info("MangaPill get_manga_info: %s", url)
        soup = self._get_soup(url)

        # Title
        title_elem = soup.select_one("h1.font-bold")
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"

        # Cover
        cover_elem = soup.select_one("div img[src*='cover']")
        if not cover_elem:
            cover_elem = soup.select_one("div.w-60 img, figure img")
        cover_url = None
        if cover_elem:
            cover_url = str(cover_elem.get("data-src") or cover_elem.get("src") or "")

        # Description
        desc_elem = soup.select_one("p.text-sm.text--secondary")
        description = None
        if desc_elem:
            raw = desc_elem.decode_contents()
            if "<br><br>" in raw:
                raw = raw.split("<br><br>", 1)[1]
            clean = BeautifulSoup(raw, "html.parser")
            description = clean.get_text("\n", strip=True)

        # Info grid (Type, Status, Year)
        info_grid: dict[str, str] = {}
        for block in soup.select("div.grid > div"):
            label = block.select_one("label")
            value = label.find_next_sibling("div") if label else None
            if label and value:
                info_grid[label.get_text(strip=True)] = value.get_text(strip=True)

        # Genres
        genres = [
            a.get_text(strip=True)
            for a in soup.select("a[href^='/search?genre=']")
        ]

        # Status
        status = info_grid.get("Status", "Unknown")

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=url,
            title=title,
            url=url,
            cover_url=cover_url or "",
            description=description or "",
            status=status,
            genres=genres,
            authors=[],
            artists=[],
            alternative_titles=[],
            year=None,
        )

    # ──────────────────────────────────────────────────────────────────
    #  Chapters
    # ──────────────────────────────────────────────────────────────────

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        url = manga_id if manga_id.startswith("http") else f"{self.base_url}/manga/{manga_id}"
        logger.info("MangaPill get_chapters: %s", url)

        soup = self._get_soup(url)
        chapters: List[Chapter] = []

        for a in soup.select("#chapters a[href^='/chapters/']"):
            ch_title = a.get_text(strip=True)
            ch_url = urljoin(self.base_url, str(a["href"]))
            ch_num = self._extract_chapter_number(ch_title)

            chapters.append(
                Chapter(
                    chapter_id=ch_url,
                    manga_id=manga_id,
                    title=ch_title,
                    chapter_number=ch_num,
                    volume=None,
                    url=ch_url,
                    release_date=None,
                    language="en",
                )
            )

        # Site lists newest first, reverse so chapter 1 is first
        chapters.reverse()

        logger.info("MangaPill returned %d chapters", len(chapters))
        return chapters

    # ──────────────────────────────────────────────────────────────────
    #  Chapter images
    # ──────────────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        url = chapter_id  # chapter_id is the full URL
        logger.info("MangaPill get_chapter_images: %s", url)

        soup = self._get_soup(url)
        images: List[str] = []

        for img in soup.select("img.js-page"):
            src = img.get("data-src") or img.get("src")
            if src:
                images.append(str(src).strip())

        logger.info("MangaPill chapter has %d images", len(images))
        return images
