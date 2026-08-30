from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

@dataclass
class JRPost:
    id: int
    text: Optional[str]
    media_url: str
    media_type: str
    tags: List[str]
    created_at: datetime
    raw_data: Dict[str, Any]

@dataclass
class JRTag:
    id: str
    name: str
    count: int
    nsfw: bool
    unsafe: bool
