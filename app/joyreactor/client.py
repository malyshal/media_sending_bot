from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
from app.core.config import settings
from .models import JRPost, JRTag
import httpx
import structlog

logger = structlog.get_logger()

class JoyReactorClient:
    def __init__(self):
        self.url = f"{settings.joyreactor_base_url}/graphql"
        self.client = httpx.AsyncClient(timeout=10.0)

    async def execute(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        
        try:
            response = await self.client.post(self.url, json=payload)
            response.raise_for_status()
            data = response.json()
            
            if "errors" in data:
                logger.error("graphql_errors", errors=data["errors"])
                raise Exception(f"GraphQL errors: {data['errors']}")
                
            return data.get("data", {})
        except httpx.HTTPStatusError as e:
            logger.error("http_error", status_code=e.response.status_code)
            raise e
        except Exception as e:
            logger.error("request_failed", error=str(e))
            raise e

    async def close(self):
        await self.client.aclose()

    async def fetch_posts_by_tag(self, tag_name: str, page: int = 1) -> List[JRPost]:
        query = """
        query GetPostsByTag($tagName: String!, $page: Int) {
          tag(name: $tagName) {
            postPager(type: NEW) {
              posts(page: $page) {
                id
                text
                createdAt
                attributes {
                  image {
                    type
                  }
                  ... on PostAttributePicture {
                    image {
                      id
                    }
                  }
                }
                postTags {
                  tag {
                    name
                  }
                }
              }
            }
          }
        }
        """
        # Note: Actual media URL construction might require additional logic 
        # since GraphQL often returns image IDs or partial paths.
        variables = {"tagName": tag_name, "page": page}
        data = await self.execute(query, variables)
        
        posts_data = data.get("tag", {}).get("postPager", {}).get("posts", [])
        results = []
        for p in posts_data:
            # Simple mapping, real implementation needs to handle attribute types correctly
            attr = p.get("attributes", [{}])[0]
            img = attr.get("image", {})
            
            results.append(JRPost(
                id=int(p["id"]),
                text=p.get("text"),
                media_url=f"{settings.joyreactor_base_url}/img/{img.get('id')}", 
                media_type=img.get("type", "UNKNOWN"),
                tags=[pt["tag"]["name"] for pt in p.get("postTags", [])],
                created_at=datetime.fromisoformat(p["createdAt"]),
                raw_data=p
            ))
        return results

    async def search_tags(self, mask: str) -> List[JRTag]:
        query = """
        query SearchTags($mask: String!) {
          tagAutocomplete(mask: $mask) {
            id
            name
            count
            nsfw
            unsafe
          }
        }
        """
        variables = {"mask": mask}
        data = await self.execute(query, variables)
        tags_data = data.get("tagAutocomplete", [])
        
        return [
            JRTag(
                id=t["id"],
                name=t["name"],
                count=t["count"],
                nsfw=t["nsfw"],
                unsafe=t["unsafe"]
            ) for t in tags_data
        ]
