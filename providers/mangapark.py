"""
MangaPark provider for MangaForge.

This provider implements scraping for MangaPark.net (formerly MangaPark.to).
It extracts manga information, chapter lists, and image URLs for download.
"""
import logging
import re
import json
from typing import List, Optional, Tuple
from urllib.parse import urljoin, quote

from core.base_provider import BaseProvider, ProviderError, MangaNotFoundError
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter

logger = logging.getLogger(__name__)


class MangaParkProvider(BaseProvider):
    """
    Provider for MangaPark.net manga website.

    This provider scrapes manga information from MangaPark.net including
    search results, manga details, chapter listings, and image URLs.

    Supports both SFW and NSFW content modes.
    """

    provider_id = "mangapark"
    provider_name = "MangaPark"
    base_url = "https://mangapark.net"

    def __init__(self):
        """Initialize the MangaPark provider."""
        super().__init__()
        self.config = Config()
        logger.info("MangaPark provider initialized")

    def search(self, query: str, page: int = 1) -> Tuple[List[MangaSearchResult], bool]:
        """
        Search for manga on MangaPark.net.

        Args:
            query: Search query string
            page: Page number for pagination (1-indexed)

        Returns:
            Tuple of (search_results, has_next_page)
        """
        logger.debug(f"Searching MangaPark for '{query}' on page {page}")

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

            # Find all manga items in search results - try multiple selectors
            manga_items = []

            # Try different selectors for search results
            selectors_to_try = [
                'div.flex.flex-col.gap-2',
                '.search-results .item',
                '.manga-item',
                'div[class*="item"]',
                'div[class*="manga"]'
            ]

            for selector in selectors_to_try:
                manga_items = soup.select(selector)
                if manga_items:
                    logger.debug(f"Found {len(manga_items)} manga items using selector: {selector}")
                    break

            if not manga_items:
                logger.warning("No manga items found in search results")
                return [], False

            for item in manga_items:
                try:
                    # Try multiple ways to find title and URL
                    title_element = None
                    title_selectors = [
                        'h3.font-bold',
                        'h4.font-bold',
                        'a.font-bold',
                        '.title',
                        '.manga-title'
                    ]

                    for selector in title_selectors:
                        title_element = item.select_one(selector)
                        if title_element:
                            break

                    if not title_element:
                        continue

                    title = title_element.text.strip()

                    # Find URL
                    url_element = title_element.find('a') if title_element.name != 'a' else title_element
                    url = url_element.get('href') if url_element else None

                    if not url or url in seen_urls:
                        continue

                    seen_urls.add(url)

                    # Construct absolute URL
                    if url.startswith('http'):
                        absolute_url = url
                    else:
                        absolute_url = urljoin(self.base_url, url)

                    # Extract manga ID from URL
                    manga_id = self._extract_manga_id_from_url(absolute_url)

                    # Try to find cover image
                    cover_element = item.find('img')
                    cover_url = ""
                    if cover_element and cover_element.get('src'):
                        src = cover_element['src']
                        if src.startswith('http'):
                            cover_url = src
                        else:
                            cover_url = urljoin(self.base_url, src)

                    result = MangaSearchResult(
                        provider_id=self.provider_id,
                        manga_id=manga_id,
                        title=title,
                        cover_url=cover_url,
                        url=absolute_url
                    )
                    results.append(result)

                except Exception as e:
                    logger.debug(f"Error processing search result item: {e}")
                    continue

            # Check if there's a next page
            has_next = self._has_next_page(soup, page)

            logger.info(f"MangaPark search returned {len(results)} results")
            return results, has_next

        except Exception as e:
            logger.error(f"MangaPark search failed: {e}")
            raise ProviderError(f"Search failed: {e}")

    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """
        Get detailed manga information from MangaPark.net.

        Args:
            manga_id: MangaPark manga ID
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
                target_url = f"{self.base_url}/title/{manga_id}"

            logger.debug(f"Fetching MangaPark manga info from: {target_url}")

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

            # Try to find cover image
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

            logger.info(f"Extracted MangaPark manga info: {manga_info.title}")
            return manga_info

        except MangaNotFoundError:
            raise
        except Exception as e:
            logger.error(f"MangaPark get_manga_info failed: {e}")
            raise ProviderError(f"Failed to get manga info: {e}")

    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """
        Get all chapters for a manga from MangaPark.net.

        Args:
            manga_id: MangaPark manga ID

        Returns:
            List of Chapter objects in reading order
        """
        logger.debug(f"Fetching MangaPark chapters for: {manga_id}")

        try:
            # Build manga URL
            manga_url = f"{self.base_url}/title/{manga_id}"

            # Make request
            response = self.session.get(manga_url)
            response.raise_for_status()

            # Parse HTML
            soup = self._parse_html(response.text)

            # Extract chapters using the exact HTML structure provided
            chapters = []

            # Find the chapter list container first
            chapter_list_container = soup.find('div', {'data-name': 'chapter-list', 'class': 'space-y-5'})
            if not chapter_list_container:
                logger.warning("Chapter list container not found")
                return chapters

            # Find all chapter links first, then extract information from each
            all_chapter_links = chapter_list_container.find_all('a', class_='link-hover link-primary visited:text-accent')

            logger.info(f"Found {len(all_chapter_links)} chapter links total")

            # Process each chapter link and extract complete information
            seen_urls = set()

            for link in all_chapter_links:
                try:
                    chapter_title = link.text.strip()
                    if 'Chapter' not in chapter_title:
                        continue

                    chapter_url = self.base_url + link['href']

                    # Skip duplicates
                    if chapter_url in seen_urls:
                        continue
                    seen_urls.add(chapter_url)

                    # Extract chapter information
                    chapter_id = self._extract_chapter_id_from_url(chapter_url)
                    chapter_number = self._extract_chapter_number(chapter_title)

                    # Find the parent container to extract additional info
                    parent_div = link.find_parent('div', class_='px-2 py-2 flex flex-wrap justify-between hover:bg-accent/5 border-b border-base-300/50 group-[.flex-col]:last:border-b-0 group-[.flex-col-reverse]:first:border-b-0')

                    # Extract uploader info
                    uploader = "Unknown"
                    if parent_div:
                        uploader_container = parent_div.find('div', class_='ml-auto inline-flex flex-wrap justify-end items-center text-sm opacity-70 space-x-2')
                        if uploader_container:
                            uploader_link = uploader_container.find('a', class_='link-hover link-primary')
                            if uploader_link:
                                uploader = uploader_link.text.strip()

                    # Extract release date
                    release_date = None
                    if parent_div:
                        time_element = parent_div.find('time')
                        if time_element:
                            date_span = time_element.find('span', {'data-passed': True})
                            if date_span:
                                release_date = date_span.text.strip()

                    chapter = Chapter(
                        chapter_id=chapter_id,
                        manga_id=manga_id,
                        title=chapter_title,
                        chapter_number=chapter_number,
                        volume=None,
                        url=chapter_url,
                        release_date=release_date,
                        language="en"
                    )
                    chapters.append(chapter)

                except Exception as e:
                    logger.debug(f"Error processing chapter link: {e}")
                    continue

            # Sort chapters by number (newest first for now, will reverse later)
            def sort_key(chapter):
                try:
                    num = float(chapter.chapter_number)
                    return num
                except ValueError:
                    return 999  # Put special chapters at the end

            chapters.sort(key=sort_key)

            logger.info(f"Successfully processed {len(chapters)} chapters from MangaPark")
            return chapters

        except Exception as e:
            logger.error(f"MangaPark get_chapters failed: {e}")
            raise ProviderError(f"Failed to get chapters: {e}")

    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """
        Get all image URLs for a chapter from MangaPark.net using Selenium (like the original script).

        Args:
            chapter_id: The full URL of the chapter, as returned by `_extract_chapter_id_from_url`.

        Returns:
            List of direct image URLs in reading order
        """
        chapter_url = chapter_id  # The chapter_id is the full URL
        logger.debug(f"Fetching MangaPark chapter images from URL: {chapter_url}")

        # Import Selenium here to avoid import errors if not available
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            import time
        except ImportError:
            logger.error("Selenium not available for MangaPark image extraction")
            raise ProviderError("Selenium required for MangaPark image extraction")

        driver = None
        try:
            # Setup Chrome options for faster performance (like original script)
            chrome_options = Options()
            chrome_options.add_argument("--non-headless")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--disable-features=VizDisplayCompositor")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-extensions")
            chrome_options.add_argument("--disable-default-apps")
            chrome_options.add_argument("--disable-background-timer-throttling")
            chrome_options.add_argument("--disable-renderer-backgrounding")
            chrome_options.add_argument("--disable-backgrounding-occluded-windows")
            chrome_options.add_argument("--disable-ipc-flooding-protection")
            chrome_options.page_load_strategy = 'eager'

            # Initialize browser
            driver = webdriver.Chrome(options=chrome_options)

            # Navigate to the chapter
            driver.get(chapter_url)

            # Wait for the page to load (like original script)
            time.sleep(10)

            # Wait for images to load (like original script)
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "img.w-full.h-full"))
                )
                logger.debug("Images loaded successfully")
            except Exception as e:
                logger.debug(f"Timeout waiting for images: {e}")

            # Find all image containers using the exact selector provided
            image_containers = driver.find_elements(By.CSS_SELECTOR, "div[data-name='image-item']")

            if not image_containers:
                logger.warning(f"No image containers found for chapter {chapter_url}")
                return []

            # Get all image URLs from the containers
            image_urls = []
            for container in image_containers:
                img_element = container.find_element(By.CSS_SELECTOR, "img.w-full.h-full")
                if img_element:
                    img_url = img_element.get_attribute('src')
                    if img_url:
                        image_urls.append(img_url)

            logger.info(f"Extracted {len(image_urls)} image URLs from MangaPark chapter using Selenium")
            return image_urls

        except Exception as e:
            logger.error(f"MangaPark get_chapter_images failed: {e}")
            raise ProviderError(f"Failed to get chapter images: {e}")
        finally:
            if driver:
                driver.quit()

    def _parse_html(self, html: str):
        """Parse HTML content using BeautifulSoup."""
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, 'html.parser')

    def _extract_manga_id_from_url(self, url: str) -> str:
        """Extract manga ID from MangaPark URL."""
        # URL format: https://mangapark.net/title/{manga_id}
        match = re.search(r'/title/(\d+)', url)
        if match:
            return match.group(1)

        # Fallback: try to extract from other URL formats
        match = re.search(r'/c\d+', url)
        if match:
            # This is a chapter URL, extract manga ID from it
            chapter_match = re.search(r'/c(\d+)', url)
            if chapter_match:
                return chapter_match.group(1)

        # If no pattern matches, return the last part of the URL
        return url.split('/')[-1]

    def _extract_chapter_id_from_url(self, url: str) -> str:
        """Extract chapter ID from MangaPark chapter URL."""
        # The URL is the ID for mangapark
        return url

    def _extract_title(self, soup) -> str:
        """Extract manga title from manga page."""
        # Use the exact working selector from the test script
        title_block = soup.select_one("div.space-y-2.hidden.md\\:block")
        if title_block:
            title_element = title_block.select_one("h3 a")
            if title_element:
                return title_element.text.strip()

        return ""

    def _extract_alternative_titles(self, soup) -> List[str]:
        """Extract alternative titles."""
        # Use the exact working selector from the test script
        title_block = soup.select_one("div.space-y-2.hidden.md\\:block")
        if title_block:
            alt_titles = [span.text.strip() for span in title_block.select("div.mt-1 span")]
            return alt_titles
        return []

    def _extract_description(self, soup) -> str:
        """Extract manga description."""
        # Use the exact selector from the working HTML structure
        desc_element = soup.select_one('div.limit-html-p')
        if desc_element:
            return desc_element.text.strip()

        # Fallback to generic selectors if specific one doesn't work
        desc_selectors = [
            '.series-summary',
            '.summary',
            '.description',
            '[itemprop="description"]'
        ]

        for selector in desc_selectors:
            desc_element = soup.select_one(selector)
            if desc_element:
                return desc_element.text.strip()

        return ""

    def _extract_authors(self, soup) -> List[str]:
        """Extract author information."""
        authors = []

        # Use the exact working selector from the test script
        title_block = soup.select_one("div.space-y-2.hidden.md\\:block")
        if title_block:
            authors = [a.text.strip() for a in title_block.select("div.mt-2 a")]

        # Fallback to generic selectors if specific one doesn't work
        if not authors:
            author_selectors = [
                '.series-author',
                '.author',
                '[itemprop="author"]',
                'a[href*="author"]'
            ]

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

        # Use the same selector as authors since MangaPark may list them together
        title_block = soup.select_one("div.space-y-2.hidden.md\\:block")
        if title_block:
            # Artists might be in the same section as authors
            creators = [a.text.strip() for a in title_block.select("div.mt-2 a")]
            # For now, assume all creators are authors until we can distinguish
            # In a real implementation, you might need to check the context
            artists = creators

        # Fallback to generic selectors if specific one doesn't work
        if not artists:
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
        """Extract genre information."""
        genres = []

        # Use the exact working selector from the test script
        genres_div = soup.select_one("div.flex.items-center.flex-wrap")
        if genres_div:
            genres = [g.text.strip() for g in genres_div.select("span span")]

        # Fallback to generic selectors if specific one doesn't work
        if not genres:
            genre_selectors = [
                '.series-genres',
                '.genres',
                '.genre',
                '[itemprop="genre"]',
                '.badge'
            ]

            for selector in genre_selectors:
                genre_elements = soup.select(selector)
                for element in genre_elements:
                    genre = element.text.strip()
                    if genre and genre not in genres:
                        genres.append(genre)

        return genres

    def _extract_status(self, soup) -> str:
        """Extract publication status."""
        # Use BeautifulSoup's find method with a function to find the q:key attribute
        status_container = soup.find('div', attrs={'q:key': 'Yn_8'})
        if status_container:
            status_span = status_container.find('span', class_='font-bold uppercase')
            if status_span:
                status = status_span.text.strip().lower()
                if "ongoing" in status:
                    return "Ongoing"
                elif "completed" in status:
                    return "Completed"
                elif "hiatus" in status:
                    return "Hiatus"

        # Fallback to generic selectors if specific one doesn't work
        status_selectors = [
            '.series-status',
            '.status',
            '.publication-status',
            '.text-success',
            '.font-bold.uppercase'
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
        # Look for cover image
        cover_selectors = [
            '.series-cover img',
            '.cover img',
            'img[alt*="cover"]',
            'img[src*="cover"]'
        ]

        for selector in cover_selectors:
            cover_element = soup.select_one(selector)
            if cover_element and cover_element.get('src'):
                src = cover_element['src']
                if src.startswith('http'):
                    return src
                else:
                    return urljoin(self.base_url, src)

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
        """Extract chapter release date."""
        # Look for date information near the chapter element
        try:
            # Try to find parent container that might contain date
            parent = chapter_element.find_parent()
            if parent:
                # Look for date patterns in the parent
                date_text = parent.find(string=re.compile(r'\d{4}-\d{2}-\d{2}|\d+ days ago'))
                if date_text:
                    return date_text.strip()
        except Exception as e:
            logger.debug(f"Could not extract date for chapter: {e}")

        return None

    def _extract_image_urls(self, soup) -> List[str]:
        """Extract image URLs from chapter page."""
        image_urls = []

        # Look for image URLs in script tags (common in modern sites)
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string:
                # Look for image arrays or URLs in JavaScript
                if 'imgHttp' in script.string or 'images' in script.string:
                    # Try to extract URLs using regex
                    url_matches = re.findall(r'https?://[^\s",]+?\.(jpg|jpeg|png|webp)', script.string)
                    for url in url_matches:
                        if url not in image_urls:
                            image_urls.append(url)

        # Also look for direct image elements - use the selector from the original script
        if not image_urls:
            # Try the exact selector from the working script: "img.w-full.h-full"
            img_elements = soup.select('img.w-full.h-full')
            if not img_elements:
                # Fallback to main img elements
                img_elements = soup.select('main img')

            for img in img_elements:
                src = img.get('src', '')
                if src and src.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    if src.startswith('http'):
                        image_urls.append(src)
                    else:
                        image_urls.append(urljoin(self.base_url, src))

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
            '.pager .next',
            '.next'
        ]

        for selector in pagination_selectors:
            next_element = soup.select_one(selector)
            if next_element:
                return True

        # Check if current page has results (if no results, likely no next page)
        result_items = soup.find_all('div', class_='flex flex-col gap-2')
        return len(result_items) > 0