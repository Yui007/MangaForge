"""
HarmonyScan provider for MangaForge.

WordPress Madara theme (French site). Uses AJAX with nonce for chapter
listing. No Cloudflare protection — plain httpx requests work fine.

Base URL: https://harmony-scan.fr
"""

import re
import logging
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from core.base_provider import BaseProvider, ProviderError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)

# AJAX endpoint for the Madara WordPress theme
AJAX_ENDPOINT = "/wp-admin/admin-ajax.php"


class HarmonyScanProvider(BaseProvider):
    """Provider for harmony-scan.fr (WordPress Madara theme, French).

    Chapters are fetched via Madara AJAX POST (action=manga_get_chapters)
    with a fallback to parsing direct HTML. No Cloudflare — plain httpx.
    """

    provider_id = "harmonyscan"
    provider_name = "HarmonyScan"
    base_url = "https://harmony-scan.fr"

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

    def _get_html(self, url: str) -> str:
        """Fetch a page and return raw HTML."""
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.text

    def _extract_manga_post_id(self, html: str) -> Optional[str]:
        """Extract the WordPress post ID for the manga.

        Tries multiple patterns common in Madara themes.
        """
        # Pattern 1: manga var in JS
        m = re.search(r'"manga_id"\s*:\s*"?(\d+)"?', html)
        if m:
            return m.group(1)
        # Pattern 2: data-id attribute
        m = re.search(r'data-id=["\'](\d+)["\']', html)
        if m:
            return m.group(1)
        # Pattern 3: body class postid-NNN
        m = re.search(r'postid-(\d+)', html)
        if m:
            return m.group(1)
        # Pattern 4: wp_manga_chapter_nonce field
        m = re.search(r'id=["\']manga-chapters-holder["\'][^>]*data-id=["\'](\d+)["\']', html)
        if m:
            return m.group(1)
        return None

    @staticmethod
    def _extract_chapter_number(title: str) -> str:
        """Extract chapter number from French/English title."""
        m = re.search(r"(?:chapitre|chapter)[- ]?([\d.]+)", title, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"([\d.]+)", title)
        if m:
            return m.group(1)
        return title

    # ──────────────────────────────────────────────────────────────────
    #  Search (Madara search page)
    # ──────────────────────────────────────────────────────────────────

    def search(
        self, query: str, page: int = 1
    ) -> Tuple[List[MangaSearchResult], bool]:
        url = f"{self.base_url}/page/{page}/?s={quote_plus(query)}&post_type=wp-manga"
        logger.info("HarmonyScan search: %s", url)

        soup = self._get_soup(url)
        results: List[MangaSearchResult] = []

        for item in soup.select("div.row.c-tabs-item__content"):
            title_link = item.select_one("div.post-title a")
            if not title_link:
                continue
            title = title_link.get_text(strip=True)
            manga_url = str(title_link["href"]).strip()

            # Cover
            img = item.select_one("img")
            cover = ""
            if img:
                cover = str(img.get("data-src") or img.get("src") or "")
                if "dflazy.jpg" in cover:
                    cover = str(img.get("data-src") or "")

            results.append(
                MangaSearchResult(
                    provider_id=self.provider_id,
                    manga_id=manga_url,
                    title=title,
                    url=manga_url,
                    cover_url=cover,
                )
            )

        has_next = bool(soup.select_one("a.next.page-numbers"))

        logger.info("HarmonyScan search returned %d results", len(results))
        return results, has_next

    # ──────────────────────────────────────────────────────────────────
    #  Manga info
    # ──────────────────────────────────────────────────────────────────

    def get_manga_info(
        self, manga_id: Optional[str] = None, url: Optional[str] = None
    ) -> MangaInfo:
        if not url and manga_id:
            url = manga_id if manga_id.startswith("http") else f"{self.base_url}/manga/{manga_id}/"
        if not url:
            raise ValueError("Must provide url or manga_id")

        logger.info("HarmonyScan get_manga_info: %s", url)
        soup = self._get_soup(url)

        # Title
        title_elem = soup.select_one(".post-title h1")
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"

        # Cover
        cover_elem = soup.select_one(".summary_image img")
        cover_url = ""
        if cover_elem:
            cover_url = str(cover_elem.get("data-src") or cover_elem.get("src") or "")
            if "dflazy.jpg" in cover_url:
                cover_url = str(cover_elem.get("data-src") or "")

        # Authors / Artists / Genres
        authors = [a.get_text(strip=True) for a in soup.select(".author-content a")]
        artists = [a.get_text(strip=True) for a in soup.select(".artist-content a")]
        genres = [a.get_text(strip=True) for a in soup.select(".genres-content a")]

        # Dynamic content items (status, alt names)
        status = "Unknown"
        alt_titles: List[str] = []
        for item in soup.select(".post-content_item"):
            heading = item.select_one(".summary-heading h5")
            content = item.select_one(".summary-content")
            if not heading or not content:
                continue
            label = heading.get_text(strip=True).lower()
            if any(k in label for k in ["status", "statut"]):
                status = content.get_text(strip=True)
            elif any(k in label for k in ["other name", "autre"]):
                raw = content.get_text(strip=True)
                alt_titles = [t.strip() for t in raw.split(",") if t.strip()]

        # Synopsis
        synopsis_elem = soup.select_one(".description-summary .summary__content p")
        description = synopsis_elem.get_text(strip=True) if synopsis_elem else ""

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=url,
            title=title,
            url=url,
            cover_url=cover_url,
            description=description,
            status=status,
            genres=genres,
            authors=authors,
            artists=artists,
            alternative_titles=alt_titles,
            year=None,
        )

    # ──────────────────────────────────────────────────────────────────
    #  Chapters
    # ──────────────────────────────────────────────────────────────────

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        url = manga_id if manga_id.startswith("http") else f"{self.base_url}/manga/{manga_id}/"
        # Ensure trailing slash for AJAX endpoint
        if not url.endswith("/"):
            url += "/"
        logger.info("HarmonyScan get_chapters: %s", url)

        # Step 1: Try the newer Madara AJAX endpoint: POST {manga_url}/ajax/chapters/
        chapters = self._fetch_chapters_ajax(url, manga_id)
        if chapters:
            chapters.reverse()  # Madara newest-first → oldest-first
            logger.info("HarmonyScan AJAX returned %d chapters", len(chapters))
            return chapters

        # Step 2: Fallback — fetch page HTML and parse chapters directly
        logger.info("AJAX failed, falling back to HTML parsing")
        html = self._get_html(url)
        chapters = self._parse_chapter_list(html, manga_id)
        chapters.reverse()

        logger.info("HarmonyScan returned %d chapters for %s", len(chapters), manga_id)
        return chapters

    def _fetch_chapters_ajax(
        self, manga_url: str, manga_id: str
    ) -> List[Chapter]:
        """Fetch chapter list via Madara AJAX endpoint.

        Newer Madara themes use POST {manga_url}/ajax/chapters/ instead
        of the old admin-ajax.php?action=manga_get_chapters pattern.
        """
        ajax_url = f"{manga_url}ajax/chapters/"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": manga_url,
        }

        logger.info("AJAX chapters: %s", ajax_url)
        try:
            resp = self.session.post(ajax_url, headers=headers)
        except Exception as e:
            logger.warning("AJAX request error: %s", e)
            return []

        if resp.status_code != 200:
            logger.warning("AJAX chapter request failed: %d", resp.status_code)
            return []

        return self._parse_chapter_list(resp.text, manga_id)

    def _parse_chapter_list(self, html: str, manga_id: str) -> List[Chapter]:
        """Parse chapter list items from HTML (works for both AJAX and page HTML)."""
        soup = BeautifulSoup(html, "html.parser")
        chapters: List[Chapter] = []

        for li in soup.select("li.wp-manga-chapter"):
            a = li.select_one("a")
            if not a:
                continue

            ch_title = a.get_text(strip=True)
            ch_url = str(a.get("href", "")).strip()
            if not ch_url:
                continue

            # Ensure absolute URL
            if not ch_url.startswith("http"):
                ch_url = f"{self.base_url}{ch_url}"

            ch_num = self._extract_chapter_number(ch_title)

            # Release date
            release_elem = li.select_one("span.chapter-release-date")
            release = None
            if release_elem:
                date_tag = release_elem.select_one("i")
                if date_tag:
                    release = date_tag.get_text(strip=True)
                else:
                    release = release_elem.get_text(strip=True)

            chapters.append(
                Chapter(
                    chapter_id=ch_url,
                    manga_id=manga_id,
                    title=ch_title,
                    chapter_number=ch_num,
                    volume=None,
                    url=ch_url,
                    release_date=release,
                    language="fr",
                )
            )

        return chapters

    # ──────────────────────────────────────────────────────────────────
    #  Chapter images
    # ──────────────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        logger.info("HarmonyScan get_chapter_images: %s", chapter_id)

        soup = self._get_soup(chapter_id)
        images: List[str] = []

        for img in soup.select(".wp-manga-chapter-img"):
            # Prefer data-src (actual image) over src (may be placeholder)
            src = str(img.get("data-src") or img.get("src") or "").strip()
            if src and not src.startswith("data:") and "dflazy.jpg" not in src:
                images.append(src)

        logger.info("HarmonyScan chapter has %d images", len(images))
        return images
