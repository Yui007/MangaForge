import os
import re
import json
import time
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from core.base_provider import BaseProvider, ProviderError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

try:
    from playwright.sync_api import sync_playwright # type: ignore
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    sync_playwright = None # type: ignore
    PLAYWRIGHT_AVAILABLE = False


class MangaFireProvider(BaseProvider):
    provider_id = "mangafire"
    provider_name = "MangaFire"
    base_url = "https://mangafire.to"

    def __init__(self):
        self.config = Config()
        super().__init__()
        self._playwright = None
        self._browser = None
        self._context = None

    def get_headers(self) -> dict:
        headers = super().get_headers()
        headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": f"{self.base_url}/",
            "Accept-Language": "en-US,en;q=0.9",
        })
        return headers

    def _ensure_browser(self):
        if not PLAYWRIGHT_AVAILABLE:
            raise ProviderError("Playwright is not installed. Run 'pip install playwright' and 'playwright install chromium'")
            
        if not self._context:
            self._playwright = sync_playwright().start() # type: ignore
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            self._context = self._browser.new_context(
                user_agent=self.get_headers()["User-Agent"],
                viewport={"width": 1280, "height": 800},
                ignore_https_errors=True
            )

    def _close_browser(self):
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def _get_search_vrf(self, query: str) -> Optional[str]:
        self._ensure_browser()
        assert self._context is not None
        page = self._context.new_page()
        captured_vrf = None

        def on_request(req):
            nonlocal captured_vrf
            url = req.url
            if "mangafire.to" in url and "ajax/manga/search" in url and "vrf=" in url:
                val = parse_qs(urlparse(url).query).get("vrf", [None])[0]
                if val:
                    captured_vrf = val

        page.on("request", on_request)

        try:
            page.goto(f"{self.base_url}/home", wait_until="networkidle", timeout=30000)
            search_input = page.locator(".search-inner input[name=keyword]")
            search_input.fill(query)
            search_input.press("Enter")
            
            for _ in range(20):
                if captured_vrf:
                    break
                page.wait_for_timeout(500)
        except Exception as e:
            pass
        finally:
            page.close()

        return captured_vrf

    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        params = {
            "keyword": query,
            "language[]": "en",
            "page": page,
            "sort": "most_relevance",
        }

        if query.strip():
            vrf = self._get_search_vrf(query.strip())
            if vrf:
                params["vrf"] = vrf

        resp = self.session.get(f"{self.base_url}/filter", params=params)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        results = []
        for item in soup.select(".original.card-lg .unit .inner"):
            link = item.select_one(".info > a")
            if not link:
                continue
            href_val = link.get("href", "")
            href_str = str(href_val) if href_val else ""
            title = link.get_text(strip=True)
            mid = href_str.split(".")[-1] if "." in href_str else href_str.split("/")[-1]
            img = item.select_one("img")
            slug = href_str.lstrip("/manga/") if href_str.startswith("/manga/") else href_str

            results.append(MangaSearchResult(
                provider_id=self.provider_id,
                manga_id=slug,
                title=title,
                cover_url=str(img["src"]) if img else "",
                url=f"{self.base_url}/manga/{slug}"
            ))

        # Check pagination
        has_next = bool(soup.select_one(".pagination .page-item a[rel='next']"))
        return results, has_next

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not manga_id and url:
            manga_id = url.split("/manga/")[-1]
            
        if not manga_id:
            raise ValueError("Must provide manga_id or url")

        slug = manga_id.lstrip("/manga/")
        resp = self.session.get(f"{self.base_url}/manga/{slug}")
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        main = soup.select_one(".main-inner:not(.manga-bottom)")
        if not main:
            raise ProviderError(f"Manga not found: {slug}")

        h1 = main.select_one("h1")
        title = h1.get_text(strip=True) if h1 else "Unknown"
        
        poster_img = main.select_one(".poster img")
        thumbnail = poster_img.get("src") if poster_img else ""
        
        status_el = main.select_one(".info > p")
        status = status_el.get_text(strip=True) if status_el else "Unknown"
        
        alt_el = main.select_one("h6")
        alt_title = alt_el.get_text(strip=True) if alt_el else ""
        alt_titles = [alt_title] if alt_title else []

        synopsis_el = soup.select_one("#synopsis .modal-content")
        description = synopsis_el.get_text(strip=True) if synopsis_el else ""

        author = ""
        genres = []
        meta = main.select_one(".meta")
        if meta:
            for span in meta.select("span"):
                txt = span.get_text()
                nxt = span.find_next_sibling("span")
                if "Author" in txt and nxt:
                    author = nxt.get_text(strip=True)
                if "Genres" in txt and nxt:
                    genres = [g.strip() for g in nxt.get_text().split(",")]

        authors = [author] if author else []

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=slug,
            title=title,
            alternative_titles=alt_titles,
            cover_url=str(thumbnail) if thumbnail else "",
            url=f"{self.base_url}/manga/{slug}",
            description=description,
            authors=authors,
            artists=[],
            genres=genres,
            status=status,
            year=None
        )

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        slug = manga_id.lstrip("/manga/")
        numeric_id = slug.split(".")[-1]
        
        pref_lang = self.config.get("providers.preferred_language", "en")
        if not pref_lang:
            pref_lang = "en"
        pref_lang = pref_lang.lower()
            
        url = f"{self.base_url}/ajax/manga/{numeric_id}/chapter/{pref_lang}"

        resp = self.session.get(url)
        resp.raise_for_status()
        data = resp.json()
        
        if "result" not in data:
            return []

        soup = BeautifulSoup(data["result"], "html.parser")
        chapters = []

        for item in soup.select("li"):
            link = item.select_one("a")
            if not link:
                continue
            href = link.get("href", "")
            number = item.get("data-number", "0")
            spans = item.select("span")
            name = spans[0].get_text(strip=True) if spans else ""
            date = spans[1].get_text(strip=True) if len(spans) > 1 else None

            # Clean name to avoid "Chapter X - Chapter X" duplication
            clean_name = name
            if name.lower().startswith(f"chapter {number}".lower()):
                clean_name = name[len(f"chapter {number}"):].lstrip(" -:")
            if clean_name.strip() == "":
                clean_name = ""

            ch_url = str(href) if str(href).startswith("http") else f"{self.base_url}{href}"
            
            chapters.append(Chapter(
                chapter_id=ch_url,
                manga_id=manga_id,
                title=clean_name,
                chapter_number=str(number),
                volume=None,
                url=ch_url,
                release_date=date,
                language="en"
            ))

        # MangaFire returns newest first. BaseProvider expects oldest first
        chapters.reverse()
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        # 'chapter_id' is the URL of the chapter
        full_url = chapter_id
        
        self._ensure_browser()
        assert self._context is not None
        page = self._context.new_page()
        captured_url = None

        def on_request(req):
            nonlocal captured_url
            url = req.url
            if "mangafire.to" in url and "ajax/read" in url:
                if "ajax/read/chapter" in url or "ajax/read/volume" in url:
                    captured_url = url

        page.on("request", on_request)

        try:
            page.goto(full_url, wait_until="networkidle", timeout=30000)
            for _ in range(20):
                if captured_url:
                    break
                page.wait_for_timeout(500)
        except Exception as e:
            pass
        finally:
            page.close()

        if not captured_url:
            raise ProviderError("Could not capture VRF-signed URL for chapter.")

        # Fetch the already-authenticated ajax URL directly
        resp = self.session.get(captured_url)
        resp.raise_for_status()
        data = resp.json()
        
        # MangaFire structures its json as `{"result": {"images": [ [url, v, offset], ... ]}}`
        images = data.get("result", {}).get("images", [])
        
        result = []
        for img in images:
            if not img or not isinstance(img, list):
                continue
            
            img_url = img[0]
            offset = img[2] if len(img) > 2 else 0
            scrambled = isinstance(offset, int) and offset > 0
            
            if scrambled:
                result.append(f"{img_url}#scrambled_offset={offset}")
            else:
                result.append(img_url)

        return result
        
    def download_image(self, url: str) -> bytes:
        # Override download_image to descramble if necessary
        offset = 0
        if "#scrambled_offset=" in url:
            url, offset_str = url.split("#scrambled_offset=")
            offset = int(offset_str)

        try:
            logger_debug_message = f"Downloading image: {url}"
            resp = self.session.get(url, headers={"Accept": "image/webp,image/apng,image/*,*/*;q=0.8"})
            resp.raise_for_status()
            data = resp.content
            
            if offset > 0:
                data = self._descramble_image(data, offset)
                
            return data
        except Exception as e:
            raise ProviderError(f"Failed to download image: {e}")

    def _descramble_image(self, image_data: bytes, offset: int) -> bytes:
        try:
            from PIL import Image
            import io
        except ImportError:
            # Cannot descramble without Pillow
            return image_data

        PIECE_SIZE = 200
        MIN_SPLIT = 5

        def ceil_div(a, b):
            return (a + b - 1) // b

        img = Image.open(io.BytesIO(image_data)).convert("RGBA")
        w, h = img.size
        result = Image.new("RGBA", (w, h))

        pw = min(PIECE_SIZE, ceil_div(w, MIN_SPLIT))
        ph = min(PIECE_SIZE, ceil_div(h, MIN_SPLIT))
        xmax = ceil_div(w, pw) - 1
        ymax = ceil_div(h, ph) - 1

        for y in range(ymax + 1):
            for x in range(xmax + 1):
                x_dst = pw * x
                y_dst = ph * y
                bw = min(pw, w - x_dst)
                bh = min(ph, h - y_dst)

                x_src = pw * x if x == xmax else pw * ((xmax - x + offset) % xmax)
                y_src = ph * y if y == ymax else ph * ((ymax - y + offset) % ymax)

                piece = img.crop((x_src, y_src, x_src + bw, y_src + bh))
                result.paste(piece, (x_dst, y_dst))

        buf = io.BytesIO()
        result.convert("RGB").save(buf, format="JPEG", quality=95)
        return buf.getvalue()
