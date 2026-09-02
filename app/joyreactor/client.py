from typing import Any, Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
from app.core.config import settings
from .models import JRPost, JRTag
from .queries import (SEARCH_TAGS_QUERY, FETCH_POSTS_QUERY, GET_POST_QUERY, SEARCH_POSTS_QUERY,
                      SEARCH_SIMILAR_QUERY, TAG_INFO_QUERY)
from .extractor import JoyReactorExtractor
import httpx
import json
import base64
import structlog
import asyncio
from datetime import timezone

logger = structlog.get_logger()

class JoyReactorClient:
    BASE_SEARCH = "https://joyreactor.cc/search"
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
                # TLS handshake to the API host is flaky on some networks
                # (Docker VM / LXC); the per-IP fallback transport is reliable.
                import json as _json
                from app.net.https import https_request_sync
                body = _json.dumps(payload).encode("utf-8")
                status, resp_headers, content = await asyncio.to_thread(
                    https_request_sync, self.api_url, "POST", body,
                    {"Content-Type": "application/json", "Accept": "application/json"},
                    15.0, False,
                )

                if status == 429:
                    logger.warning("rate_limit_exceeded", status_code=429, retry=retries)
                    await asyncio.sleep(15)
                    retries += 1
                    continue

                if status == 403:
                    logger.error("joyreactor_api_forbidden", status_code=403, url=self.api_url)
                    raise Exception("JoyReactor API returned 403 Forbidden")

                if status >= 400:
                    raise Exception(f"HTTP {status}")

                data = json.loads(content.decode("utf-8"))

                if "errors" in data:
                    logger.error("graphql_errors", errors=data["errors"], query=query[:100])
                    raise Exception(f"GraphQL errors: {data['errors']}")

                return data.get("data", {})

            except Exception as e:
                logger.error("request_error", error=str(e) or repr(e), retry=retries)
                retries += 1
                if retries > max_retries:
                    raise
                await asyncio.sleep(2 ** retries)

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

    async def _parse_list_post(self, p: dict) -> Optional[JRPost]:
        """Normalize a post entry from a list response."""
        media_url, media_type = self._media_from_attributes(p["id"], p.get("attributes", []))
        if not media_url:
            logger.warning("post_without_media_skipped", post_id=p["id"])
            return None
        try:
            created_at = datetime.fromisoformat(p["createdAt"])
        except (KeyError, ValueError):
            created_at = datetime.utcnow()
        # DB columns are TIMESTAMP WITHOUT TIME ZONE (UTC); API returns tz-aware ISO strings
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
        return JRPost(
            id=p["id"],
            text=p.get("text"),
            content=[],
            tags=[pt["tag"]["name"] for pt in p.get("postTags", [])],
            created_at=created_at,
            media_url=media_url,
            media_type=media_type,
            media_urls=self._all_media_urls(p["id"], p.get("attributes", [])),
            raw_data=p
        )

    async def fetch_posts_by_tag(self, tag_name: str, page: int = 1) -> List[JRPost]:
        """Fetch fresh posts for a tag.

        The NEW pager is ASCENDING (page 1 = 2009-era posts), so we resolve the
        LAST page from `count` and walk backwards from it: `page` is interpreted
        as an offset from the end (1 = newest page, 2 = one page older, ...).
        If the string is not a real tag (tag: null), fall back to full-text search.
        """
        variables = {"tagName": tag_name, "page": page}
        data = await self.execute(FETCH_POSTS_QUERY, variables)
        tag_data = data.get("tag")

        if not tag_data or not tag_data.get("postPager"):
            # Not a real tag -> try full-text search (TS #30/#56: arbitrary strings)
            logger.info("tag_not_found_using_search", tag=tag_name)
            return await self.search_posts(tag_name, page=page)

        pager = tag_data["postPager"]
        total = pager.get("count") or 0
        per_page = 10
        last_page = max(1, (int(total) + per_page - 1) // per_page)

        # page=1 -> last page; page=2 -> last-1, etc.
        effective_page = max(1, last_page - (page - 1))

        posts_data = pager.get("posts", [])
        if effective_page != page:
            variables["page"] = effective_page
            data = await self.execute(FETCH_POSTS_QUERY, variables)
            posts_data = data.get("tag", {}).get("postPager", {}).get("posts", [])

        results = []
        for p in posts_data:
            post = await self._parse_list_post(p)
            if post:
                results.append(post)
        from app.core.metrics import metrics
        metrics.inc("posts_received", len(results))
        return results

    async def search_posts(self, query: str, page: int = 1) -> List[JRPost]:
        """Full-text search, newest first (sortByDate). Works for arbitrary
        strings, including strings that are not real tags."""
        data = await self.execute(SEARCH_POSTS_QUERY, {"query": query, "page": page})
        srch = (data.get("search") or {}).get("postPager") or {}
        results = []
        for p in srch.get("posts", []):
            post = await self._parse_list_post(p)
            if post:
                results.append(post)
        from app.core.metrics import metrics
        metrics.inc("posts_received", len(results))
        return results

    async def search_tags(self, mask: str) -> List[JRTag]:
        """Tag suggestions matching the website search:
        1. API prefix autocomplete (tagAutocomplete);
        2. site /search/<query> page -> exact tag suggestions with counts
           (e.g. "тюлень" -> тюлень, тюлени, тюлень любви);
        3. search.similarQueries as fallback.
        Deduplicated, site results first (they match what the user sees)."""
        result: List[JRTag] = []
        seen: set = set()

        # 1. Site search page = the exact suggestions the user sees on the site
        try:
            site_tags = await asyncio.to_thread(self._site_search_tags, mask)
            for name, count in site_tags:
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    result.append(JRTag(id=name, name=name, count=count or 0,
                                        nsfw=False, unsafe=False))
        except Exception as e:
            logger.warning("site_search_tags_failed", error=str(e))

        # 2. API prefix autocomplete
        try:
            variables = {"mask": mask}
            data = await self.execute(SEARCH_TAGS_QUERY, variables)
            for t in data.get("tagAutocomplete", []) or []:
                if t["name"].lower() not in seen:
                    seen.add(t["name"].lower())
                    result.append(JRTag(id=t["id"], name=t["name"], count=t.get("count", 0),
                                        nsfw=t.get("nsfw", False), unsafe=t.get("unsafe", False)))
        except Exception as e:
            logger.warning("autocomplete_failed", error=str(e))

        # 3. similarQueries fallback (works even under rate limiting)
        if not result:
            try:
                sim_data = await self.execute(SEARCH_SIMILAR_QUERY, {"query": mask})
                for q in (sim_data.get("search") or {}).get("similarQueries", []) or []:
                    if q and q.lower() not in seen:
                        seen.add(q.lower())
                        result.append(JRTag(id=q, name=q, count=0, nsfw=False, unsafe=False))
            except Exception as e:
                logger.warning("similar_queries_failed", error=str(e))

        return result[:10]

    async def get_tag_info(self, name: str) -> Optional[dict]:
        """Returns {id, name, count} if the API knows this tag verbatim, else None."""
        try:
            data = await self.execute(TAG_INFO_QUERY, {"name": name})
            tag = data.get("tag")
            if tag and tag.get("name"):
                return {"id": tag["id"], "name": tag["name"], "count": tag.get("count", 0)}
        except Exception as e:
            logger.warning("tag_info_error", name=name, error=str(e))
        return None

    @staticmethod
    def _site_search_tags(query: str) -> list:
        """Parse the public site search page (__NEXT_DATA__) for exact tag suggestions."""
        import re as _re
        import urllib.request as _req
        from urllib.parse import quote as _quote
        import json as _json

        url = f"{JoyReactorClient.BASE_SEARCH}/{_quote(query)}"
        request = _req.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        html = _req.urlopen(request, timeout=20).read().decode("utf-8", "replace")
        m = _re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, _re.S)
        if not m:
            return []
        data = _json.loads(m.group(1))
        store = (((data.get("props") or {}).get("pageProps")) or {}).get("relayStore") or {}
        out = []
        for k, v in store.items():
            if k.startswith("client:root:search(") and isinstance(v, dict) and "tags" in v:
                for ref in v["tags"].get("__refs", []):
                    t = store.get(ref, {})
                    if t.get("name"):
                        out.append((t["name"], t.get("count") or 0))
        return out
