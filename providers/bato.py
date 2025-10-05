"""
Bato provider for MangaForge.

This provider implements scraping for Bato.to (formerly Mangakatana).
It extracts manga information, chapter lists, and image URLs for download.
"""
import logging
import re
import json
from typing import List, Optional, Tuple
from urllib.parse import quote

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)


class BatoProvider(BaseProvider):
    """
    Provider for Bato.to manga website.

    This provider scrapes manga information from Bato.to including
    search results, manga details, chapter listings, and image URLs.
    """

    provider_id = "bato"
    provider_name = "Bato"
    base_url = "https://bato.to"

    def __init__(self):
        """Initialize the Bato provider."""
        super().__init__()
        logger.info("Bato provider initialized")

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        """
        Search for manga on Bato.to.

        Args:
            query: Search query string
            page: Page number for pagination (1-indexed)

        Returns:
            Tuple of (search_results, has_next_page)
        """
        logger.debug(f"Searching Bato for '{query}' on page {page}")

        try:
            # Build search URL
            search_url = f"{self.base_url}/search"
            params = {
                'word': query,
                'page': page
            }

            # Make request
            response = self.session.get(search_url, params=params)
            response.raise_for_status()

            # Parse HTML
            soup = self._parse_html(response.text)

            # Extract search results
            results = []
            seen_urls = set()

            # Find all manga items in search results
            for item in soup.find_all('div', class_='item-text'):
                title_element = item.find('a', class_='item-title')
                if title_element:
                    title = title_element.text.strip()
                    url = self.base_url + title_element['href']

                    # Avoid duplicates
                    if url not in seen_urls:
                        seen_urls.add(url)

                        # Extract manga ID from URL
                        manga_id = self._extract_manga_id_from_url(url)

                        result = MangaSearchResult(
                            provider_id=self.provider_id,
                            manga_id=manga_id,
                            title=title,
                            cover_url="",  # Bato search doesn't show covers easily
                            url=url
                        )
                        results.append(result)

            # Check if there's a next page
            has_next = self._has_next_page(soup, page)

            logger.info(f"Bato search returned {len(results)} results")
            return results, has_next

        except Exception as e:
            logger.error(f"Bato search failed: {e}")
            raise ProviderError(f"Search failed: {e}")

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """
        Get detailed manga information from Bato.to.

        Args:
            manga_id: Bato manga ID
            url: Direct URL to manga page

        Returns:
            MangaInfo object with complete details
        """
        if not manga_id and not url:
            raise ValueError("Either manga_id or url must be provided")

        try:
            # Build URL and extract manga_id
            if url:
                target_url = url
                manga_id = self._extract_manga_id_from_url(url)
                if not manga_id:
                    raise MangaNotFoundError(f"Could not extract manga ID from URL: {url}")
            else:
                if not manga_id:
                    raise ValueError("manga_id is required when url is not provided")
                target_url = f"{self.base_url}/series/{manga_id}"

            logger.debug(f"Fetching Bato manga info from: {target_url}")

            # Make request
            response = self.session.get(target_url)
            if response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {manga_id}")
            response.raise_for_status()

            # Parse HTML
            soup = self._parse_html(response.text)

            # Extract basic info
            title = self._extract_title(soup)
            if not title:
                raise MangaNotFoundError(f"Could not extract title for manga: {manga_id}")

            # Extract additional metadata
            alternative_titles = self._extract_alternative_titles(soup)
            description = self._extract_description(soup)
            authors = self._extract_authors(soup)
            artists = self._extract_artists(soup)
            genres = self._extract_genres(soup)
            status = self._extract_status(soup)
            year = self._extract_year(soup)

            manga_info = MangaInfo(
                provider_id=self.provider_id,
                manga_id=manga_id,
                title=title,
                alternative_titles=alternative_titles,
                cover_url="",  # Bato doesn't show covers on series page easily
                url=target_url,
                description=description,
                authors=authors,
                artists=artists,
                genres=genres,
                status=status,
                year=year
            )

            logger.info(f"Extracted Bato manga info: {manga_info.title}")
            return manga_info

        except MangaNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Bato get_manga_info failed: {e}")
            raise ProviderError(f"Failed to get manga info: {e}")

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """
        Get all chapters for a manga from Bato.to.

        Args:
            manga_id: Bato manga ID

        Returns:
            List of Chapter objects in reading order
        """
        logger.debug(f"Fetching Bato chapters for: {manga_id}")

        try:
            # Get manga info page first (chapters are on the same page)
            manga_info = self.get_manga_info(manga_id=manga_id)
            series_url = manga_info.url

            # Make request to series page
            response = self.session.get(series_url)
            response.raise_for_status()

            # Parse HTML
            soup = self._parse_html(response.text)

            # Extract chapters
            chapters = []
            chapter_elements = soup.find_all('a', class_='chapt')

            for chapter_element in chapter_elements:
                chapter_title = chapter_element.text.strip()
                chapter_url = self.base_url + chapter_element['href']

                # Extract chapter information
                chapter_id = self._extract_chapter_id_from_url(chapter_url)
                chapter_number = self._extract_chapter_number(chapter_title)
                volume = self._extract_volume(chapter_title)

                # Extract release date using the correct Bato selector
                release_date = self._extract_chapter_date(chapter_element)

                chapter = Chapter(
                    chapter_id=chapter_id,
                    manga_id=manga_id,
                    title=chapter_title,
                    chapter_number=chapter_number,
                    volume=volume,
                    url=chapter_url,
                    release_date=release_date,
                    language="en"
                )
                chapters.append(chapter)

            # Reverse order so Chapter 1 comes first
            chapters.reverse()

            logger.info(f"Extracted {len(chapters)} chapters from Bato")
            return chapters

        except Exception as e:
            logger.error(f"Bato get_chapters failed: {e}")
            raise ProviderError(f"Failed to get chapters: {e}")

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """
        Get all image URLs for a chapter from Bato.to.

        Args:
            chapter_id: Bato chapter ID

        Returns:
            List of direct image URLs in reading order
        """
        logger.debug(f"Fetching Bato chapter images for: {chapter_id}")

        try:
            # Build chapter URL
            chapter_url = f"{self.base_url}/chapter/{chapter_id}"

            # Make request
            response = self.session.get(chapter_url)
            response.raise_for_status()

            # Parse HTML
            soup = self._parse_html(response.text)

            # Extract image URLs from JavaScript
            image_urls = self._extract_image_urls(soup)

            if not image_urls:
                logger.warning(f"No image URLs found for chapter {chapter_id}")
                return []

            logger.info(f"Extracted {len(image_urls)} image URLs from Bato chapter")
            return image_urls

        except Exception as e:
            logger.error(f"Bato get_chapter_images failed: {e}")
            raise ProviderError(f"Failed to get chapter images: {e}")

    def _parse_html(self, html: str):
        """Parse HTML content using BeautifulSoup."""
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, 'html.parser')

    def _extract_manga_id_from_url(self, url: str) -> str:
        """Extract manga ID from Bato URL."""
        # URL format: https://bato.to/series/{manga_id}
        match = re.search(r'/series/(\d+)', url)
        if match:
            return match.group(1)

        # Fallback: try to extract from other URL formats
        match = re.search(r'/title/(\d+)', url)
        if match:
            return match.group(1)

        # If no pattern matches, return the last part of the URL
        return url.split('/')[-1]

    def _extract_chapter_id_from_url(self, url: str) -> str:
        """Extract chapter ID from Bato chapter URL."""
        # URL format: https://bato.to/chapter/{chapter_id}
        match = re.search(r'/chapter/(\d+)', url)
        if match:
            return match.group(1)
        return url.split('/')[-1]

    def _extract_title(self, soup) -> str:
        """Extract manga title from series page."""
        title_element = soup.find('h3', class_='item-title')
        return title_element.text.strip() if title_element else ""

    def _extract_alternative_titles(self, soup) -> List[str]:
        """Extract alternative titles."""
        # Bato doesn't prominently display alternative titles
        return []

    def _extract_description(self, soup) -> str:
        """Extract manga description."""
        # Look for description in various possible locations
        desc_selectors = [
            'div[itemprop="description"]',
            '.series-description',
            '.description',
            '.summary'
        ]

        for selector in desc_selectors:
            desc_element = soup.select_one(selector)
            if desc_element:
                return desc_element.text.strip()

        return ""

    def _extract_authors(self, soup) -> List[str]:
        """Extract author information using Bato-specific selectors."""
        authors = []

        # Use the correct Bato selector for authors
        author_divs = soup.find_all('div', class_='attr-item')
        for div in author_divs:
            if div.find('b', class_='text-muted', string='Authors:'):
                author_links = div.find_all('a')
                for link in author_links:
                    author = link.text.strip()
                    if author and author not in authors:
                        authors.append(author)
                break  # Found the authors section, no need to continue

        # Fallback to generic selectors if specific one doesn't work
        if not authors:
            author_selectors = ['.series-author', '.author', '[itemprop="author"]', 'a[href*="author"]']
            for selector in author_selectors:
                author_elements = soup.select(selector)
                for element in author_elements:
                    author = element.text.strip()
                    if author and author not in authors:
                        authors.append(author)

        return authors

    def _extract_artists(self, soup) -> List[str]:
        """Extract artist information."""
        artists = []

        # Look for artist information
        artist_selectors = [
            '.series-artist',
            '.artist',
            '[itemprop="artist"]',
            'a[href*="artist"]'
        ]

        for selector in artist_selectors:
            artist_elements = soup.select(selector)
            for element in artist_elements:
                artist = element.text.strip()
                if artist and artist not in artists:
                    artists.append(artist)

        return artists

    def _extract_genres(self, soup) -> List[str]:
        """Extract genre information using Bato-specific selectors."""
        genres = []

        # Use the correct Bato selector for genres
        genre_divs = soup.find_all('div', class_='attr-item')
        for div in genre_divs:
            if div.find('b', class_='text-muted', string='Genres:'):
                # Extract all text within spans and underlined elements
                genre_spans = div.find_all(['span', 'u'])
                for span in genre_spans:
                    genre = span.text.strip()
                    if genre and genre not in genres and genre != ',' and genre != 'Genres:':
                        genres.append(genre)
                break  # Found the genres section, no need to continue

        # Fallback to generic selectors if specific one doesn't work
        if not genres:
            genre_selectors = ['.series-genres', '.genres', '.genre', '[itemprop="genre"]']
            for selector in genre_selectors:
                genre_elements = soup.select(selector)
                for element in genre_elements:
                    genre = element.text.strip()
                    if genre and genre not in genres:
                        genres.append(genre)

        return genres

    def _extract_status(self, soup) -> str:
        """Extract publication status using Bato-specific selectors."""
        # Use the correct Bato selector for "Original work"
        original_work_element = soup.find('b', class_='text-muted', string='Original work:')
        if original_work_element:
            # For Bato, if it shows "Original work", it typically means it's original/completed
            # This is a heuristic since Bato doesn't always clearly mark status
            return "Completed"

        # Look for other status indicators
        status_selectors = ['.series-status', '.status', '.publication-status']
        for selector in status_selectors:
            status_element = soup.select_one(selector)
            if status_element:
                status = status_element.text.strip().lower()
                if "ongoing" in status:
                    return "Ongoing"
                elif "completed" in status:
                    return "Completed"
                elif "hiatus" in status:
                    return "Hiatus"

        return "Unknown"

    def _extract_year(self, soup) -> Optional[int]:
        """Extract publication year."""
        # Look for year information
        year_selectors = [
            '.series-year',
            '.year',
            '.publication-year'
        ]

        for selector in year_selectors:
            year_element = soup.select_one(selector)
            if year_element:
                year_text = year_element.text.strip()
                try:
                    return int(year_text)
                except ValueError:
                    pass

        return None

    def _extract_chapter_number(self, chapter_title: str) -> str:
        """Extract chapter number from chapter title."""
        # Look for patterns like "Chapter 123" or "Ch. 123"
        match = re.search(r'(?:Chapter|Ch\.?)\s*(\d+(?:\.\d+)?)', chapter_title, re.IGNORECASE)
        if match:
            return match.group(1)

        # Look for just numbers at the beginning
        match = re.search(r'^(\d+(?:\.\d+)?)', chapter_title)
        if match:
            return match.group(1)

        # If no number found, return the whole title as chapter number
        return chapter_title

    def _extract_volume(self, chapter_title: str) -> Optional[str]:
        """Extract volume number from chapter title."""
        # Look for volume patterns
        match = re.search(r'Vol(?:ume)?\.?\s*(\d+)', chapter_title, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _extract_chapter_date(self, chapter_element) -> Optional[str]:
        """Extract chapter release date using Bato-specific selectors."""
        try:
            # Look for the parent container that contains the date
            # The date is in a div with class "extra" containing the timestamp
            parent_div = chapter_element.find_parent()
            if parent_div:
                # Try multiple selectors to find the date
                date_selectors = [
                    'div.extra i:last-child',
                    'div.extra i[class*="ps-3"]',
                    '.extra i'
                ]

                for selector in date_selectors:
                    date_element = parent_div.select_one(selector)
                    if date_element and 'days ago' in date_element.text:
                        return date_element.text.strip()

                # Fallback: look for any element containing "days ago"
                for element in parent_div.find_all(string=lambda text: text and 'days ago' in text):
                    return element.strip()

        except Exception as e:
            logger.debug(f"Could not extract date for chapter: {e}")

        return None

    def _extract_image_urls(self, soup) -> List[str]:
        """Extract image URLs from chapter page JavaScript."""
        image_urls = []

        # Find script tags containing image data
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'imgHttps' in script.string:
                # Look for imgHttps array in JavaScript
                match = re.search(r'imgHttps\s*=\s*(\[.*?\]);', script.string, re.DOTALL)
                if match:
                    try:
                        # Parse JSON array of image URLs
                        image_urls = json.loads(match.group(1))
                        break
                    except json.JSONDecodeError as e:
                        logger.warning(f"Error decoding image URLs JSON: {e}")
                        continue

        # Filter out empty URLs and ensure they start with http
        valid_urls = [
            url for url in image_urls
            if url and isinstance(url, str) and url.startswith('http')
        ]

        return valid_urls

    def _has_next_page(self, soup, current_page: int) -> bool:
        """Check if there's a next page in search results."""
        # Look for next page link or pagination indicators
        pagination_selectors = [
            '.pagination .next',
            'a[href*="page"]',
            '.pager .next'
        ]

        for selector in pagination_selectors:
            next_element = soup.select_one(selector)
            if next_element:
                return True

        # Check if current page has results (if no results, likely no next page)
        result_items = soup.find_all('div', class_='item-text')
        return len(result_items) > 0