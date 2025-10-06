"""
MangaPark Provider for MangaForge.

This provider uses Selenium to scrape MangaPark with NSFW mode permanently enabled.
"""
from typing import List, Optional
import time
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

from core.base_provider import BaseProvider
from models import MangaSearchResult, MangaInfo, Chapter
import logging

logger = logging.getLogger(__name__)


class MangaParkProvider(BaseProvider):
    """Provider for MangaPark with NSFW mode enabled."""
    
    provider_id = "mangapark"
    provider_name = "MangaPark"
    base_url = "https://mangapark.net"
    requires_browser = True
    
    def __init__(self):
        """Initialize the provider WITHOUT starting Selenium yet."""
        self.driver = None  # Don't initialize here
    
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
        Search for manga on MangaPark.

        Note: MangaPark search is limited, so we return has_next=False always.
        """
        self._ensure_driver()  # Initialize driver only when search is called
        try:
            search_url = f"{self.base_url}/search?word={query}"
            logger.debug(f"Searching MangaPark: {search_url}")

            self.driver.get(search_url)
            time.sleep(2)
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            results = []
            # Find manga items in search results
            manga_items = soup.select('div.flex.gap-2')
            
            for item in manga_items[:10]:  # Limit to 10 results per page
                try:
                    link_elem = item.select_one('a[href*="/title/"]')
                    if not link_elem:
                        continue
                    
                    title = link_elem.get_text(strip=True)
                    href = link_elem.get('href', '')
                    
                    # Extract manga ID from URL
                    manga_id = href.split('/title/')[-1].split('/')[0] if '/title/' in href else ''
                    
                    # Get cover image
                    img_elem = item.select_one('img')
                    cover_url = img_elem.get('src', '') if img_elem else ''
                    
                    if title and manga_id:
                        results.append(MangaSearchResult(
                            provider_id=self.provider_id,
                            manga_id=manga_id,
                            title=title,
                            cover_url=cover_url,
                            url=urljoin(self.base_url, href)
                        ))
                except Exception as e:
                    logger.debug(f"Error parsing search result item: {e}")
                    continue
            
            logger.info(f"Found {len(results)} results for '{query}'")
            return results, False  # MangaPark doesn't have clear pagination
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise
    
    def get_manga_info(self, manga_id: str = None, url: str = None) -> MangaInfo:
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
            title_elem = soup.select_one('h1, h2.text-2xl')
            title = title_elem.get_text(strip=True) if title_elem else "Unknown"
            
            # Extract cover
            cover_elem = soup.select_one('img[alt*="cover"], img.object-cover')
            cover_url = cover_elem.get('src', '') if cover_elem else ''
            
            # Extract description
            desc_elem = soup.select_one('div.summary, div[class*="description"]')
            description = desc_elem.get_text(strip=True) if desc_elem else ''
            
            # Extract authors
            authors = []
            author_elems = soup.select('a[href*="/author/"]')
            for elem in author_elems:
                author = elem.get_text(strip=True)
                if author and author not in authors:
                    authors.append(author)
            
            # Extract genres
            genres = []
            genre_elems = soup.select('a[href*="/genre/"]')
            for elem in genre_elems:
                genre = elem.get_text(strip=True)
                if genre and genre not in genres:
                    genres.append(genre)
            
            # Extract status
            status = "Unknown"
            status_elem = soup.select_one('div:contains("Status")')
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
        try:
            manga_url = f"{self.base_url}/title/{manga_id}"
            logger.debug(f"Fetching chapters from: {manga_url}")
            
            self.driver.get(manga_url)
            time.sleep(3)
            
            # Find chapter elements
            chapter_elements = self.driver.find_elements(
                By.CSS_SELECTOR, 
                'a.link-hover.link-primary.visited\\:text-accent'
            )
            
            if not chapter_elements:
                # Try alternative selector
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
                    
                    # Extract chapter ID from URL
                    chapter_id = href.split('/')[-1] if '/' in href else href
                    
                    # Try to extract chapter number from title
                    import re
                    match = re.search(r'(?:chapter|ch\.?)\s*(\d+(?:\.\d+)?)', title.lower())
                    chapter_number = match.group(1) if match else str(len(chapters) + 1)
                    
                    chapters.append(Chapter(
                        chapter_id=chapter_id,
                        manga_id=manga_id,
                        title=title,
                        chapter_number=chapter_number,
                        volume=None,
                        url=href if href.startswith('http') else urljoin(self.base_url, href),
                        release_date=None,
                        language="en"
                    ))
                    
                except Exception as e:
                    logger.debug(f"Error parsing chapter element: {e}")
                    continue
            
            # Reverse to get chapters in reading order (oldest first)
            chapters.reverse()
            
            logger.info(f"Found {len(chapters)} chapters")
            return chapters
            
        except Exception as e:
            logger.error(f"Failed to get chapters: {e}")
            raise
    
    def get_chapter_images(self, chapter_id: str) -> List[str]:
        """Get all image URLs for a chapter."""
        try:
            # Construct chapter URL - chapter_id is already the full URL from get_chapters
            chapter_url = chapter_id if chapter_id.startswith('http') else f"{self.base_url}/chapter/{chapter_id}"
            
            logger.debug(f"Fetching chapter images: {chapter_url}")
            self.driver.get(chapter_url)
            time.sleep(5)
            
            # Wait for images to load
            try:
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "img.w-full.h-full"))
                )
            except Exception as e:
                logger.warning(f"Timeout waiting for images: {e}")
            
            # Find image elements
            image_elements = self.driver.find_elements(By.CSS_SELECTOR, "img.w-full.h-full")
            
            if not image_elements:
                # Try alternative selector
                image_elements = self.driver.find_elements(By.CSS_SELECTOR, "main img")
            
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
    
    def __del__(self):
        """Clean up Selenium driver."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.debug(f"Error closing driver: {e}")