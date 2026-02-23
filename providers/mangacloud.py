from typing import List, Optional
import time
import httpx
from bs4 import BeautifulSoup

from core.base_provider import BaseProvider
from models import MangaSearchResult, MangaInfo, Chapter


class MangaCloudProvider(BaseProvider):
    provider_id = "mangacloud"
    provider_name = "MangaCloud"
    base_url = "https://mangacloud.org"
    
    api_base = "https://api.mangacloud.org"
    image_cdn_base = "https://pika.mangacloud.org"

    def get_headers(self) -> dict:
        headers = super().get_headers()
        headers.update({
            "Accept": "application/json",
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
        })
        return headers

    def _api_get(self, endpoint: str, params: Optional[dict] = None, retries: int = 3) -> dict:
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
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

    def _api_post(self, endpoint: str, payload: dict, retries: int = 3) -> dict:
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        for attempt in range(retries):
            try:
                resp = self.session.post(url, json=payload)
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
        if page > 1:
            return [], False

        payload = {"title": query}
        resp = self._api_post("comic/browse", payload)
        comics = resp.get("data", [])
        if not isinstance(comics, list):
            comics = [comics]

        results = []
        for comic in comics:
            comic_id = str(comic.get("id", ""))
            cover = comic.get("cover")
            cover_url = ""
            if cover:
                cover_url = f"{self.image_cdn_base}/{comic_id}/{cover['id']}.{cover.get('f', 'jpeg')}"

            title_val = comic.get("title") or "Unknown"
            
            results.append(MangaSearchResult(
                provider_id=self.provider_id,
                manga_id=comic_id,
                title=str(title_val),
                cover_url=cover_url,
                url=f"{self.base_url}/comic/{comic_id}"
            ))

        return results, False

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not manga_id and url:
            manga_id = url.rstrip("/").split("/")[-1]

        if not manga_id:
            raise ValueError("Must provide manga_id or url")

        resp = self._api_get(f"comic/{manga_id}")
        comic = resp.get("data", {})

        tags = comic.get("tags", [])
        genres = [str(t["name"]) for t in tags if t.get("type") == "genre"]

        cover_url = ""
        cover = comic.get("cover")
        if cover:
            cover_url = f"{self.image_cdn_base}/{manga_id}/{cover['id']}.{cover.get('f', 'jpeg')}"

        authors_str = comic.get("authors") or ""
        authors = [a.strip() for a in str(authors_str).split(",") if a.strip()]

        artists_str = comic.get("artists") or ""
        artists = [a.strip() for a in str(artists_str).split(",") if a.strip()]

        alt_titles_str = comic.get("alt_titles") or ""
        alt_titles = [t.strip() for t in str(alt_titles_str).split(",") if t.strip()]

        start_year = comic.get("start_year")
        try:
            year = int(start_year) if start_year else None
        except (ValueError, TypeError):
            year = None
            
        title_val = comic.get("title") or "Unknown"
        status_val = comic.get("status") or "Unknown"
        desc_val = comic.get("description") or ""

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=manga_id,
            title=str(title_val),
            alternative_titles=alt_titles,
            cover_url=cover_url,
            url=f"{self.base_url}/comic/{manga_id}",
            description=str(desc_val),
            authors=authors,
            artists=artists,
            genres=genres,
            status=str(status_val),
            year=year
        )

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        resp = self._api_get(f"comic/{manga_id}")
        comic = resp.get("data", {})
        raw_chapters = comic.get("chapters", [])

        chapters = []
        for ch in raw_chapters:
            ch_num = str(ch.get("number", ""))
            ch_name = str(ch.get("name") or "").strip()
            ch_id = str(ch.get("id", ""))
            date_val = str(ch.get("created_date") or "")[:10]

            chapters.append(Chapter(
                chapter_id=ch_id,
                manga_id=manga_id,
                title=ch_name,
                chapter_number=ch_num,
                volume=None,
                url=f"{self.base_url}/chapter/{ch_id}",
                release_date=date_val,
                language="en"
            ))

        # API returns newest first. The required order is oldest first.
        chapters.reverse()
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        resp = self._api_get(f"chapter/{chapter_id}")
        data = resp.get("data", {})
        comic_id_val = str(data.get("comic_id", ""))
        images = data.get("images", [])

        urls = []
        for img in images:
            img_id = str(img.get("id", ""))
            fmt = str(img.get("f", "webp"))
            urls.append(f"{self.image_cdn_base}/{comic_id_val}/{chapter_id}/{img_id}.{fmt}")

        return urls
