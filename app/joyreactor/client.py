from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
from app.core.config import settings
from .models import JRPost, JRTag
from .queries import SEARCH_TAGS_QUERY, FETCH_POSTS_QUERY, GET_POST_QUERY
from .extractor import JoyReactorExtractor
from .rate_limiter import RateLimiter
import httpx
import base64
import structlog
import asyncio

logger = structlog.get_logger()

class JoyReactorClient:
    def __init__(self):
        self.api_url = settings.joyreactor_api_url
        self.base_url = settings.joyreactor_base_url
        self.extractor = JoyReactorExtractor(self.base_url)
        self.rate_limiter = RateLimiter(min_interval=2.5)
        
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
        
        retries = 0
        max_retries = 3
        
        while retries <= max_retries:
            await self.rate_limiter.wait()
            
            try:
                response = await self.client.post(self.api_url, json=payload)
                
                if response.status_code == 429:
                    logger.warning("rate_limit_exceeded", status_code=429, retry=retries)
                    await asyncio.sleep(15)
                    retries += 1
                    continue
                
                if response.status_code == 403:
                    logger.error("joyreactor_api_forbidden", status_code=403, url=self.api_url)
                    raise httpx.HTTPStatusError("JoyReactor API returned 403 Forbidden", request=response.request, response=response)
                    
                response.raise_for_status()
                data = response.json()
                
                if "errors" in data:
                    logger.error("graphql_errors", errors=data["errors"], query=query[:100])
                    raise Exception(f"GraphQL errors: {data['errors']}")
                    
                return data.get("data", {})
                
            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                logger.error("request_error", error=str(e), retry=retries)
                retries += 1
                if retries > max_retries:
                    raise e
                await asyncio.sleep(2 ** retries)
            except Exception as e:
                logger.error("unexpected_error", error=str(e))
                raise e
                
        raise Exception("Max retries exceeded")

    async def close(self):
        await self.client.aclose()

    def _decode_global_id(self, global_id: str) -> str:
        try:
            decoded = base64.b64decode(global_id).decode('utf-8')
            if ':' in decoded:
                return decoded.split(':')[-1]
            return decoded
        except Exception as e:
            logger.error("decode_global_id_failed", global_id=global_id, error=str(e))
            return global_id

    async def fetch_post(self, post_id: str) -> Optional[JRPost]:
        """
        Fetches a single post and normalizes it.
        """
        data = await self.execute(GET_POST_QUERY, {"id": post_id})
        post_data = data.get("post")
        if not post_data:
            return None
            
        # Note: slug should ideally be retrieved from GraphQL if possible, 
        # or by making a quick HTML request to get the URL.
        # For now, we use a placeholder "post" to satisfy the URL builder.
        slug = "post" 
        
        content = self.extractor.normalize_post(
            post_id=post_data["id"],
            text=post_data.get("text"),
            attributes=post_data.get("attributes", []),
            slug=slug
        )
        
        return JRPost(
            id=post_data["id"],
            text=post_data.get("text"),
            content=content,
            tags=[pt["tag"]["name"] for pt in post_data.get("postTags", [])],
            created_at=datetime.fromisoformat(post_data["createdAt"]),
            raw_data=post_data
        )

    async def fetch_posts_by_tag(self, tag_name: str, page: int = 1) -> List[JRPost]:
        variables = {"tagName": tag_name, "page": page}
        data = await self.execute(FETCH_POSTS_QUERY, variables)
        
        posts_data = data.get("tag", {}).get("postPager", {}).get("posts", [])
        results = []
        for p in posts_data:
            post = await self.fetch_post(p["id"])
            if post:
                results.append(post)
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
