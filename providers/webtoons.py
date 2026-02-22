"""Webtoons provider for MangaForge."""
import logging
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError
from core.config import Config
from models import Chapter, MangaInfo, MangaSearchResult

logger = logging.getLogger(__name__)


class WebtoonsProvider(BaseProvider):
    """Provider implementation for LINE Webtoons (webtoons.com)."""

    provider_id = "webtoons"
    provider_name = "Webtoons"
    base_url = "https://www.webtoons.com"

    def __init__(self) -> None:
        self._config = Config()
        self._manga_url_cache: Dict[str, str] = {}
        self._chapter_url_cache: Dict[str, str] = {}
        super().__init__()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        logger.debug("Webtoons search: query=%s page=%s", query, page)

        try:
            # Use the correct Webtoons search URL format
            search_url = f"{self.base_url}/en/search/originals"
            params = {
                "keyword": query,
                "page": page,
            }
            response = self.session.get(search_url, params=params)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            results: List[MangaSearchResult] = []

            for item in soup.select("ul.webtoon_list li"):
                link = item.find("a", class_="link")
                title_el = item.find("strong", class_="title")
                thumb = item.find("img")
                title_no = link.get("data-title-no") if link else None

                if not (link and title_el and title_no):
                    continue

                manga_id = title_no.strip()
                manga_url = link.get("href")
                title = self._clean_text(title_el.get_text(strip=True))
                cover_url = thumb.get("src") if thumb and thumb.get("src") else ""

                result = MangaSearchResult(
                    provider_id=self.provider_id,
                    manga_id=manga_id,
                    title=title,
                    cover_url=cover_url,
                    url=manga_url,
                )
                results.append(result)
                if manga_url:
                    self._manga_url_cache[manga_id] = manga_url

            total_results = self._extract_total_results(response.text)
            has_next = False
            if total_results is not None and results:
                estimated_page_size = len(results)
                has_next = (page * estimated_page_size) < total_results
            elif len(results) >= 30:
                has_next = True

            logger.info("Webtoons search returned %s results (has_next=%s)", len(results), has_next)
            return results, has_next

        except Exception as exc:  # noqa: BLE001
            logger.error("Webtoons search failed: %s", exc)
            raise ProviderError(f"Search failed: {exc}") from exc

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        target_url = self._resolve_manga_url(manga_id=manga_id, url=url)
        logger.debug("Webtoons get_manga_info: url=%s", target_url)

        try:
            response = self.session.get(target_url)
            if response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {target_url}")
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            header = soup.select_one("div.detail_header")
            title_el = header.select_one("h1.subj") if header else None
            cover_el = header.select_one("span.thmb img") if header else None

            title = self._clean_text(title_el.get_text(strip=True)) if title_el else ""
            if not title:
                raise MangaNotFoundError("Unable to extract title from Webtoons page")

            cover_url: str = "https://via.placeholder.com/300x400?text=No+Cover"
            if cover_el and cover_el.get("src"):
                cover_url = str(cover_el.get("src"))
            description = self._extract_description(soup)
            authors = self._extract_authors(soup)
            genres = self._extract_genres(soup)
            status = self._extract_status(soup)

            manga_info = MangaInfo(
                provider_id=self.provider_id,
                manga_id=self._extract_manga_id_from_url(target_url),
                title=title,
                alternative_titles=[],
                cover_url=cover_url,
                url=target_url,
                description=description,
                authors=authors,
                artists=[],
                genres=genres,
                status=status,
                year=None,
            )
            self._manga_url_cache[manga_info.manga_id] = target_url
            return manga_info

        except MangaNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Webtoons get_manga_info failed: %s", exc)
            raise ProviderError(f"Failed to fetch manga info: {exc}") from exc

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """Fetch all chapters via the Webtoons mobile API.

        Tries the originals endpoint first, then falls back to the
        canvas endpoint.  Both return JSON with all episodes in one
        request (no pagination needed).
        """
        logger.debug("Webtoons get_chapters: manga_id=%s", manga_id)

        title_no = manga_id.strip()
        api_base = "https://m.webtoons.com/api/v1"
        endpoints = [
            f"{api_base}/webtoon/{title_no}/episodes?pageSize=10000",
            f"{api_base}/canvas/{title_no}/episodes?pageSize=10000",
        ]

        raw_episodes: List[dict] = []
        last_error: Optional[Exception] = None

        for api_url in endpoints:
            try:
                logger.debug("Trying Webtoons API: %s", api_url)
                response = self.session.get(api_url)
                response.raise_for_status()
                data = response.json()

                if not data.get("success"):
                    logger.debug("API returned success=false for %s", api_url)
                    continue

                result = data.get("result", {})
                raw_episodes = result.get("episodeList", [])
                if raw_episodes:
                    logger.info(
                        "Webtoons API returned %d episodes from %s",
                        len(raw_episodes),
                        api_url,
                    )
                    break
            except Exception as exc:
                last_error = exc
                logger.debug("Webtoons API %s failed: %s", api_url, exc)
                continue

        if not raw_episodes:
            if last_error:
                raise ProviderError(
                    f"Failed to fetch chapter list: {last_error}"
                ) from last_error
            raise ProviderError(
                f"No episodes found for title_no={title_no}"
            )

        episodes: List[Chapter] = []
        for ep in raw_episodes:
            episode_no = ep.get("episodeNo")
            if episode_no is None:
                continue

            episode_no_str = str(episode_no)
            title = self._clean_text(ep.get("episodeTitle", f"Episode {episode_no}"))
            chapter_number = self._extract_chapter_number(
                title, episode_no
            )

            # Build viewer URL from the API's viewerLink
            viewer_link = ep.get("viewerLink", "")
            if viewer_link and not viewer_link.startswith("http"):
                episode_url = f"https://www.webtoons.com{viewer_link}"
            else:
                episode_url = viewer_link or ""

            # Convert exposureDateMillis to readable date
            release_date: Optional[str] = None
            millis = ep.get("exposureDateMillis")
            if millis:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
                    release_date = dt.strftime("%Y-%m-%d")
                except (ValueError, OSError, OverflowError):
                    pass

            chapter_id = f"{manga_id}:{episode_no_str}"
            if episode_url:
                self._chapter_url_cache[chapter_id] = episode_url

            episodes.append(
                Chapter(
                    chapter_id=chapter_id,
                    manga_id=manga_id,
                    title=title,
                    chapter_number=chapter_number,
                    volume=None,
                    url=episode_url,
                    release_date=release_date,
                    language="en",
                )
            )

        episodes.sort(key=lambda ch: self._extract_sort_key(ch.chapter_id))
        logger.info("Webtoons get_chapters collected %s chapters", len(episodes))
        return episodes

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        logger.debug("Webtoons get_chapter_images: chapter_id=%s", chapter_id)

        try:
            manga_id, episode_no = chapter_id.split(":", 1)
        except ValueError as exc:  # noqa: BLE001
            raise ProviderError(f"Invalid chapter ID: {chapter_id}") from exc

        chapter_url = self._chapter_url_cache.get(chapter_id)
        if not chapter_url:
            chapter_url = self._build_viewer_url(manga_id, episode_no)

        try:
            response = self.session.get(chapter_url)
            if response.status_code == 404:
                raise ProviderError(f"Chapter not found: {chapter_url}")
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            image_container = soup.find("div", id="_imageList")
            if not image_container:
                logger.warning("Webtoons chapter images container missing: %s", chapter_url)
                return []

            image_urls: List[str] = []
            for img in image_container.find_all("img", class_="_images"):
                src = img.get("data-url") or img.get("data-src") or img.get("src")
                if src:
                    image_urls.append(src)

            logger.info("Webtoons chapter %s images: %s", chapter_id, len(image_urls))
            return image_urls

        except Exception as exc:  # noqa: BLE001
            logger.error("Webtoons get_chapter_images failed: %s", exc)
            raise ProviderError(f"Failed to fetch chapter images: {exc}") from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def get_headers(self) -> Dict[str, str]:
        headers = super().get_headers()
        headers["User-Agent"] = self._config.get("network.user_agent", headers.get("User-Agent", ""))
        headers["Referer"] = self.base_url
        return headers

    def _resolve_manga_url(self, manga_id: Optional[str], url: Optional[str]) -> str:
        if url:
            resolved = url if url.startswith("http") else f"{self.base_url}{url}"
            if manga_id:
                self._manga_url_cache[manga_id] = resolved
            else:
                self._manga_url_cache[self._extract_manga_id_from_url(resolved)] = resolved
            return resolved

        if manga_id and manga_id in self._manga_url_cache:
            return self._manga_url_cache[manga_id]

        if manga_id:
            raise MangaNotFoundError(
                "Webtoons requires a full URL for manga info. Please perform a search first so the URL is cached."
            )

        raise ValueError("Either manga_id or url must be provided")

    @staticmethod
    def _extract_manga_id_from_url(url: str) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query))
        if "title_no" in query:
            return query["title_no"]
        match = re.search(r"title_no=(\d+)", url)
        if match:
            return match.group(1)
        return url.strip().rstrip("/").split("/")[-1]

    @staticmethod
    def _extract_total_results(html: str) -> Optional[int]:
        match = re.search(r"webtoonCount\s*:\s*(\d+)", html)
        if match:
            try:
                return int(match.group(1))
            except ValueError:  # pragma: no cover - defensive
                return None
        return None

    @staticmethod
    def _clean_text(value: Optional[str]) -> str:
        if not value:
            return ""
        return value.replace("\ufeff", "").replace("\uFFFD", "").strip()

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str:
        desc = soup.select_one("p.summary")
        return WebtoonsProvider._clean_text(desc.get_text(" ", strip=True)) if desc else ""

    @staticmethod
    def _extract_authors(soup: BeautifulSoup) -> List[str]:
        author_area = soup.select_one("div.author_area")
        if not author_area:
            return []
        texts = [t for t in author_area.stripped_strings if "author info" not in t.lower()]
        authors_text = " ".join(texts)
        parts = re.split(r"[,/&]", authors_text)
        return [WebtoonsProvider._clean_text(part) for part in parts if WebtoonsProvider._clean_text(part)]

    @staticmethod
    def _extract_genres(soup: BeautifulSoup) -> List[str]:
        genre_el = soup.select_one("div.detail_header h2.genre")
        if not genre_el:
            return []
        genres = genre_el.get_text(" ", strip=True)
        parts = re.split(r"[,/|]", genres)
        return [WebtoonsProvider._clean_text(part) for part in parts if WebtoonsProvider._clean_text(part)]

    @staticmethod
    def _extract_status(soup: BeautifulSoup) -> str:
        day_info = soup.select_one("p.day_info")
        if not day_info:
            return "Unknown"
        text = WebtoonsProvider._clean_text(day_info.get_text(" ", strip=True))
        if "UP" in text.upper():
            return "Ongoing"
        return text or "Unknown"



    def _extract_chapter_number(self, label: str, fallback: Optional[int]) -> str:
        match = re.search(r"(\d+(?:\.\d+)?)", label)
        if match:
            return match.group(1)
        if fallback is not None:
            return str(fallback)
        return label.strip() or "0"

    @staticmethod
    def _extract_sort_key(chapter_id: str) -> int:
        try:
            _, episode_no = chapter_id.split(":", 1)
            return int(episode_no)
        except (ValueError, AttributeError):  # pragma: no cover - defensive
            return 0

    def _build_viewer_url(self, manga_id: str, episode_no: str) -> str:
        cached_url = self._manga_url_cache.get(manga_id)
        if cached_url:
            parsed = urlparse(cached_url)
            viewer_path = parsed.path.replace("/list", "/viewer")
            params = dict(parse_qsl(parsed.query))
            params["episode_no"] = episode_no
            new_query = urlencode(params, doseq=True)
            return urlunparse(parsed._replace(path=viewer_path, query=new_query))
        return f"{self.base_url}/en/viewer?title_no={manga_id}&episode_no={episode_no}"
