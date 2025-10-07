"""
MangaBuddy provider for MangaForge.

This provider implements scraping for MangaBuddy.com.
It extracts manga information, chapter lists, and image URLs for download.
"""
import logging
import re
import json
from typing import List, Optional, Tuple
from urllib.parse import quote, urljoin

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError, ChapterNotFoundError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)


class MangaBuddyProvider(BaseProvider):
    """
    Provider for MangaBuddy.com manga website.

    This provider scrapes manga information from MangaBuddy.com including
    search results, manga details, chapter listings, and image URLs.
    """

    provider_id = "mangabuddy"
    provider_name = "MangaBuddy"
    base_url = "https://mangabuddy.com"

    def __init__(self):
        """Initialize the MangaBuddy provider with cloudscraper."""
        # Don't call super().__init__() since we're using cloudscraper
        import cloudscraper

        self.session = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            }
        )

        # Set headers
        self.session.headers.update(self.get_headers())

        logger.info("MangaBuddy provider initialized with CloudScraper")

    def download_image(self, url: str) -> bytes:
        """
        Download a single image from MangaBuddy (bypasses Cloudflare).

        Args:
            url: Image URL to download

        Returns:
            Image data as bytes
        """
        try:
            logger.debug(f"Downloading image with CloudScraper: {url}")

            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            return response.content

        except Exception as e:
            logger.error(f"Failed to download image {url}: {e}")
            raise ProviderError(f"Image download failed: {e}")

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        """
        Search for manga on MangaBuddy.com.

        Args:
            query: Search query string
            page: Page number for pagination (1-indexed)

        Returns:
            Tuple of (search_results, has_next_page)
        """
        logger.debug(f"Searching MangaBuddy for '{query}' on page {page}")

        try:
            # Build search URL
            search_url = f"{self.base_url}/search"
            params = {
                'q': query,
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
            manga_items = soup.find_all('div', class_='manga-item')

            for item in manga_items:
                title_element = item.find('a', class_='manga-title')
                if title_element:
                    title = title_element.text.strip()
                    url = urljoin(self.base_url, title_element['href'])

                    # Avoid duplicates
                    if url not in seen_urls:
                        seen_urls.add(url)

                        # Extract manga ID from URL
                        manga_id = self._extract_manga_id_from_url(url)

                        result = MangaSearchResult(
                            provider_id=self.provider_id,
                            manga_id=manga_id,
                            title=title,
                            cover_url="",  # MangaBuddy search doesn't show covers easily
                            url=url
                        )
                        results.append(result)

            # Check if there's a next page
            has_next = self._has_next_page(soup, page)

            logger.info(f"MangaBuddy search returned {len(results)} results")
            return results, has_next

        except Exception as e:
            logger.error(f"MangaBuddy search failed: {e}")
            raise ProviderError(f"Search failed: {e}")

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """
        Get detailed manga information from MangaBuddy.com.

        Args:
            manga_id: MangaBuddy manga ID
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
                # manga_id should be just the slug, not a full path
                target_url = f"{self.base_url}/{manga_id}"

            logger.debug(f"Fetching MangaBuddy manga info from: {target_url}")

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
                cover_url="",  # MangaBuddy doesn't show covers on series page easily
                url=target_url,
                description=description,
                authors=authors,
                artists=artists,
                genres=genres,
                status=status,
                year=year
            )

            logger.info(f"Extracted MangaBuddy manga info: {manga_info.title}")
            return manga_info

        except MangaNotFoundError:
            raise
        except Exception as e:
            logger.error(f"MangaBuddy get_manga_info failed: {e}")
            raise ProviderError(f"Failed to get manga info: {e}")

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """
        Get all chapters for a manga from MangaBuddy.com.

        Args:
            manga_id: MangaBuddy manga ID

        Returns:
            List of Chapter objects in reading order
        """
        logger.debug(f"Fetching MangaBuddy chapters for: {manga_id}")

        try:
            # Build manga URL
            manga_url = f"{self.base_url}/{manga_id}"

            # Make request to manga page
            response = self.session.get(manga_url)
            response.raise_for_status()

            # Parse HTML
            soup = self._parse_html(response.text)

            # Extract chapters using MangaBuddy-specific selectors
            chapters = []
            chapter_list_ul = soup.find('ul', class_='chapter-list')

            if chapter_list_ul:
                for li in chapter_list_ul.find_all('li'):
                    chapter_a = li.find('a', href=True)
                    if chapter_a:
                        chapter_title_element = chapter_a.find('strong', class_='chapter-title')
                        chapter_title = chapter_title_element.text.strip() if chapter_title_element else "Unknown Chapter"

                        chapter_url = urljoin(self.base_url, chapter_a['href'])

                        # Extract chapter information
                        chapter_id = self._extract_chapter_id_from_url(chapter_url)
                        chapter_number = self._extract_chapter_number(chapter_title)
                        volume = self._extract_volume(chapter_title)

                        # Extract release date using MangaBuddy-specific time selector
                        release_date_element = chapter_a.find('time', class_='chapter-update')
                        release_date = release_date_element.text.strip() if release_date_element else None

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

            # MangaBuddy chapters are usually listed in descending order, reverse to get ascending
            chapters.reverse()

            logger.info(f"Extracted {len(chapters)} chapters from MangaBuddy")
            return chapters

        except Exception as e:
            logger.error(f"MangaBuddy get_chapters failed: {e}")
            raise ProviderError(f"Failed to get chapters: {e}")

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """
        Get all image URLs for a chapter from MangaBuddy.com using Playwright.

        Args:
            chapter_id: MangaBuddy chapter ID

        Returns:
            List of direct image URLs in reading order
        """
        logger.debug(f"Fetching MangaBuddy chapter images for: {chapter_id}")

        try:
            # Extract manga_id from chapter_id (format: manga-id/chapter-id)
            if '/' in chapter_id:
                manga_slug = chapter_id.split('/')[0]
                chapter_slug = chapter_id.split('/')[-1]
                chapter_url = f"{self.base_url}/{manga_slug}/{chapter_slug}"
            else:
                chapter_url = f"{self.base_url}/{chapter_id}"

            logger.debug(f"Chapter URL: {chapter_url}")

            # Use Playwright to handle dynamic image loading
            image_urls = self._get_chapter_images_with_playwright(chapter_url)

            if not image_urls:
                logger.warning(f"No image URLs found for chapter {chapter_id}")
                return []

            logger.info(f"Extracted {len(image_urls)} image URLs from MangaBuddy chapter")
            return image_urls

        except Exception as e:
            logger.error(f"MangaBuddy get_chapter_images failed: {e}")
            raise ProviderError(f"Failed to get chapter images: {e}")

    def _get_chapter_images_with_playwright(self, chapter_url: str) -> List[str]:
        """Use Playwright to extract image URLs from dynamically loaded content."""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                # Use cloudscraper session for browser context to bypass Cloudflare
                browser = p.chromium.launch(headless=True)

                # Create context with cloudscraper headers
                context = browser.new_context(
                    user_agent=self.get_headers().get('User-Agent', ''),
                    extra_http_headers=self.get_headers()
                )

                page = context.new_page()

                # Navigate to chapter page
                logger.debug(f"Navigating to chapter: {chapter_url}")
                page.goto(chapter_url, wait_until="domcontentloaded", timeout=30000)

                # Wait for images to load
                page.wait_for_timeout(3000)  # Wait 3 seconds for dynamic content

                # Extract image URLs using Playwright
                image_urls = page.evaluate("""
                    () => {
                        const images = [];
                        // Get images from the chapter-images container
                        const container = document.querySelector('div.container#chapter-images');
                        if (container) {
                            const imgElements = container.querySelectorAll('img');
                            imgElements.forEach(img => {
                                const src = img.getAttribute('data-src') || img.getAttribute('src');
                                if (src && src.includes('mbcdns') && (src.endsWith('.jpg') || src.endsWith('.jpeg') || src.endsWith('.png') || src.endsWith('.gif'))) {
                                    images.push(src);
                                }
                            });
                        }
                        return images;
                    }
                """)

                browser.close()

                # Clean URLs (remove query parameters)
                clean_urls = []
                for url in image_urls:
                    if url:
                        clean_url = re.sub(r'\?.*$', '', url)
                        if clean_url:
                            clean_urls.append(clean_url)

                logger.debug(f"Playwright extracted {len(clean_urls)} image URLs")
                return clean_urls

        except ImportError:
            logger.error("Playwright not available for MangaBuddy image extraction")
            # Fallback to cloudscraper method
            return self._get_chapter_images_with_cloudscraper(chapter_url)
        except Exception as e:
            logger.error(f"Playwright extraction failed: {e}")
            # Fallback to cloudscraper method
            return self._get_chapter_images_with_cloudscraper(chapter_url)

    def _get_chapter_images_with_cloudscraper(self, chapter_url: str) -> List[str]:
        """Fallback method using cloudscraper for image extraction."""
        try:
            # Make request with cloudscraper
            response = self.session.get(chapter_url)
            response.raise_for_status()

            # Parse HTML
            soup = self._parse_html(response.text)

            # Extract image URLs
            return self._extract_image_urls(soup)

        except Exception as e:
            logger.error(f"Cloudscraper fallback failed: {e}")
            return []

    def _parse_html(self, html: str):
        """Parse HTML content using BeautifulSoup."""
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, 'html.parser')

    def _extract_manga_id_from_url(self, url: str) -> str:
        """Extract manga ID from MangaBuddy URL."""
        # URL format: https://mangabuddy.com/{manga_id}
        match = re.search(r'://mangabuddy\.com/([^/?]+)', url)
        if match:
            return match.group(1)
        return url.split('/')[-1]

    def _extract_chapter_id_from_url(self, url: str) -> str:
        """Extract chapter ID from MangaBuddy chapter URL."""
        # URL format: https://mangabuddy.com/{manga_id}/{chapter_id}
        # We need to return: {manga_id}/{chapter_id}
        
        match = re.search(r'://mangabuddy\.com/([^/]+)/([^/?]+)', url)
        if match:
            manga_slug = match.group(1)
            chapter_slug = match.group(2)
            return f"{manga_slug}/{chapter_slug}"
        
        # Fallback: try to extract last two parts
        parts = url.rstrip('/').split('/')
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
        
        return url.split('/')[-1]

    def _extract_title(self, soup) -> str:
        """Extract manga title from manga page."""
        # Try multiple selectors for title
        title_selectors = [
            'div.name.box h1',
            '.manga-info h1',
            '.manga-title',
            'h1'
        ]

        for selector in title_selectors:
            title_element = soup.select_one(selector)
            if title_element:
                return title_element.text.strip()

        return ""

    def _extract_alternative_titles(self, soup) -> List[str]:
        """Extract alternative titles."""
        # MangaBuddy doesn't prominently display alternative titles
        return []

    def _extract_description(self, soup) -> str:
        """Extract manga description."""
        # Look for description in various possible locations
        desc_selectors = [
            '.manga-summary',
            '.summary',
            '.description',
            'div[class*="summary"]'
        ]

        for selector in desc_selectors:
            desc_element = soup.select_one(selector)
            if desc_element:
                return desc_element.text.strip()

        return ""

    def _extract_authors(self, soup) -> List[str]:
        """Extract author information using MangaBuddy-specific selectors."""
        authors = []

        # Use the specific MangaBuddy author selector
        author_p = soup.find('p', string=lambda text: text and 'Authors :' in text)
        if author_p:
            author_links = author_p.find_all('a')
            for link in author_links:
                author = link.text.strip()
                if author and author not in authors:
                    authors.append(author)

        # Fallback to generic selectors if specific one doesn't work
        if not authors:
            author_selectors = ['.manga-author', '.author', '[itemprop="author"]']
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
            '.manga-artist',
            '.artist',
            'span:contains("Artist")',
            'div[class*="artist"]'
        ]

        for selector in artist_selectors:
            artist_elements = soup.select(selector)
            for element in artist_elements:
                artist = element.text.strip()
                if artist and artist not in artists:
                    artists.append(artist)

        return artists

    def _extract_genres(self, soup) -> List[str]:
        """Extract genre information using MangaBuddy-specific selectors."""
        genres = []

        # Use the specific MangaBuddy genre selector
        genre_p = soup.find('p', string=lambda text: text and 'Genres :' in text)
        if genre_p:
            genre_links = genre_p.find_all('a')
            for link in genre_links:
                genre = link.text.strip()
                if genre and genre not in genres:
                    genres.append(genre)

        # Fallback to generic selectors if specific one doesn't work
        if not genres:
            genre_selectors = ['.manga-genres', '.genres', '.genre', '[itemprop="genre"]']
            for selector in genre_selectors:
                genre_elements = soup.select(selector)
                for element in genre_elements:
                    genre = element.text.strip()
                    if genre and genre not in genres:
                        genres.append(genre)

        return genres

    def _extract_status(self, soup) -> str:
        """Extract publication status."""
        # Look for status indicators
        status_selectors = [
            '.manga-status',
            '.status',
            '.publication-status'
        ]

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
            '.manga-year',
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
        """Extract chapter release date using MangaBuddy-specific selectors."""
        # This method is now handled directly in get_chapters method
        # since we have access to the time element there
        return None

    def _extract_image_urls(self, soup) -> List[str]:
        """Extract image URLs from chapter page using MangaBuddy-specific selectors."""
        image_urls = []

        # Use the correct MangaBuddy image selector
        chapter_images_div = soup.find('div', class_='container', id='chapter-images')
        if chapter_images_div:
            img_elements = chapter_images_div.find_all('img')
            for img in img_elements:
                # Get the image URL from data-src or src attribute
                img_url = img.get('data-src') or img.get('src')
                if img_url and img_url.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                    # Clean up the URL (remove query parameters if present)
                    clean_url = re.sub(r'\?.*$', '', img_url)
                    if clean_url:
                        image_urls.append(clean_url)

        # If no images found with the specific selector, try fallback
        if not image_urls:
            # Look for any img tags with manga-related URLs
            all_img_elements = soup.find_all('img')
            for img in all_img_elements:
                img_url = img.get('data-src') or img.get('src')
                if img_url and 'mbcdns' in img_url and img_url.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                    clean_url = re.sub(r'\?.*$', '', img_url)
                    if clean_url and clean_url not in image_urls:
                        image_urls.append(clean_url)

        logger.debug(f"Found {len(image_urls)} image URLs")
        return image_urls

    def _has_next_page(self, soup, current_page: int) -> bool:
        """Check if there's a next page in search results."""
        # Look for pagination elements
        pagination_selectors = [
            '.pagination .next',
            'a[href*="page"]',
            '.pager .next'
        ]

        for selector in pagination_selectors:
            next_element = soup.select_one(selector)
            if next_element:
                return True

        return False

    def get_headers(self) -> dict:
        """Override headers for MangaBuddy-specific requirements."""
        config = Config()
        headers = {
            'User-Agent': config.get('network.user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'),
            'Referer': self.base_url,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        return headers