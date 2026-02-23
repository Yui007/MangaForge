import re
from typing import List, Optional
from urllib.parse import quote

from bs4 import BeautifulSoup
import httpx

from core.base_provider import BaseProvider, ProviderError
from models import MangaSearchResult, MangaInfo, Chapter


class MangaKatanaProvider(BaseProvider):
    provider_id = "mangakatana"
    provider_name = "MangaKatana"
    base_url = "https://mangakatana.com"

    def get_headers(self) -> dict:
        headers = super().get_headers()
        headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{self.base_url}/",
        })
        return headers

    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        encoded_query = quote(query)
        url = f"{self.base_url}/page/{page}?search={encoded_query}&search_by=m_name"
        
        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        book_list = soup.select_one("#book_list")
        if not book_list:
            return [], False

        items = book_list.select("div.item")
        results = []

        for item in items:
            title_tag = item.select_one("h3.title a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            href = str(title_tag.get("href", ""))
            
            cover_tag = item.select_one(".wrap_img img")
            cover = ""
            if cover_tag:
                cover_val = (
                    cover_tag.get("data-src")
                    or cover_tag.get("data-lazy-src")
                    or cover_tag.get("src")
                )
                if cover_val:
                    cover = str(cover_val)

            manga_id = href.split("/")[-1] if "/" in href else href

            results.append(MangaSearchResult(
                provider_id=self.provider_id,
                manga_id=manga_id,
                title=title,
                cover_url=cover,
                url=href if href.startswith("http") else f"{self.base_url}/{href.lstrip('/')}"
            ))

        has_next = bool(soup.select_one("a.next.page-numbers"))
        return results, has_next

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        if not url and manga_id:
            url = f"{self.base_url}/manga/{manga_id}"
            
        if not url:
            raise ValueError("Must provide url or manga_id")

        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.select_one("div.info h1.heading")
        title = title_tag.get_text(strip=True) if title_tag else "Unknown"

        cover_tag = soup.select_one("div.cover img")
        cover_url = str(cover_tag.get("src", "")) if cover_tag else ""

        alt_titles = []
        authors = []
        genres = []
        status = "Unknown"
        updated_at = None

        for row in soup.select("ul.meta li.d-row-small"):
            label_tag = row.select_one(".label")
            value_tag = row.select_one(".value")
            if not label_tag or not value_tag:
                continue
                
            label = label_tag.get_text(strip=True).rstrip(":").lower()
            value = value_tag.get_text(" ", strip=True)

            if "alt name" in label:
                alt_titles = [t.strip() for t in value.split(";") if t.strip()]
            elif "author" in label:
                authors = [a.get_text(strip=True) for a in value_tag.select("a.author")]
            elif "genre" in label:
                genres = [a.get_text(strip=True) for a in value_tag.select("a")]
            elif "status" in label:
                status = value
            elif "update" in label:
                updated_at = value

        desc_tag = soup.select_one("div.summary p")
        description = desc_tag.get_text(strip=True) if desc_tag else ""
        
        # Determine actual ID from the URL we ended up at
        actual_id = str(resp.url).split("/")[-1]

        return MangaInfo(
            provider_id=self.provider_id,
            manga_id=actual_id,
            title=title,
            alternative_titles=alt_titles,
            cover_url=cover_url,
            url=str(resp.url),
            description=description,
            authors=authors,
            artists=[],
            genres=genres,
            status=status,
            year=None
        )

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        url = f"{self.base_url}/manga/{manga_id}"
        resp = self.session.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        chapters = []
        for row in soup.select("table.uk-table tbody tr"):
            ch_tag = row.select_one("div.chapter a")
            date_tag = row.select_one("div.update_time")
            if not ch_tag:
                continue

            ch_title = ch_tag.get_text(strip=True)
            ch_url = str(ch_tag.get("href", ""))
            
            # Extract chapter number, fallback to raw title if unable
            m = re.search(r'Chapter\s+([\d\.]+)', ch_title, re.IGNORECASE)
            ch_num = m.group(1) if m else ch_title
            
            # If the title is just "Chapter X", make the title empty so it isn't redundant
            # Because the CLI renders: f"Chapter {chapter.number} - {chapter.title}"
            clean_title = ch_title
            if m and ch_title.strip().lower() == f"chapter {ch_num}".lower():
                clean_title = ""
            elif m:
                # If it's something like "Chapter 1: The Beginning", extract "The Beginning"
                # or just leave it as is if extracting is messy.
                # Let's remove the "Chapter X" prefix from the title if it starts with it
                prefix = m.group(0)
                if ch_title.startswith(prefix):
                    clean_title = ch_title[len(prefix):].lstrip(" -:")
            
            ch_id = ch_url
            date_str = date_tag.get_text(strip=True) if date_tag else None

            chapters.append(Chapter(
                chapter_id=ch_id,
                manga_id=manga_id,
                title=clean_title,
                chapter_number=ch_num,
                volume=None,
                url=ch_url,
                release_date=date_str,
                language="en"
            ))

        # Output chapters oldest first
        chapters.reverse()
        return chapters

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        # 'chapter_id' is just the ID, we need manga_id or the full url.
        # But wait, we only get chapter_id.
        # If chapter_id is something like "c1", how do we get the URL?
        # Better to pass the full URL or include manga_id in chapter_id.
        # Let's adjust get_chapters to use the full URL as chapter_id, just in case.
        # Oh, BaseProvider expects chapter_id. So we will just use the full URL as chapter_id for mangakatana too.
        
        # Actually I'd better just fetch the full URL if piece starts with http
        url = chapter_id if chapter_id.startswith("http") else str(chapter_id)
        if not url.startswith("http"):
            # Wait, mangakatana chapter urls look like manga_url/c1
            # But the chapter id doesn't know the manga id. 
            # I will change get_chapters to store full url as chapter_id.
            raise ProviderError("Chapter ID must be a full URL for MangaKatana")

        resp = self.session.get(url)
        resp.raise_for_status()
        html = resp.text

        # ── Primary: extract from JS array ──────────────────────────────────────
        arrays = re.findall(r"var\s+(\w+)\s*=\s*\[(.*?)\];", html, re.DOTALL)
        for name, content in arrays:
            urls = re.findall(r"'(https://[^']+)'", content)
            if len(urls) > 5:
                # Need to return these correctly encoded or without single quotes
                return urls

        # ── Fallback: scrape <img data-src> / <img src> from HTML ───────────────
        soup = BeautifulSoup(html, "html.parser")
        images = []
        for img_div in soup.select("div.wrap_img"):
            img_tag = img_div.select_one("img")
            if img_tag:
                src = img_tag.get("data-src") or img_tag.get("src", "")
                src_str = str(src)
                if src_str and src_str.startswith("http"):
                    images.append(src_str)
        return images
