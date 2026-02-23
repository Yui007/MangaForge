from typing import List, Optional
import time

import httpx

from core.base_provider import BaseProvider, ProviderError
from models import MangaSearchResult, MangaInfo, Chapter


class AtsuProvider(BaseProvider):
    provider_id = "atsu"
    provider_name = "Atsu"
    base_url = "https://atsu.moe"

    def get_headers(self) -> dict:
        headers = super().get_headers()
        headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": f"{self.base_url}/",
        })
        return headers

    def _retry_get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.RequestError as e:
                if attempt == retries - 1:
                    raise e
                time.sleep(2 ** attempt)
            except httpx.HTTPStatusError as e:
                if attempt == retries - 1:
                    raise e
                time.sleep(2 ** attempt)
        return {}

    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        api_url = f"{self.base_url}/api/search/page"
        data = self._retry_get(api_url, params={"query": query})
        
        items = data.get("hits", [])
        if not items:
            return [], False
            
        results = []
        for item in items:
            manga_id = str(item.get("id", ""))
            if not manga_id:
                continue
                
            title = str(item.get("title") or item.get("englishTitle") or "Unknown")
            
            poster = item.get("largeImage") or item.get("mediumImage") or item.get("image") or ""
            cover_url = ""
            if isinstance(poster, str) and poster:
                poster = poster.lstrip("/")
                if poster.startswith("static/"):
                    poster = poster[len("static/") :]
                cover_url = f"{self.base_url}/static/{poster}"

            results.append(MangaSearchResult(
                provider_id=self.provider_id,
                manga_id=manga_id,
                title=title,
                cover_url=cover_url,
                url=f"{self.base_url}/manga/{manga_id}"
            ))

        return results, False

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not manga_id and url:
            # e.g., https://atsu.moe/manga/OaKBx
            manga_id = url.strip("/").split("/")[-1]
            
        if not manga_id:
            raise ValueError("Must provide manga_id or url")

        api_url = f"{self.base_url}/api/manga/page"
        data = self._retry_get(api_url, params={"id": manga_id})
        
        # Format similar to models.py in standalone scraper
        manga = data.get("mangaPage") or data
        if "id" not in manga:
            raise ProviderError(f"Failed to fetch manga info for {manga_id}")

        title = str(manga.get("title") or manga.get("englishTitle") or "Unknown")
        synopsis = str(manga.get("synopsis", ""))
        
        poster = manga.get("poster") or manga.get("image")
        cover_url = ""
        if isinstance(poster, dict):
            poster = poster.get("image")
        if isinstance(poster, str):
            poster = poster.lstrip("/")
            if poster.startswith("static/"):
                poster = poster[len("static/") :]
            cover_url = f"{self.base_url}/static/{poster}"

        genres = []
        for g in manga.get("genres") or []:
            if g.get("name"):
                genres.append(str(g["name"]))
        if not genres:
            for t in manga.get("tags") or []:
                if t.get("name"):
                    genres.append(str(t["name"]))

        authors = []
        for a in manga.get("authors") or []:
            if a.get("name"):
                authors.append(str(a["name"]))

        status = str(manga.get("status", "Unknown"))
        
        m_info = MangaInfo(
            provider_id=self.provider_id,
            manga_id=manga_id,
            title=title,
            alternative_titles=[],
            cover_url=cover_url,
            url=f"{self.base_url}/manga/{manga_id}",
            description=synopsis,
            authors=authors,
            artists=[],
            genres=genres,
            status=status,
            year=None
        )
        return m_info

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        from core.config import Config
        preferred_scan = Config().get("providers.preferred_scanlator", "")
        
        # Fetch manga info directly to get scanlator IDs mappings
        api_url_page = f"{self.base_url}/api/manga/page"
        page_data = self._retry_get(api_url_page, params={"id": manga_id})
        manga = page_data.get("mangaPage") or page_data
        
        scanlators = {}
        for s in manga.get("scanlators", []):
            if s.get("id") and s.get("name"):
                scanlators[str(s["name"]).lower()] = str(s["id"])

        api_url = f"{self.base_url}/api/manga/allChapters"
        data = self._retry_get(api_url, params={"mangaId": manga_id})
        
        chapters_data = data.get("chapters", [])
        
        def normalize_number(value: float) -> str:
            if value == int(value):
                return str(int(value))
            return f"{value:.6f}".rstrip("0").rstrip(".")

        # Group by chapter number
        from collections import defaultdict
        by_number: dict[str, list[dict]] = defaultdict(list)
        
        for ch in chapters_data:
            ch_num_raw = ch.get("number")
            ch_num_float = float(ch_num_raw) if ch_num_raw is not None else 0.0
            ch_num_str = normalize_number(ch_num_float)
            by_number[ch_num_str].append(ch)
            
        # Reverse mapping: id -> name
        scan_id_to_name = {v: k for k, v in scanlators.items()}
            
        chapters = []
        for number, entries in by_number.items():
            entries_to_add = entries
            
            if preferred_scan and len(entries) > 1:
                for entry in entries:
                    scan_id = entry.get("scanlationMangaId") or entry.get("scanId")
                    if scan_id and scan_id_to_name.get(scan_id, "") == preferred_scan.lower():
                        entries_to_add = [entry]
                        break

            for selected in entries_to_add:
                chapter_id = str(selected.get("id", ""))
                if not chapter_id:
                    continue
                    
                ch_title = str(selected.get("title", ""))
                
                scan_id = selected.get("scanlationMangaId") or selected.get("scanId")
                scan_name = scan_id_to_name.get(scan_id, "").title() if scan_id else None
                
                title_val = f"Chapter {number}"
                if ch_title:
                    title_val += f" - {ch_title}"
                if scan_name:
                    title_val += f" [{scan_name}]"

                chapters.append(Chapter(
                    chapter_id=f"{manga_id}::{chapter_id}",
                    manga_id=manga_id,
                    title=title_val,
                    chapter_number=number,
                    volume=None,
                    url=f"{self.base_url}/chapter/{chapter_id}",
                    release_date=None,
                    language="en"
                ))

        # Sort so oldest is first
        chapters.sort(key=lambda x: float(x.chapter_number) if x.chapter_number.replace(".", "", 1).isdigit() else 9999.0)
        
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        if "::" not in chapter_id:
            raise ProviderError("Invalid chapter_id format for Atsu provider")
            
        manga_id, true_chapter_id = chapter_id.split("::", 1)
        api_url = f"{self.base_url}/api/read/chapter"
        data = self._retry_get(api_url, params={"mangaId": manga_id, "chapterId": true_chapter_id})
        
        read_chapter = data.get("readChapter", {})
        pages_data = read_chapter.get("pages", [])
        
        images = []
        for p in pages_data:
            img = str(p.get("image", ""))
            if img:
                images.append(f"{self.base_url}{img}")
                
        return images
