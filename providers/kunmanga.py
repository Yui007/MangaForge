"""
KunManga provider for MangaForge.

WordPress Madara theme — uses FlareSolverr to bypass Cloudflare,
then plain requests with the obtained cookies for chapters and images.

    FlareSolverr running at http://localhost:8191
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

import requests as plain_requests  # FlareSolverr communication (localhost)
from bs4 import BeautifulSoup

from core.base_provider import BaseProvider, ProviderError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)

# FlareSolverr defaults
DEFAULT_FLARESOLVERR_URL = "http://localhost:8191/v1"
FLARESOLVERR_SESSION_ID = "kunmanga_session"


class KunMangaProvider(BaseProvider):
    """Provider for kunmanga.com (WordPress Madara theme).

    Uses FlareSolverr for the initial Cloudflare challenge, then
    a plain requests.Session with the obtained cookies for all
    subsequent requests (chapter pages, images).
    """

    provider_id = "kunmanga"
    provider_name = "KunManga"
    base_url = "https://kunmanga.com"

    def __init__(self) -> None:
        self.config = Config()
        self.flaresolverr_url = self.config.get(
            "network.flaresolverr_url", DEFAULT_FLARESOLVERR_URL
        )
        super().__init__()

        # Plain requests session — populated after FlareSolverr solve
        self._plain_session: Optional[plain_requests.Session] = None
        self._solved: bool = False

    # ──────────────────────────────────────────────────────────────────
    #  FlareSolverr integration
    # ──────────────────────────────────────────────────────────────────

    def _ensure_solved(self) -> None:
        """Lazily solve Cloudflare challenge if not done yet."""
        if self._solved and self._plain_session is not None:
            return
        self._flaresolverr_solve(self.base_url)
        self._solved = True

    def _flaresolverr_solve(self, url: str) -> str:
        """Send a request through FlareSolverr, return HTML and build session."""
        logger.info("[FlareSolverr] Solving: %s", url)
        payload = {
            "cmd": "request.get",
            "url": url,
            "session": FLARESOLVERR_SESSION_ID,
            "maxTimeout": 60000,
        }
        try:
            resp = plain_requests.post(
                self.flaresolverr_url, json=payload, timeout=120
            )
            resp.raise_for_status()
        except plain_requests.exceptions.ConnectionError as exc:
            raise ProviderError(
                f"Cannot connect to FlareSolverr at {self.flaresolverr_url}. "
                "Start it with: docker run -d -p 8191:8191 "
                "ghcr.io/flaresolverr/flaresolverr:latest"
            ) from exc

        data = resp.json()
        if data.get("status") != "ok":
            raise ProviderError(
                f"FlareSolverr error: {data.get('message', data)}"
            )

        solution = data["solution"]
        html = solution.get("response", "")
        user_agent = solution.get("userAgent", "")
        cookies = {c["name"]: c["value"] for c in solution.get("cookies", [])}

        logger.info(
            "[FlareSolverr] Solved! cookies=%d UA=%s...",
            len(cookies), user_agent[:60]
        )

        # Build plain session with obtained cookies
        self._plain_session = plain_requests.Session()
        self._plain_session.cookies.update(cookies)
        self._plain_session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": f"{self.base_url}/",
            "Connection": "keep-alive",
        })

        return html

    def _flaresolverr_get(self, url: str) -> str:
        """Fetch a URL via FlareSolverr (for initial/search pages)."""
        logger.info("[FlareSolverr] GET %s", url)
        payload = {
            "cmd": "request.get",
            "url": url,
            "session": FLARESOLVERR_SESSION_ID,
            "maxTimeout": 60000,
        }
        resp = plain_requests.post(
            self.flaresolverr_url, json=payload, timeout=120
        )
        data = resp.json()
        if data.get("status") != "ok":
            raise ProviderError(
                f"FlareSolverr error: {data.get('message', data)}"
            )
        return data["solution"].get("response", "")

    def _plain_get(self, url: str) -> str:
        """Fetch a page using the cookie-loaded plain session."""
        if not self._plain_session:
            self._ensure_solved()
        resp = self._plain_session.get(url, timeout=30)  # type: ignore[union-attr]
        resp.raise_for_status()
        return resp.text

    # ──────────────────────────────────────────────────────────────────
    #  Parsers (Madara theme selectors)
    # ──────────────────────────────────────────────────────────────────

    def _parse_manga_info_from_html(self, html: str, url: str) -> MangaInfo:
        """Parse manga metadata from the manga page HTML."""
        soup = BeautifulSoup(html, "html.parser")

        # Title
        t = soup.select_one("div.post-title h1")
        title = t.get_text(strip=True) if t else "Unknown"

        # Cover image
        img = soup.select_one("div.summary_image img")
        cover = ""
        if img:
            raw_src = img.get("src")
            cover = str(raw_src).strip() if raw_src else ""

        # Structured info from post-content items
        authors, artists, genres = [], [], []
        status = "Unknown"
        alt_titles = []

        for item in soup.select("div.post-content_item"):
            heading = item.select_one("h5")
            content = item.select_one("div.summary-content")
            if not heading or not content:
                continue
            label = heading.get_text(strip=True).lower()

            if "alternative" in label:
                raw = content.get_text(strip=True)
                alt_titles = [t.strip() for t in raw.split(",") if t.strip()]
            elif "author" in label:
                authors = [a.get_text(strip=True) for a in content.select("a")]
            elif "artist" in label:
                artists = [a.get_text(strip=True) for a in content.select("a")]
            elif "genre" in label:
                genres = [a.get_text(strip=True) for a in content.select("a")]
            elif "status" in label:
                status = content.get_text(strip=True)

        # Synopsis
        desc_elem = soup.select_one(".description-summary .summary__content p")
        if not desc_elem:
            desc_elem = soup.select_one(".description-summary .summary__content")
        description = desc_elem.get_text(strip=True) if desc_elem else None

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=url,
            title=title,
            url=url,
            cover_url=cover,
            description=description or "",
            status=status,
            genres=genres,
            authors=authors,
            artists=artists,
            alternative_titles=alt_titles,
            year=None,
        )

    @staticmethod
    def _parse_chapters_from_html(html: str, manga_id: str) -> List[Chapter]:
        """Parse chapter list from the manga page HTML."""
        soup = BeautifulSoup(html, "html.parser")
        chapters: List[Chapter] = []

        for li in soup.select("ul.main.version-chap li.wp-manga-chapter"):
            a = li.select_one("a")
            if not a:
                continue

            ch_title = a.get_text(strip=True)
            ch_url = str(a["href"]).strip()

            # Ensure absolute URL
            if not ch_url.startswith("http"):
                ch_url = f"https://kunmanga.com{ch_url}"

            # Extract chapter number
            m = re.search(r"Chapter\s*([\d.]+)", ch_title, re.IGNORECASE)
            ch_num = m.group(1) if m else ch_title

            # Release date
            date_tag = li.select_one("span.chapter-release-date i")
            new_tag = li.select_one("span.chapter-release-date a.c-new-tag")
            if date_tag:
                release = date_tag.get_text(strip=True)
            elif new_tag:
                release = str(new_tag.get("title", "New")).strip() or "New"
            else:
                release = None

            chapters.append(
                Chapter(
                    chapter_id=ch_url,
                    manga_id=manga_id,
                    title=ch_title,
                    chapter_number=ch_num,
                    volume=None,
                    url=ch_url,
                    release_date=release,
                    language="en",
                )
            )

        return chapters

    # ──────────────────────────────────────────────────────────────────
    #  Search
    # ──────────────────────────────────────────────────────────────────

    def search(
        self, query: str, page: int = 1
    ) -> Tuple[List[MangaSearchResult], bool]:
        self._ensure_solved()

        # Madara search URL
        url = f"{self.base_url}/page/{page}/?s={query.replace(' ', '+')}&post_type=wp-manga"
        logger.info("KunManga search: %s", url)

        try:
            html = self._flaresolverr_get(url)
        except Exception:
            html = self._plain_get(url)

        soup = BeautifulSoup(html, "html.parser")
        results: List[MangaSearchResult] = []

        for item in soup.select("div.row.c-tabs-item__content"):
            # Title & URL
            title_link = item.select_one("div.post-title a")
            if not title_link:
                continue
            title = title_link.get_text(strip=True)
            manga_url = str(title_link["href"]).strip()

            # Cover
            img = item.select_one("img")
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

        # Check next page
        has_next = bool(soup.select_one("a.next.page-numbers"))

        logger.info("KunManga search returned %d results", len(results))
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

        self._ensure_solved()
        logger.info("KunManga get_manga_info: %s", url)

        # Use FlareSolverr for the main manga page
        html = self._flaresolverr_get(url)
        return self._parse_manga_info_from_html(html, url)

    # ──────────────────────────────────────────────────────────────────
    #  Chapters
    # ──────────────────────────────────────────────────────────────────

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        url = manga_id if manga_id.startswith("http") else f"{self.base_url}/manga/{manga_id}/"
        self._ensure_solved()
        logger.info("KunManga get_chapters: %s", url)

        # Use FlareSolverr for the manga page (has chapter list in HTML)
        html = self._flaresolverr_get(url)
        chapters = self._parse_chapters_from_html(html, manga_id)

        # Madara lists newest first — reverse so chapter 1 comes first
        chapters.reverse()

        logger.info("KunManga returned %d chapters for %s", len(chapters), manga_id)
        return chapters

    # ──────────────────────────────────────────────────────────────────
    #  Chapter images
    # ──────────────────────────────────────────────────────────────────

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """Fetch chapter page with plain requests (cookies bypass CF)."""
        self._ensure_solved()
        logger.info("KunManga get_chapter_images: %s", chapter_id)

        html = self._plain_get(chapter_id)
        soup = BeautifulSoup(html, "html.parser")

        images: List[str] = []
        for img in soup.select("div.page-break img.wp-manga-chapter-img"):
            src = str(img.get("src", "")).strip()
            if src:
                images.append(src)

        logger.info("KunManga chapter has %d images", len(images))
        return images

    # ──────────────────────────────────────────────────────────────────
    #  Image download (override for custom referer)
    # ──────────────────────────────────────────────────────────────────

    def download_image(self, url: str) -> bytes:
        """Download image using the cookie-loaded plain session."""
        try:
            if not self._plain_session:
                self._ensure_solved()

            resp = self._plain_session.get(  # type: ignore[union-attr]
                url, timeout=30,
                headers={"Referer": f"{self.base_url}/"}
            )
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.error("KunManga image download failed: %s — %s", url, e)
            raise ProviderError(f"Failed to download image: {e}")
