from typing import List, Optional, Dict, Any, Union
from dataclasses import dataclass
from datetime import datetime

@dataclass
class JRMedia:
    type: str  # "photo" or "video"
    id: int
    url: str
    preview_url: Optional[str] = None

@dataclass
class JRContentItem:
    type: str  # "text" or "media"
    content: Optional[str] = None
    media: Optional[JRMedia] = None

@dataclass
class JRPost:
    id: str
    text: Optional[str]
    content: List[JRContentItem]
    tags: List[str]
    created_at: datetime
    raw_data: Dict[str, Any]
    media_url: Optional[str] = None
    media_type: Optional[str] = None  # "image" | "video" | "gif"
    # Ordered list of all media items (TS #83: text + multiple media)
    media_urls: Optional[list] = None  # list[tuple[url, media_type]]

@dataclass
class JRTag:
    id: str
    name: str
    count: int
    nsfw: bool
    unsafe: bool
