from typing import List, Dict, Any, Optional
from .client import joyreactor_client
from .queries import FETCH_POSTS_QUERY
import structlog

logger = structlog.get_logger()

class JoyReactorService:
    async def get_latest_posts(self, tag: Optional[str] = None, first: int = 20, after: Optional[str] = None) -> List[Dict[str, Any]]:
        variables = {"first": first, "after": after}
        if tag:
            variables["tag"] = tag
            
        data = await joyreactor_client.execute_query(FETCH_POSTS_QUERY, variables)
        posts_data = data.get("posts", {})
        edges = posts_data.get("edges", [])
        
        posts = [edge["node"] for edge in edges]
        return posts

    async def get_posts_pagination(self, tag: Optional[str] = None, first: int = 20, after: Optional[str] = None):
        variables = {"first": first, "after": after}
        if tag:
            variables["tag"] = tag
            
        data = await joyreactor_client.execute_query(FETCH_POSTS_QUERY, variables)
        posts_data = data.get("posts", {})
        edges = posts_data.get("edges", [])
        
        posts = [edge["node"] for edge in edges]
        page_info = posts_data.get("pageInfo", {})
        
        return posts, page_info

joyreactor_service = JoyReactorService()
