import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode, quote, urlparse

from bs4 import BeautifulSoup
import httpx

from core.base_provider import BaseProvider, ProviderError
from models import MangaSearchResult, MangaInfo, Chapter


class MangaTaroProvider(BaseProvider):
    provider_id = "mangataro"
    provider_name = "MangaTaro"
    base_url = "https://mangataro.org"

    def get_headers(self) -> dict:
        headers = super().get_headers()
        headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
            "Accept-Language": "en-US,en;q=0.9",
        })
        return headers

    def _generate_api_signature(self):
        timestamp = int(time.time())
        hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        secret = f"mng_ch_{hour}"
        digest = hashlib.md5(f"{timestamp}{secret}".encode()).hexdigest()
        return digest[:16], timestamp

    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        if page > 1:
            # MangaTaro auth/search doesn't seem to natively paginate in our provided logic
            return [], False

        url = f"{self.base_url}/auth/search?q={quote(query)}"
        resp = self.session.get(url)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            return [], False

        raw_results = data.get("results", [])
        results = []

        for item in raw_results:
            slug = str(item.get("slug", ""))
            if not slug:
                continue

            results.append(MangaSearchResult(
                provider_id=self.provider_id,
                manga_id=slug,
                title=str(item.get("title", "")),
                cover_url=str(item.get("thumbnail", "")),
                url=str(item.get("permalink", f"{self.base_url}/manga/{slug}"))
            ))

        return results, False

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not url and manga_id:
            url = f"{self.base_url}/manga/{manga_id}"
            
        if not url:
            raise ValueError("Must provide url or manga_id")

        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title = ""
        meta_title = soup.find("meta", property="og:title")
        if meta_title and meta_title.get("content"):
            title = str(meta_title["content"]).strip()
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(" ", strip=True)

        # Alt titles
        alt_titles = []
        alt_p = soup.find("p", class_=lambda c: bool(c and "text-sm text-neutral-400 mb-3 sm:mb-4" in str(c)))
        if alt_p:
            raw_alt = alt_p.get_text(" / ", strip=True)
            alt_titles = [t.strip() for t in raw_alt.split("/") if t.strip()]

        # Description
        description = ""
        meta_desc = soup.find("meta", property="og:description")
        if meta_desc and meta_desc.get("content"):
            description = str(meta_desc["content"]).strip()

        # Cover
        thumbnail = ""
        cover_img = soup.select_one("img.aspect-\\[2\\/3\\]")
        if cover_img:
            thumbnail = str(cover_img.get("src", "")).strip()
        if not thumbnail:
            meta_img = soup.find("meta", property="og:image")
            if meta_img and meta_img.get("content"):
                thumbnail = str(meta_img["content"]).strip()

        # Type & Status & Year
        status = "Unknown"
        year = None
        for span in soup.select("span.bg-neutral-800\\/70, span.capitalize"):
            text = span.get_text(strip=True)
            if not text:
                continue
            low = text.lower()
            if low in ("ongoing", "completed", "hiatus", "cancelled", "dropped"):
                status = text
            elif re.fullmatch(r"\d{4}", text):
                try:
                    year = int(text)
                except ValueError:
                    year = None

        def _split_people(text: str) -> List[str]:
            parts = re.split(r"[,&/]+", text)
            return [p.strip() for p in parts if p.strip()]

        authors = []
        artists = []
        for label_el in soup.find_all(string=re.compile(r"Author|Artist", re.I)):
            label_text = label_el.strip()
            parent = label_el.parent
            if not parent:
                continue
            container = parent.parent
            if container:
                name_div = container.find_previous_sibling("div")
                if name_div:
                    name_text = name_div.get_text(" ", strip=True)
                else:
                    name_div2 = container.find("div", class_=lambda c: bool(c and "text-neutral-200" in " ".join(c or [])))
                    name_text = name_div2.get_text(" ", strip=True) if name_div2 else ""

                if name_text:
                    names = _split_people(name_text)
                    if "author" in label_text.lower():
                        authors.extend(names)
                    if "artist" in label_text.lower():
                        artists.extend(names)
                        
        authors = list(dict.fromkeys(authors))
        artists = list(dict.fromkeys(a for a in artists if a not in authors))

        genres = []
        for anchor in soup.select('a[href*="/genre/"]'):
            text = anchor.get_text(" ", strip=True)
            if text and text not in genres:
                genres.append(text)

        parsed = urlparse(url)
        parts = [p for p in parsed.path.split("/") if p]
        slug = parts[-1] if parts else parsed.netloc

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=slug,
            title=title,
            alternative_titles=alt_titles,
            cover_url=thumbnail,
            url=url,
            description=description,
            authors=authors,
            artists=artists,
            genres=genres,
            status=status,
            year=year
        )

    def _extract_manga_id_from_soup(self, soup: BeautifulSoup) -> Optional[str]:
        for selector in (
            ".add-to-library-btn[data-manga-id]",
            "button[data-manga-id]",
            ".chapter-list[data-manga-id]",
            ".manga-status-btn[data-manga-id]",
        ):
            el = soup.select_one(selector)
            if el:
                mid = str(el.get("data-manga-id", "")).strip()
                if mid:
                    return mid

        el = soup.find(attrs={"data-manga-id": True})
        if el:
            mid = str(el.get("data-manga-id", "")).strip()
            if mid:
                return mid

        for sc in soup.find_all("script"):
            if sc.string:
                m = re.search(r'"manga_id"\s*:\s*"?(\d+)"?', sc.string)
                if m:
                    return m.group(1)
        return None

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        url = f"{self.base_url}/manga/{manga_id}"
        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        mid = self._extract_manga_id_from_soup(soup)
        if not mid:
            raise ProviderError(f"Could not extract internal manga ID for {manga_id}")

        token, timestamp = self._generate_api_signature()
        params = {
            "manga_id": mid, 
            "offset": 0, 
            "limit": 5000,
            "order": "DESC", 
            "_t": token, 
            "_ts": timestamp
        }
        api_url = f"{self.base_url}/auth/manga-chapters?" + urlencode(params)
        
        c_resp = self.session.get(api_url)
        c_resp.raise_for_status()
        data = c_resp.json()
        
        if not data.get("success"):
            return []

        raw_chapters = data.get("chapters", [])
        if not raw_chapters:
            raw_chapters = data.get("data", [])

        chapters = []
        for entry in raw_chapters:
            if not isinstance(entry, dict):
                continue
                
            ch_id = str(entry.get("id") or entry.get("hid") or "").strip()
            if not ch_id:
                continue
                
            ch_url = str(entry.get("url") or entry.get("slug") or "")
            if ch_url and not ch_url.startswith("http"):
                ch_url = f"{self.base_url}{ch_url}"

            ch_title = str(entry.get("title", ""))
            date_str = str(entry.get("date", ""))
            
            chap_num = str(entry.get("chap", ""))

            title_val = f"Chapter {chap_num}"
            if ch_title:
                title_val += f" - {ch_title}"

            chapters.append(Chapter(
                chapter_id=ch_id,
                manga_id=manga_id,
                title=title_val,
                chapter_number=chap_num,
                volume=None,
                url=ch_url,
                release_date=date_str,
                language="en"
            ))

        chapters.reverse()  # API returns DESC (newest first)
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        api_url = f"{self.base_url}/auth/chapter-content?chapter_id={chapter_id}"
        resp = self.session.get(api_url)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            raise ProviderError("MangaTaro auth/chapter-content returned success=false")

        images = data.get("images", [])
        return [str(img) for img in images]
