"""
MangaPark Provider for MangaForge.

This provider uses Selenium to scrape MangaPark with NSFW mode permanently enabled.
"""
from typing import List, Optional, Union
import time
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webdriver import WebDriver
from bs4 import BeautifulSoup

from core.base_provider import BaseProvider
from core.config import Config
from models import MangaSearchResult, MangaInfo, Chapter
import logging

logger = logging.getLogger(__name__)

# Global config instance
_config = Config()


class MangaParkProvider(BaseProvider):
    """Provider for MangaPark with NSFW mode enabled."""

    provider_id = "mangapark"
    provider_name = "MangaPark"
    base_url = "https://mangapark.net"
    requires_browser = True

    def __init__(self) -> None:
        """Initialize the provider with HTTP session and Selenium setup."""
        super().__init__()  # Initialize HTTP session from BaseProvider
        self.driver: Optional[WebDriver] = None  # Don't initialize Selenium yet
    
    def _initialize_driver_with_nsfw(self):
        """Initialize Chrome driver and enable NSFW settings permanently."""
        logger.info("Initializing MangaPark provider with NSFW mode enabled")
        
        chrome_options = Options()
        chrome_options.add_argument("--non-headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-images")  # Faster loading
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.page_load_strategy = 'eager'
        
        self.driver = webdriver.Chrome(options=chrome_options)
        
        # Enable NSFW settings
        self._enable_nsfw_settings()
    
    def _ensure_driver(self):
        """Initialize driver only when first needed (lazy loading)."""
        if self.driver is None:
            logger.info("Initializing MangaPark provider with NSFW mode enabled")
            self._initialize_driver_with_nsfw()

    def _enable_nsfw_settings(self):
        """Enable NSFW settings - called once during initialization."""
        try:
            logger.info("Enabling NSFW settings...")
            self.driver.get(f"{self.base_url}/site-settings?group=safeBrowsing")
            time.sleep(1)
            
            nsfw_radio = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, 'input[type="radio"][name="safe_reading"][value="2"]')
                )
            )
            nsfw_radio.click()
            logger.info("NSFW settings enabled successfully")
            time.sleep(5)  # Wait for settings to apply
            
        except Exception as e:
            logger.warning(f"Could not enable NSFW settings: {e}")
    
    def search(self, query: str, page: int = 1) -> tuple[List[MangaSearchResult], bool]:
        """
        Search for manga on MangaPark using HTTP requests and BeautifulSoup.

        This is much faster and more efficient than using Selenium for search.
        """
        logger.debug(f"Searching MangaPark for '{query}' on page {page}")

        try:
            # Build search URL and params like Bato provider
            search_url = f"{self.base_url}/search"
            params = {
                'word': query,
                'page': page
            }

            logger.debug(f"Searching MangaPark: {search_url} with params: {params}")

            # Make request with params (like Bato provider)
            response = self.session.get(search_url, params=params)
            response.raise_for_status()

            # Parse HTML with BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')

            results = []

            # Use the working selector from test.py
            manga_items = soup.select("div.grid > div.flex.border-b")

            # Get results limit from config (default to 20 if not found)
            results_limit = _config.get('ui.results_per_page', 20)

            for item in manga_items[:results_limit]:
                try:
                    # Extract title and URL
                    title_tag = item.select_one("h3 a")
                    if not title_tag:
                        continue

                    title = title_tag.get_text(strip=True)
                    href = title_tag.get('href', '')
                    manga_url = urljoin(self.base_url, href)

                    # Extract manga ID from URL
                    manga_id = href.split('/title/')[-1].split('/')[0] if '/title/' in href else ''
                    if not manga_id or not title:
                        continue

                    # Extract thumbnail
                    img_tag = item.select_one("img")
                    cover_url = urljoin(self.base_url, img_tag.get('src', '')) if img_tag else ''

                    # Extract alternative titles
                    alt_titles = []
                    info_blocks = item.select("div.text-xs.opacity-80.line-clamp-2")
                    if len(info_blocks) >= 1:
                        alt_titles = [
                            span.get_text(strip=True)
                            for span in info_blocks[0].select("span")
                            if span.get_text(strip=True)
                        ]

                    # Extract authors
                    authors = []
                    if len(info_blocks) >= 2:
                        authors = [
                            a.get_text(strip=True)
                            for a in info_blocks[1].select("span")
                            if a.get_text(strip=True)
                        ]

                    # Extract rating
                    rating_tag = item.select_one("span.font-bold")
                    rating = rating_tag.get_text(strip=True) if rating_tag else ""

                    # Extract followers
                    follow_tag = item.select_one("div[id^='comic-follow-swap-'] span.ml-1")
                    followers = follow_tag.get_text(strip=True) if follow_tag else ""

                    # Extract genres
                    genres = [
                        g.get_text(strip=True)
                        for g in item.select("div.flex.flex-wrap.text-xs.opacity-70 span.whitespace-nowrap")
                    ]

                    # Extract latest chapter info
                    chapter_tag = item.select_one("a.link-hover.link-primary")
                    latest_chapter = chapter_tag.get_text(strip=True) if chapter_tag else ""
                    latest_chapter_url = urljoin(self.base_url, chapter_tag.get('href', '')) if chapter_tag else ""

                    # Extract update time
                    time_tag = item.select_one("time span")
                    updated = time_tag.get_text(strip=True) if time_tag else ""

                    # Create search result
                    result = MangaSearchResult(
                        provider_id=self.provider_id,
                        manga_id=manga_id,
                        title=title,
                        cover_url=cover_url,
                        url=manga_url
                    )

                    results.append(result)

                    logger.debug(f"Found: {title} | Rating: {rating} | Followers: {followers}")

                except Exception as e:
                    logger.debug(f"Error parsing search result item: {e}")
                    continue

            # Check for pagination
            has_next = self._has_next_page(soup, page)

            logger.info(f"Found {len(results)} results for '{query}' on page {page}")
            return results, has_next

        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise
    
    def get_manga_info(self, manga_id: Optional[str] = None, url: Optional[str] = None) -> MangaInfo:
        """Get detailed manga information."""
        self._ensure_driver()  # Initialize driver only when needed
        try:
            if url:
                manga_url = url
                manga_id = url.split('/title/')[-1].split('/')[0] if '/title/' in url else manga_id
            else:
                manga_url = f"{self.base_url}/title/{manga_id}"

            logger.debug(f"Fetching manga info: {manga_url}")
            self.driver.get(manga_url)
            time.sleep(2)
            
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            # Extract title
            title_elem = soup.select_one('h3.text-lg.md\\:text-2xl.font-bold a')
            title = title_elem.get_text(strip=True) if title_elem else "Unknown"

            # Extract cover
            cover_elem = soup.select_one('img[alt*="cover"], img.object-cover')
            cover_url = cover_elem.get('src', '') if cover_elem else ''

            # Extract description
            desc_elem = soup.select_one('div.limit-html-p')
            description = desc_elem.get_text(strip=True) if desc_elem else ''

            # Extract authors
            authors = []
            author_elems = soup.select('.mt-2.text-sm.md\\:text-base.opacity-80 a')
            for elem in author_elems:
                author = elem.get_text(strip=True)
                if author and author not in authors:
                    authors.append(author)

            # Extract genres
            genres = []
            genre_elems = soup.select('.flex.items-center.flex-wrap span.whitespace-nowrap')
            for elem in genre_elems:
                genre = elem.get_text(strip=True)
                if genre and genre not in genres and genre not in ['Genres:', ',']:
                    genres.append(genre)

            # Extract status
            status = "Unknown"
            status_elem = soup.select_one('.font-bold.uppercase.text-success')
            if status_elem:
                status_text = status_elem.get_text(strip=True).lower()
                if 'ongoing' in status_text:
                    status = "Ongoing"
                elif 'completed' in status_text:
                    status = "Completed"
            
            return MangaInfo(
                provider_id=self.provider_id,
                manga_id=manga_id,
                title=title,
                alternative_titles=[],
                cover_url=cover_url,
                url=manga_url,
                description=description,
                authors=authors,
                artists=[],
                genres=genres,
                status=status,
                year=None
            )

        except Exception as e:
            logger.error(f"Failed to get manga info: {e}")
            raise
    
    def get_chapters(self, manga_id: str) -> List[Chapter]:
        """Get all chapters for a manga."""
        self._ensure_driver()
        try:
            manga_url = f"{self.base_url}/title/{manga_id}"
            logger.debug(f"Fetching chapters from: {manga_url}")

            self.driver.get(manga_url)
            time.sleep(3)
            
            chapter_elements = self.driver.find_elements(
                By.CSS_SELECTOR, 
                'a.link-hover.link-primary.visited\\:text-accent'
            )
            
            if not chapter_elements:
                chapter_elements = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    'a[href*="/title/"][href*="/chapter"]'
                )
            
            chapters = []
            seen_urls = set()
            
            for element in chapter_elements:
                try:
                    title = element.text.strip()
                    href = element.get_attribute('href')

                    if not title or len(title) < 3 or not href:
                        continue

                    if href in seen_urls:
                        continue

                    seen_urls.add(href)

                    # Extract chapter number
                    import re
                    match = re.search(r'(?:chapter|ch\.?)\s*(\d+(?:\.\d+)?)', title.lower())
                    chapter_number = match.group(1) if match else str(len(chapters) + 1)

                    # Extract release date
                    release_date = None
                    try:
                        parent_element = element.find_element(By.XPATH, "./ancestor-or-self::*[contains(@class, 'flex') or contains(@class, 'gap')]/div[last()]")
                        if parent_element:
                            time_elem = parent_element.find_element(By.CSS_SELECTOR, "div.ml-auto.whitespace-nowrap time span")
                            if time_elem:
                                release_date = time_elem.text.strip() if time_elem.text else None
                    except Exception as e:
                        logger.debug(f"Could not extract release date: {e}")

                    chapters.append(Chapter(
                        chapter_id=href,  # ← CHANGED: Store the FULL URL as chapter_id
                        manga_id=manga_id,
                        title=title,
                        chapter_number=chapter_number,
                        volume=None,
                        url=href,  # ← CHANGED: Use full href directly
                        release_date=release_date,
                        language="en"
                    ))
                    
                except Exception as e:
                    logger.debug(f"Error parsing chapter element: {e}")
                    continue
            
            chapters.reverse()
            
            logger.info(f"Found {len(chapters)} chapters")
            return chapters

        except Exception as e:
            logger.error(f"Failed to get chapters: {e}")
            raise
        finally:
            self.cleanup()
    
    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """Get all image URLs for a chapter."""
        # For chapter downloads, we don't need NSFW mode - use separate browser instance
        driver = None
        try:
            # Initialize browser WITHOUT NSFW settings for downloads
            logger.debug("Initializing browser for chapter download (no NSFW needed)")
            chrome_options = Options()
            chrome_options.add_argument("--non-headless")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument("--disable-web-security")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.page_load_strategy = 'eager'

            driver = webdriver.Chrome(options=chrome_options)

            # chapter_id parameter is actually the full URL from Chapter object
            # Use it directly since it should already be properly constructed
            chapter_url = chapter_id

            logger.debug(f"Fetching chapter images: {chapter_url}")
            driver.get(chapter_url)
            time.sleep(5)

            # Wait for images to load
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "img.w-full.h-full"))
                )
            except Exception as e:
                logger.warning(f"Timeout waiting for images: {e}")

            # Find image elements - use the correct selector based on HTML structure
            image_elements = driver.find_elements(By.CSS_SELECTOR, "img.w-full.h-full")

            if not image_elements:
                # Try alternative selectors based on the provided HTML structure
                image_elements = driver.find_elements(By.CSS_SELECTOR, "div[data-name='image-item'] img")

            if not image_elements:
                # Try another fallback selector
                image_elements = driver.find_elements(By.CSS_SELECTOR, "main img")

            image_urls = []
            for img in image_elements:
                img_url = img.get_attribute('src')
                if img_url and img_url.startswith('http'):
                    image_urls.append(img_url)

            logger.info(f"Found {len(image_urls)} images")
            return image_urls

        except Exception as e:
            logger.error(f"Failed to get chapter images: {e}")
            raise
        finally:
            # Clean up the download-specific driver
            if driver:
                try:
                    driver.quit()
                except Exception as e:
                    logger.debug(f"Error closing download driver: {e}")
    
    def cleanup(self):
        """Clean up Selenium driver."""
        if self.driver:
            try:
                logger.info("Closing MangaPark browser driver")
                self.driver.quit()
                self.driver = None
            except Exception as e:
                logger.debug(f"Error closing driver: {e}")

    def _has_next_page(self, soup, current_page: int) -> bool:
        """Check if there's a next page in MangaPark search results."""
        # Use the correct MangaPark pagination selector from provided HTML
        pagination_container = soup.select_one('.flex.items-center.flex-wrap.space-x-1.my-10.justify-center')

        if pagination_container:
            # Look for page links within the pagination container
            page_links = pagination_container.select('a[href*="page"]')

            for link in page_links:
                href = link.get('href', '')
                if href and 'page=' in href:
                    try:
                        # Extract page number from href
                        page_num = int(href.split('page=')[-1].split('&')[0])
                        if page_num > current_page:
                            return True
                    except (ValueError, IndexError):
                        continue

        # Fallback: Check if current page has results (if no results, likely no next page)
        result_items = soup.select("div.grid > div.flex.border-b")
        if len(result_items) > 0:
            # If we got a full page of results, there might be more pages
            results_limit = _config.get('ui.results_per_page', 20)
            return len(result_items) >= results_limit

        return False

    def __del__(self):
        """Clean up Selenium driver."""
        self.cleanup()