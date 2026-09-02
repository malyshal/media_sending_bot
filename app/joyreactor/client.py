from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
from app.core.config import settings
from .models import JRPost, JRTag
from .queries import SEARCH_TAGS_QUERY, FETCH_POSTS_QUERY, GET_POST_QUERY
from .extractor import JoyReactorExtractor
import httpx
import base64
import structlog
import asyncio
from datetime import timezone

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
        
        retries = 0
        max_retries = 3
        
        while retries <= max_retries:
            
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

    @staticmethod
    def _media_from_attributes(post_id: str, attributes: List[Dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
        """Builds a direct media URL from the first picture attribute.

        Verified CDN patterns (numeric attribute id = <nid>):
          JPEG image:  https://img1.joyreactor.cc/pics/post/-<nid>.jpeg
          GIF/video:   https://img1.joyreactor.cc/pics/post/-<nid>.webm

        Returns (media_url, media_type) with media_type in {"image", "video", "gif"}
        per TS #12/#33.
        """
        for attr in attributes or []:
            if attr.get("__typename") != "PostAttributePicture":
                continue
            numeric_id = JoyReactorClient._decode_global_id_static(attr.get("id", ""))
            if not numeric_id.isdigit():
                logger.error("unknown_media_id_format", post_id=post_id, attr_id=attr.get("id"))
                continue
            image = attr.get("image", {}) or {}
            has_video = bool(image.get("hasVideo"))
            media_type = (image.get("type") or "").upper()
            # Animated content (GIF/webm) served as .webm, static photos as .jpeg
            if has_video:
                return f"https://img1.joyreactor.cc/pics/post/-{numeric_id}.webm", "gif"
            if media_type == "GIF":
                return f"https://img1.joyreactor.cc/pics/post/-{numeric_id}.webm", "gif"
            return f"https://img1.joyreactor.cc/pics/post/-{numeric_id}.jpeg", "image"
        return None, None

    @staticmethod
    def _all_media_urls(post_id: str, attributes: List[Dict[str, Any]]) -> list[tuple[str, str]]:
        """All media items of the post in order (TS #83: text + multiple media)."""
        result = []
        for attr in attributes or []:
            if attr.get("__typename") != "PostAttributePicture":
                continue
            numeric_id = JoyReactorClient._decode_global_id_static(attr.get("id", ""))
            if not numeric_id.isdigit():
                continue
            image = attr.get("image", {}) or {}
            if image.get("hasVideo") or (image.get("type") or "").upper() == "GIF":
                result.append((f"https://img1.joyreactor.cc/pics/post/-{numeric_id}.webm", "gif"))
            else:
                result.append((f"https://img1.joyreactor.cc/pics/post/-{numeric_id}.jpeg", "image"))
        return result

    @staticmethod
    def _decode_global_id_static(global_id: str) -> str:
        try:
            decoded = base64.b64decode(global_id).decode("utf-8")
            return decoded.split(":")[-1] if ":" in decoded else decoded
        except Exception:
            return global_id

    # alias for internal use
    _decode_global_id_static = _decode_global_id_static

    async def fetch_post(self, post_id: str) -> Optional[JRPost]:
        """
        Fetches a single post and normalizes it.
        """
        data = await self.execute(GET_POST_QUERY, {"id": post_id})
        post_data = data.get("node")
        if not post_data:
            return None

        media_url, media_type = self._media_from_attributes(post_data["id"], post_data.get("attributes", []))
        media_urls = self._all_media_urls(post_data["id"], post_data.get("attributes", []))

        try:
            created_at = datetime.fromisoformat(post_data["createdAt"])
        except (KeyError, ValueError):
            created_at = datetime.utcnow()
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)

        return JRPost(
            id=post_data["id"],
            text=post_data.get("text"),
            content=[],
            tags=[pt["tag"]["name"] for pt in post_data.get("postTags", [])],
            created_at=created_at,
            media_url=media_url,
            media_type=media_type,
            media_urls=media_urls,
            raw_data=post_data
        )

    async def fetch_posts_by_tag(self, tag_name: str, page: int = 1) -> List[JRPost]:
        variables = {"tagName": tag_name, "page": page}
        data = await self.execute(FETCH_POSTS_QUERY, variables)
        
        posts_data = data.get("tag", {}).get("postPager", {}).get("posts", [])
        results = []
        for p in posts_data:
            # Posts from the list already contain everything needed; no per-post API call
            media_url, media_type = self._media_from_attributes(p["id"], p.get("attributes", []))
            if not media_url:
                logger.warning("post_without_media_skipped", post_id=p["id"])
                continue
            try:
                created_at = datetime.fromisoformat(p["createdAt"])
            except (KeyError, ValueError):
                created_at = datetime.utcnow()
            # DB columns are TIMESTAMP WITHOUT TIME ZONE (UTC); API returns tz-aware ISO strings
            if created_at.tzinfo is not None:
                created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
            results.append(JRPost(
                id=p["id"],
                text=p.get("text"),
                content=[],
                tags=[pt["tag"]["name"] for pt in p.get("postTags", [])],
                created_at=created_at,
                media_url=media_url,
                media_type=media_type,
                media_urls=self._all_media_urls(p["id"], p.get("attributes", [])),
                raw_data=p
            ))
        from app.core.metrics import metrics
        metrics.inc("posts_received", len(results))
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
