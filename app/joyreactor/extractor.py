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
                # Video logic: search for <video> and <source> tags
                # Prefer <source> inside <video> as it often contains the actual file
                video_tag = soup.find('video')
                if video_tag:
                    source_tag = video_tag.find('source')
                    if source_tag and source_tag.get('src'):
                        return urljoin(self.base_url, source_tag['src'])
                    if video_tag.get('src'):
                        return urljoin(self.base_url, video_tag['src'])
                
                # Fallback to any source tag in the page
                source_tag = soup.find('source')
                if source_tag and source_tag.get('src'):
                    return urljoin(self.base_url, source_tag['src'])
            else:
                # Image logic: search for the main post image
                # Prefer specific post image containers/IDs
                img_tag = soup.find('img', class_='post_image') or soup.find('img', id='post_image')
                
                if not img_tag:
                    # Filtered search: find all imgs and pick the first one that looks like a post image
                    all_imgs = soup.find_all('img', src=True)
                    for img in all_imgs:
                        src = img['src']
                        # Exclude known ads/service images
                        if any(x in src for x in ['mc.yandex.ru', '/avatar/', '/service/', '/ads/']):
                            continue
                        # Prefer post pics
                        if 'img2.joyreactor.cc/pics/post/' in src or 'img.joyreactor.cc/pics/post/' in src:
                            img_tag = img
                            break
                        # If we found a reasonable image that isn't an ad, it's a candidate
                        img_tag = img # Tentative
                    
                if img_tag and img_tag.get('src'):
                    url = img_tag['src']
                    # Convert thumbnail/preview URLs to full size
                    if '/preview/' in url:
                        url = url.replace('/preview/', '/full/')
                    return urljoin(self.base_url, url)
            
            return None
        except Exception as e:
            logger.error("extraction_failed", post_id=post_id, error=str(e))
            return None
