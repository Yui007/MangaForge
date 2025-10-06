from typing import List, Optional
from core.base_provider import BaseProvider, MangaNotFoundError, ProviderError
from models import MangaSearchResult, MangaInfo, Chapter
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import logging

logger = logging.getLogger(__name__)

class WeebCentralProvider(BaseProvider):
    provider_id = "weebcentral"
    provider_name = "WeebCentral"
    base_url = "https://weebcentral.com"

    def __init__(self):
        """Initialize the WeebCentral provider."""
        super().__init__()
        # Enhanced headers matching the original scraper
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'image',
            'Sec-Fetch-Mode': 'no-cors',
            'Sec-Fetch-Site': 'cross-site',
            'Pragma': 'no-cache',
            'Cache-Control': 'no-cache',
        }
        logger.info("WeebCentral provider initialized")

    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        """
        Note: WeebCentral doesn't have a search API.
        This is a placeholder implementation.
        """
        # WeebCentral doesn't appear to have a search function
        # Return empty results with no next page
        return ([], False)

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """
        Get detailed manga information from WeebCentral.

        Args:
            manga_id: WeebCentral manga ID
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

            logger.debug(f"Fetching WeebCentral manga info from: {target_url}")

            # Make request
            response = self.session.get(target_url)
            if response.status_code == 404:
                raise MangaNotFoundError(f"Manga not found: {manga_id}")
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.content.decode('utf-8'), 'html.parser')

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

            # Extract cover URL
            cover_url = self._extract_cover_url(soup)

            manga_info = MangaInfo(
                provider_id=self.provider_id,
                manga_id=manga_id,
                title=title,
                alternative_titles=alternative_titles,
                cover_url=cover_url,
                url=target_url,
                description=description,
                authors=authors,
                artists=artists,
                genres=genres,
                status=status,
                year=year
            )

            logger.info(f"Extracted WeebCentral manga info: {manga_info.title}")
            return manga_info

        except MangaNotFoundError:
            raise
        except Exception as e:
            logger.error(f"WeebCentral get_manga_info failed: {e}")
            raise ProviderError(f"Failed to get manga info: {e}")

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """
        Get all chapters for a manga from WeebCentral.

        Args:
            manga_id: WeebCentral manga ID

        Returns:
            List of Chapter objects in reading order
        """
        logger.debug(f"Fetching WeebCentral chapters for: {manga_id}")

        try:
            # Build series URL directly
            series_url = f"{self.base_url}/series/{manga_id}"

            # Generate the full chapter list URL (following original logic)
            chapter_list_url = self._get_chapter_list_url(series_url)
            logger.info(f"Fetching chapter list from: {chapter_list_url}")

            # Make request to chapter list page
            response = self.session.get(chapter_list_url)
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.content.decode('utf-8'), 'html.parser')

            # Extract chapters using the original logic
            chapters = []
            chapter_elements = soup.select("div[x-data] > a")

            for chapter_element in reversed(chapter_elements):  # Reverse order so Chapter 1 comes first
                chapter_url = chapter_element.get('href')
                chapter_name_element = chapter_element.select_one("span.flex > span")
                chapter_title = chapter_name_element.text.strip() if chapter_name_element else "Unknown Chapter"

                if chapter_url:
                    if not chapter_url.startswith(('http://', 'https://')):
                        chapter_url = urljoin(self.base_url, chapter_url)

                    # For WeebCentral, the chapter URL from the list IS the chapter identifier
                    # Use the full URL as the chapter_id (following original logic)
                    chapter_id = chapter_url
                    chapter_number = self._extract_chapter_number(chapter_title)
                    volume = self._extract_volume(chapter_title)

                    # Extract release date using the correct WeebCentral selector
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

            logger.info(f"Extracted {len(chapters)} chapters from WeebCentral")
            return chapters

        except Exception as e:
            logger.error(f"WeebCentral get_chapters failed: {e}")
            raise ProviderError(f"Failed to get chapters: {e}")

    def _get_chapter_list_url(self, manga_url: str) -> str:
        """Generate the full chapter list URL from manga URL (following original logic)"""
        from urllib.parse import urlparse
        parsed_url = urlparse(manga_url)
        path_parts = parsed_url.path.split('/')
        chapter_list_path = f"{'/'.join(path_parts[:3])}/full-chapter-list"
        return f"{self.base_url}{chapter_list_path}"

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """
        Get all image URLs for a chapter from WeebCentral.

        Args:
            chapter_id: WeebCentral chapter URL (or ID)

        Returns:
            List of direct image URLs in reading order
        """
        logger.debug(f"Fetching WeebCentral chapter images for: {chapter_id}")

        try:
            # chapter_id is already the full URL for WeebCentral
            chapter_url = chapter_id if chapter_id.startswith(('http://', 'https://')) else f"{self.base_url}/chapter/{chapter_id}"

            # Use Selenium to get images (WeebCentral uses JavaScript)
            image_urls = self._get_chapter_images_selenium(chapter_url)

            if not image_urls:
                logger.warning(f"No image URLs found for chapter {chapter_id}")
                return []

            logger.info(f"Extracted {len(image_urls)} image URLs from WeebCentral chapter")
            return image_urls

        except Exception as e:
            logger.error(f"WeebCentral get_chapter_images failed: {e}")
            raise ProviderError(f"Failed to get chapter images: {e}")

    def _get_chapter_images_selenium(self, chapter_url: str) -> List[str]:
        """Get image URLs using Selenium for JavaScript-heavy pages (following original logic)."""
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        import time

        # Use exact same Chrome options as original scraper
        options = webdriver.ChromeOptions()
        options.add_argument('--non-headless')
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument(f'user-agent={self.headers["User-Agent"]}')
        # Add preference to disable brotli compression (from original)
        options.add_experimental_option('prefs', {
            'profile.default_content_settings.cookies': 2,
            'profile.managed_default_content_settings.images': 1,
            'profile.default_content_setting_values.notifications': 2
        })
        # Add header to disable brotli (from original)
        options.add_argument('--accept-encoding=gzip, deflate')

        driver = webdriver.Chrome(options=options)

        try:
            logger.info("Loading page with Selenium...")
            driver.get(chapter_url)
            time.sleep(3)  # Wait for JavaScript to load (exact timing from original)

            # Wait for images to load (exact selector from original)
            WebDriverWait(driver, 10).until(
                lambda x: x.find_elements(By.CSS_SELECTOR, "img[src*='/manga/']")
            )

            # Get all image elements (exact logic from original)
            image_elements = driver.find_elements(By.CSS_SELECTOR, "img[src*='/manga/']")
            image_urls = []

            for img in image_elements:
                url = img.get_attribute('src')
                if url and not url.startswith('data:'):
                    image_urls.append(url)

            logger.info(f"Found {len(image_urls)} images")
            return image_urls

        except Exception as e:
            logger.error(f"Selenium error: {e}")
            raise ProviderError(f"Failed to get chapter images: {e}")
        finally:
            driver.quit()

    def _extract_manga_id_from_url(self, url: str) -> str:
        """Extract manga ID from WeebCentral URL."""
        # URL format: https://weebcentral.com/series/{manga_id}/{slug}
        match = re.search(r'/series/([^/]+)', url)
        if match:
            return match.group(1)
        return ""

    def _extract_chapter_id_from_url(self, url: str) -> str:
        """Extract chapter ID from WeebCentral chapter URL."""
        # URL format: https://weebcentral.com/chapter/{chapter_id}
        # First try to extract from full URL
        match = re.search(r'/chapter/(\d+)', url)
        if match:
            return match.group(1)

        # If not found, try to extract from the last part of the URL
        last_part = url.split('/')[-1]
        if last_part and last_part != '404':
            return last_part

        # If still not found, return the original URL as fallback
        return url

    def _extract_title(self, soup) -> str:
        """Extract manga title from series page."""
        # Use the correct selector from the original scraper
        title_element = soup.select_one("section[x-data] > section:nth-of-type(2) h1")
        if title_element:
            return title_element.text.strip()

        # Fallback to other selectors
        fallback_selectors = [
            'h3[class*="item-title"]',
            'h1[class*="title"]',
            '.series-title',
            'h1'
        ]

        for selector in fallback_selectors:
            title_element = soup.select_one(selector)
            if title_element:
                return title_element.text.strip()

        return ""

    def _extract_alternative_titles(self, soup) -> List[str]:
        """Extract alternative titles."""
        # WeebCentral doesn't prominently display alternative titles
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
        """Extract author information using WeebCentral-specific selectors."""
        authors = []

        # Use the correct WeebCentral selector for authors
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
        """Extract genre information using WeebCentral-specific selectors."""
        genre_set = set()

        # Use the correct WeebCentral selector for genres
        genre_divs = soup.find_all('div', class_='attr-item')
        for div in genre_divs:
            if div.find('b', class_='text-muted', string='Genres:'):
                # Only get direct text from span elements, skip nested u tags
                # Find the container span first
                container_span = div.find('span')
                if container_span:
                    # Get only direct span children (not u tags)
                    for span in container_span.find_all('span', recursive=False):
                        genre = span.text.strip()
                        if genre and genre not in [',', '']:
                            genre_set.add(genre)

                    # Also get text from u tags (they're at the same level)
                    for u_tag in container_span.find_all('u'):
                        genre = u_tag.text.strip()
                        if genre and genre not in [',', '']:
                            genre_set.add(genre)
                break

        genres = sorted(list(genre_set))

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
        """Extract publication status using WeebCentral-specific selectors."""
        # Use the correct WeebCentral selector for "Original work"
        original_work_element = soup.find('b', class_='text-muted', string='Original work:')
        if original_work_element:
            # For WeebCentral, if it shows "Original work", it typically means it's original/completed
            # This is a heuristic since WeebCentral doesn't always clearly mark status
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

    def _extract_cover_url(self, soup) -> str:
        """Extract cover image URL."""
        cover_img_element = soup.select_one("img[alt$='cover']")
        if cover_img_element and 'src' in cover_img_element.attrs:
            cover_url = cover_img_element['src']
            if not cover_url.startswith(('http://', 'https://')):
                return urljoin(self.base_url, cover_url)
            return cover_url
        return ""

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
        """Extract chapter release date using WeebCentral-specific selectors."""
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