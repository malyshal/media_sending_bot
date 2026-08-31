from bs4 import BeautifulSoup
import structlog
from typing import Optional
from urllib.parse import urljoin

logger = structlog.get_logger()

class JoyReactorExtractor:
    """
    Extracts actual media URLs from JoyReactor post HTML pages.
    Follows the principle of using GraphQL for metadata and HTML for the final URL.
    """
    
    def __init__(self, base_url: str):
        self.base_url = base_url

    async def extract_media_url(self, html_content: str, post_id: str, is_video: bool = False) -> Optional[str]:
        """
        Parses post HTML to find the actual media URL.
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            if is_video:
                # Video logic: search for video tags or specific JS variables
                # JoyReactor often uses <video> tags or specific source patterns
                video_tag = soup.find('video')
                if video_tag and video_tag.get('src'):
                    return urljoin(self.base_url, video_tag['src'])
                
                source_tag = soup.find('source')
                if source_tag and source_tag.get('src'):
                    return urljoin(self.base_url, source_tag['src'])
            else:
                # Image logic: search for the main post image
                # Typically the image is in a specific container or has a predictable class
                img_tag = soup.find('img', class_='post_image') or soup.find('img', id='post_image')
                if not img_tag:
                    # Fallback: find first large image in the post content
                    img_tag = soup.find('img', src=True)
                
                if img_tag and img_tag.get('src'):
                    url = img_tag['src']
                    # Convert thumbnail/preview URLs to full size if needed
                    # gallery-dl often looks for /full/ or replaces preview patterns
                    if '/preview/' in url:
                        url = url.replace('/preview/', '/full/')
                    return urljoin(self.base_url, url)
            
            return None
        except Exception as e:
            logger.error("extraction_failed", post_id=post_id, error=str(e))
            return None
