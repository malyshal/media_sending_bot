from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
from app.core.config import settings
from .models import JRPost, JRTag
from .queries import SEARCH_TAGS_QUERY, FETCH_POSTS_QUERY
from .extractor import JoyReactorExtractor
import httpx
import structlog

logger = structlog.get_logger()

class JoyReactorClient:
    def __init__(self):
        self.api_url = settings.joyreactor_api_url
        self.base_url = settings.joyreactor_base_url
        self.extractor = JoyReactorExtractor(self.base_url)
        
        self.headers = {
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        self.timeout = httpx.Timeout(
            timeout=10.0, 
            connect=5.0, 
            read=15.0, 
            write=5.0, 
            pool=5.0
        )
        
        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            headers=self.headers
        )

    async def execute(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        
        try:
            response = await self.client.post(self.api_url, json=payload)
            
            if response.status_code == 403:
                logger.error("joyreactor_api_forbidden", status_code=403, url=self.api_url)
                raise httpx.HTTPStatusError("JoyReactor API returned 403 Forbidden", request=response.request, response=response)
                
            response.raise_for_status()
            data = response.json()
            
            if "errors" in data:
                logger.error("graphql_errors", errors=data["errors"], query=query[:100])
                raise Exception(f"GraphQL errors: {data['errors']}")
                
            return data.get("data", {})
        except httpx.TimeoutException as e:
            logger.error("joyreactor_api_timeout", error=str(e))
            raise e
        except httpx.HTTPStatusError as e:
            logger.error("http_error", status_code=e.response.status_code)
            raise e
        except Exception as e:
            logger.error("request_failed", error=str(e))
            raise e

    async def close(self):
        await self.client.aclose()

    async def get_post_html(self, post_id: str) -> Optional[str]:
        url = f"{self.base_url}/post/{post_id}"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error("fetch_post_html_failed", post_id=post_id, error=str(e))
            return None

    def _normalize_media_type(self, api_type: str) -> str:
        api_type = api_type.lower()
        if any(ext in api_type for ext in ["jpg", "jpeg", "png", "webp"]):
            return "image"
        if any(ext in api_type for ext in ["gif", "mp4", "webm"]):
            return "video"
        return "image" # Default

    async def fetch_posts_by_tag(self, tag_name: str, page: int = 1) -> List[JRPost]:
        variables = {"tagName": tag_name, "page": page}
        data = await self.execute(FETCH_POSTS_QUERY, variables)
        
        posts_data = data.get("tag", {}).get("postPager", {}).get("posts", [])
        results = []
        for p in posts_data:
            attributes = p.get("attributes", [])
            media_url, media_type = "", "UNKNOWN"
            
            for attr in attributes:
                if attr and "image" in attr:
                    img = attr["image"]
                    media_type_val = "video" if img.get("hasVideo") else img.get("type", "UNKNOWN")
                    media_url = f"resolve://{p['id']}" 
                    media_type = media_type_val
                    break 
            
            results.append(JRPost(
                id=str(p["id"]),
                text=p.get("text"),
                media_url=media_url,
                media_type=self._normalize_media_type(media_type),
                tags=[pt["tag"]["name"] for pt in p.get("postTags", [])],
                created_at=datetime.fromisoformat(p["createdAt"]),
                raw_data=p
            ))
        return results

    async def search_tags(self, mask: str) -> List[JRTag]:
        variables = {"mask": mask}
        data = await self.execute(SEARCH_TAGS_QUERY, variables)
        tags_data = data.get("tagAutocomplete", [])
        
        return [
            JRTag(
                id=t["id"],
                name=t["name"],
                count=t.get("count", 0),
                nsfw=t.get("nsfw", False),
                unsafe=t.get("unsafe", False)
            ) for t in tags_data
        ]
