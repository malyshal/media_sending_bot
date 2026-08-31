import re
import structlog
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin
from .models import JRContentItem, JRMedia

logger = structlog.get_logger()

class JoyReactorExtractor:
    """
    Handles normalization of JoyReactor posts by mapping GraphQL data
    and text placeholders to a sequence of content items.
    """
    
    def __init__(self, base_url: str):
        self.base_url = base_url

    def normalize_post(self, post_id: str, text: Optional[str], attributes: List[Dict[str, Any]], slug: str = "") -> List[JRContentItem]:
        """
        Transforms raw GraphQL data into a normalized list of content items.
        Implements the logic from TS.MD Section 12.
        """
        if not text:
            text = ""

        # 1. Extract pictures and their properties
        pictures = []
        for idx, attr in enumerate(attributes):
            if attr.get("__typename") == "PostAttributePicture":
                img_data = attr.get("image", {})
                pic_id = attr.get("id")
                
                pictures.append({
                    "attribute_index": idx,
                    "picture_index": len(pictures) + 1,
                    "id": pic_id,
                    "has_video": img_data.get("hasVideo", False),
                    "type": img_data.get("type", "UNKNOWN")
                })

        # 2. Parse text for placeholders &attribute_insert_N&
        # This splits the text while keeping the delimiters
        parts = re.split(r'(&attribute_insert_(\d+)&)', text)
        
        # re.split with capturing groups returns: [text, delimiter, group1, text, delimiter, group1, ...]
        normalized_content = []
        i = 0
        while i < len(parts):
            part = parts[i]
            if not part:
                i += 1
                continue
            
            if part.startswith("&attribute_insert_") and part.endswith("&"):
                # This is a placeholder
                try:
                    # The number is in the next element of the parts list
                    pic_idx = int(parts[i+1])
                    
                    # Find the picture with this picture_index
                    pic = next((p for p in pictures if p["picture_index"] == pic_idx), None)
                    
                    if pic:
                        normalized_content.append(JRContentItem(
                            type="media",
                            media=self._build_media_object(pic, slug)
                        ))
                    else:
                        logger.error("media_not_found", post_id=post_id, picture_index=pic_idx)
                except (IndexError, ValueError):
                    logger.error("invalid_placeholder", post_id=post_id, part=part)
                
                i += 2 # Skip the delimiter and the captured number
            else:
                # This is regular text
                normalized_content.append(JRContentItem(
                    type="text",
                    content=part
                ))
                i += 1

        return normalized_content

    def _build_media_object(self, pic: Dict[str, Any], slug: str) -> JRMedia:
        """
        Constructs the final JRMedia object with URLs based on TS.MD Section 6 & 7.
        """
        numeric_id = pic["id"]
        host = "img.joyreactor.cc" 
        
        if pic["has_video"]:
            return JRMedia(
                type="video",
                id=int(numeric_id),
                url=f"https://{host}/pics/post/webm/{slug}-{numeric_id}.webm",
                preview_url=f"https://{host}/pics/post/static/{slug}-{numeric_id}.jpeg"
            )
        else:
            ext = "jpg" 
            return JRMedia(
                type="photo",
                id=int(numeric_id),
                url=f"https://{host}/pics/post/{slug}-{numeric_id}.{ext}"
            )
