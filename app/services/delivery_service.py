from typing import Optional, Any
import structlog
from aiogram import Bot, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from app.services.post_service import PostService
from app.services.media_manager import MediaManager
from app.core.metrics import metrics
from app.core.config import settings
from app.bot.post_tag_keyboard import build_post_tags_keyboard
from app.db.models.post import Post
from pathlib import Path
from app.db.session import async_session
from sqlalchemy import delete, and_
import asyncio
import secrets
import time as _time

logger = structlog.get_logger()

MAX_SKIP_DEPTH = 5
TELEGRAM_CAPTION_LIMIT = 1024


def _post_link(post: Post) -> str:
    """Source post URL on joyreactor.cc (for debugging/moderation)."""
    try:
        import base64 as _b64
        numeric = post.id
        if post.id.startswith("UG9zdDo"):
            decoded = _b64.b64decode(post.id).decode("utf-8", "replace")
            numeric = decoded.split(":", 1)[-1]
        return f"https://joyreactor.cc/post/{numeric}"
    except Exception:
        return ""


class _DeliveryRetryable(Exception):
    """Internal: the candidate post is broken (dead CDN link etc.); try the next one."""


import re as _re
from bs4 import BeautifulSoup

_ATTR_PLACEHOLDER = _re.compile(r"&attribute_insert_\d+&")


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities: JoyReactor post text is HTML
    (e.g. "<p>Drama Queen</p>"), Telegram captions must be plain text."""
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    plain = soup.get_text(separator="\n", strip=True)
    return plain


def _make_caption(text: Optional[str], link: str = "", limit: int = TELEGRAM_CAPTION_LIMIT) -> str:
    """TS #34: post text as caption (+optional source link), truncated to the Telegram limit.
    The source link is always kept visible: text is truncated first.
    Media placeholders (&attribute_insert_N&) are removed — they mark where
    media is inserted on the site and are not human-readable text.
    HTML markup (<p>...</p> etc.) is stripped to plain text.
    `limit` lets callers reuse this for bot.send_message (4096) when the post
    has no media to attach the caption to."""
    text = _ATTR_PLACEHOLDER.sub(" ", text or "")
    text = BeautifulSoup(text, "html.parser").get_text(separator="\n", strip=True)
    if not text:
        return f"🔗 {link}" if link else ""
    link_block = f"\n\n🔗 {link}" if link else ""
    budget = limit - len(link_block)
    if len(text) > budget:
        text = text[: budget - 1] + "…"
    return text + link_block


TELEGRAM_MESSAGE_LIMIT = 4096


class DeliveryService:
    def __init__(self, bot: Bot, post_service: Optional[PostService], media_manager: MediaManager):
        # post_service may be None on paths that only send already-prepared
        # media (e.g. expanding a collapsed post from a stash).
        self.bot = bot
        self.post_service = post_service
        self.media_manager = media_manager

    async def send_batch_posts(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], max_posts: int, ignore_history: bool = False, show_links: bool = False) -> int:
        """
        Sends a batch of posts to a chat. Returns number of posts successfully sent.
        Broken posts (dead CDN links, oversized media) are skipped, bounded by
        MAX_SKIP_DEPTH to obey TS #74 (no infinite recursive search).
        """
        sent_count = 0
        attempts = 0
        max_attempts = max_posts + MAX_SKIP_DEPTH
        while sent_count < max_posts and attempts < max_attempts:
            attempts += 1
            try:
                message = await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history, show_links=show_links)
            except _DeliveryRetryable:
                continue
            if message:
                sent_count += 1
            else:
                break
        return sent_count

    async def send_next_post(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], ignore_history: bool = False, _depth: int = 0, show_links: bool = False, post: Optional[Post] = None) -> Optional[types.Message]:
        # 1. Get candidate post (or use the explicitly requested one)
        post = post if post is not None else await self.post_service.get_next_post_for_chat(chat_id, include_tags, exclude_tags, ignore_history=ignore_history)
        if not post:
            logger.info("no_suitable_post_found", chat_id=chat_id)
            return None

        # Media URL is already a direct CDN link (built from GraphQL attributes).
        media_url = post.media_url

        # 2. RACE CONDITION FIX: Try to lock the post in DB before processing
        # Only the process that successfully inserts into history can send the post.
        if not ignore_history:
            if not await self.post_service.repo.try_lock_post_for_chat(chat_id, post.id):
                logger.info("post_already_locked_by_another_process", chat_id=chat_id, post_id=post.id)
                return await self._retry_bounded(chat_id, include_tags, exclude_tags, ignore_history, _depth, show_links)

        processed_paths: list[Path] = []
        try:
            # 3. Prepare media (TS #83: a post may contain several media items)
            media_items = self._post_media_items(post)

            # 4. Try interleaved text+media delivery when the post text actually
            # contains &attribute_insert_N& markers — produces the same "text
            # wraps the picture" layout the site has, and naturally handles
            # posts with more than 10 media items (we send multiple media
            # groups with text messages between them).
            blocks = self._post_content_blocks(post.text)
            has_inserts = any(b[0] == "media" for b in blocks)
            if has_inserts and len(media_items) >= 1:
                tag_kb = build_post_tags_keyboard(chat_id, post, include_tags, exclude_tags)
                message = await self._send_interleaved(
                    chat_id, post, media_items, blocks,
                    show_links=show_links,
                    tag_kb=tag_kb,
                )
                metrics.inc("posts_sent")
                return message

            caption = _make_caption(post.text, _post_link(post) if show_links else "")

            if len(media_items) > 1:
                processed = []
                try:
                    for url, mtype in media_items:
                        try:
                            path, mime = await self._prepare_media(url, mtype)
                        except Exception as e:
                            if "HTTP 404" in str(e):
                                resolved = await self.post_service.client.resolve_media_via_post_page(post.id)
                                if not resolved:
                                    raise
                                url, mtype = resolved[0]
                                path, mime = await self._prepare_media(url, mtype)
                            else:
                                raise
                        processed.append((path, mime))
                    message = await self.send_media_group_with_tags(chat_id, processed, post, include_tags, exclude_tags, caption)
                    metrics.inc("posts_sent")
                    return message
                finally:
                    for p, _ in processed:
                        if p:
                            await self.media_manager.cleanup_file(p)

            # Single media
            try:
                processed_path, mime_type = await self._prepare_media(media_items[0][0], media_items[0][1])
            except Exception as e:
                # New posts use slugged CDN paths not derivable from the API:
                # resolve real media URLs from the public post page and retry.
                if "HTTP 404" in str(e):
                    resolved = await self.post_service.client.resolve_media_via_post_page(post.id)
                    if resolved:
                        logger.info("media_resolved_via_post_page", post_id=post.id, count=len(resolved))
                        url2, mtype2 = resolved[0]
                        processed_path, mime_type = await self._prepare_media(url2, mtype2)
                    else:
                        raise
                else:
                    raise
            processed_paths.append(processed_path)

            tag_kb = build_post_tags_keyboard(chat_id, post, include_tags, exclude_tags)

            if mime_type == "video/mp4":
                message = await self.bot.send_video(
                    chat_id=chat_id,
                    video=types.FSInputFile(processed_path),
                    caption=caption,
                    reply_markup=tag_kb,
                )
            elif mime_type.startswith("image/"):
                message = await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=types.FSInputFile(processed_path),
                    caption=caption,
                    reply_markup=tag_kb,
                )
            else:
                # TS #33: send_document fallback for unusual media
                message = await self.bot.send_document(
                    chat_id=chat_id,
                    document=types.FSInputFile(processed_path),
                    caption=caption,
                    reply_markup=tag_kb,
                )
            metrics.inc("posts_sent")
            return message

        except TelegramBadRequest as e:
            if "IMAGE_PROCESS_FAILED" in str(e):
                # Telegram can't process the image: fall back to plain text so
                # the user still gets the post content.
                logger.info("image_process_failed_text_fallback", chat_id=chat_id, post_id=post.id)
                link = _post_link(post)
                text = _make_caption(post.text, link if show_links else "")
                if not text:
                    text = f"🔗 {link}"
                message = await self.bot.send_message(chat_id=chat_id, text=text)
                metrics.inc("posts_sent")
                return message
            metrics.inc("delivery_failures")
            logger.error(
                "delivery_failed", chat_id=chat_id, post_id=post.id,
                error=str(e) or repr(e), exc_type=type(e).__name__,
            )
            # Unlock so the post can be retried later if the failure was transient
            if not ignore_history:
                await self._unlock_post(chat_id, post.id)
            # Broken candidate (dead CDN link, oversized media, Telegram refusal):
            # signal the caller to try the next post instead of aborting.
            raise _DeliveryRetryable() from e

        except Exception as e:
            metrics.inc("delivery_failures")
            logger.error(
                "delivery_failed", chat_id=chat_id, post_id=post.id,
                error=str(e) or repr(e), exc_type=type(e).__name__,
            )
            # Unlock so the post can be retried later if the failure was transient
            if not ignore_history:
                await self._unlock_post(chat_id, post.id)
            # Broken candidate (dead CDN link, oversized media, Telegram refusal):
            # signal the caller to try the next post instead of aborting.
            raise _DeliveryRetryable() from e
        finally:
            for p in processed_paths:
                if p:
                    await self.media_manager.cleanup_file(p)

    def _post_media_items(self, post: Post) -> list[tuple[str, str]]:
        """Media list for delivery: all media if available, else the single primary item.

        Resolution order:
          1. raw_data.attributes via _all_media_urls (old GraphQL numeric CDN)
          2. raw_data._resolved_media_urls (slugged CDN scraped by
             resolve_media_via_post_page for newer posts)
          3. media_url alone (single-image posts)

        Note: NO 10-item cap here. Telegram limits a single send_media_group to
        10 items, but DeliveryService._send_interleaved() chunks the media
        into multiple groups and interleaves text between them, so callers
        receive the full ordered list.
        """
        items: list[tuple[str, str]] = []
        raw = post.raw_data if isinstance(post.raw_data, dict) else None
        if raw:
            items = self.post_service.client._all_media_urls(post.id, raw.get("attributes", []))
            if not items:
                resolved = raw.get("_resolved_media_urls") or []
                items = [(u, t or "image") for u, t in resolved]
        if not items and post.media_url:
            items = [(post.media_url, post.media_type or "image")]
        return items

    @staticmethod
    def _post_content_blocks(text: Optional[str]) -> list[tuple[str, Any]]:
        """Split post HTML into an ordered list of (kind, payload) blocks.

        JoyReactor post text contains &attribute_insert_N& markers where media
        should be inserted in the rendered post on the site. We use them to
        reconstruct the site's "text wraps the picture" layout on Telegram:

          kind="text",   payload="<p>...</p>"  -> Telegram text message / caption
          kind="media",  payload=1..N          -> 1-based media index in the post

        HTML tags inside text blocks are stripped at send time (Telegram
        captions are plain text); newlines are preserved.
        """
        if not text:
            return []
        parts = _ATTR_PLACEHOLDER.split(text)
        indices = [int(n) for n in _re.findall(r"&attribute_insert_(\d+)&", text)]
        blocks: list[tuple[str, Any]] = []
        for i, chunk in enumerate(parts):
            if chunk.strip():
                blocks.append(("text", chunk))
            if i < len(indices):
                blocks.append(("media", indices[i]))
        if not blocks:
            return [("text", text)]
        return blocks

    async def _send_interleaved(
        self,
        chat_id: int,
        post: Post,
        media_items: list[tuple[str, str]],
        blocks: list[tuple[str, Any]],
        show_links: bool = False,
        tag_kb=None,
    ) -> types.Message:
        """Deliver a post with the text-and-pictures layout of the source site.

        Walks `blocks` and:
          * sends pure-text blocks as plain send_message calls;
          * groups consecutive media blocks into batches of <=10 (Telegram's
            send_media_group limit) and sends each batch as a media group;
          * if text immediately precedes a media batch, attaches it as the
            caption of the first item in that group;
          * when >10 media items are present, multiple media groups are sent
            with their respective in-between text segments between them;
          * the tag keyboard is attached only to the LAST message so it does
            not pile up on the chat.

        Long posts (more than COLLAPSE_THRESHOLD runs) are sent collapsed:
        only the first run is sent, with the tag keyboard plus a
        "Показать весь пост (N)" button. Pressing it deletes the placeholder
        and sends the remaining runs.
        """
        runs = self._build_interleaved_runs(post, blocks)

        # collapse_post_threshold <= 0 disables the collapsed-preview mode.
        threshold = int(getattr(settings, "collapse_post_threshold", 3) or 0)
        if threshold > 0 and len(runs) > threshold:
            return await self._send_collapsed(chat_id, post, media_items, runs, show_links, tag_kb)

        return await self._send_runs(chat_id, post, media_items, runs, show_links=show_links, tag_kb=tag_kb)

    @staticmethod
    def _build_interleaved_runs(post: Post, blocks: list[tuple[str, Any]]) -> list[dict]:
        """Plan the message stream for an interleaved post without sending anything.

        Returns a list of run dicts:
          {"kind": "text",   "text": str}
          {"kind": "media",  "items": [(1-based idx, url, type), ...], "caption": Optional[str]}

        The caption uses the text chunk that immediately precedes the media
        run, except for "intro" text (which becomes a leading standalone run
        — Telegram shows captions BELOW images, but the site shows intro text
        ABOVE the first image).
        """
        runs: list[dict] = []
        cur_text_chunks: list[str] = []
        cur_media: list[tuple[int, Optional[str], Optional[str]]] = []  # (idx, url, type)

        def text_to_caption() -> Optional[str]:
            if not cur_text_chunks:
                return None
            return "\n\n".join(cur_text_chunks)

        def flush_text_run():
            if cur_media or not cur_text_chunks:
                return
            txt = "\n\n".join(cur_text_chunks)
            cur_text_chunks.clear()
            runs.append({"kind": "text", "text": txt})

        def flush_media_run():
            if not cur_media:
                return
            cap = text_to_caption()
            cur_text_chunks.clear()
            for start in range(0, len(cur_media), 10):
                chunk = cur_media[start:start + 10]
                runs.append({
                    "kind": "media",
                    "items": list(chunk),
                    "caption": cap if start == 0 else None,
                })
            cur_media.clear()

        for kind, payload in blocks:
            if kind == "text":
                chunk = _clean_html(payload)
                if not chunk:
                    continue
                if cur_media:
                    flush_media_run()
                cur_text_chunks.append(chunk)
            else:
                idx_1based = int(payload)
                cur_media.append((idx_1based, None, None))
                if len(cur_media) > 10:
                    flush_media_run()

        if cur_media:
            flush_media_run()
        else:
            flush_text_run()

        # Intro-text rule: if the first run is media with a caption, pull the
        # caption out as a leading standalone text run.
        if runs and runs[0]["kind"] == "media" and runs[0]["caption"]:
            intro_caption = runs[0]["caption"]
            runs[0]["caption"] = None
            runs.insert(0, {"kind": "text", "text": intro_caption})

        return runs

    async def _send_runs(
        self,
        chat_id: int,
        post: Post,
        media_items: list[tuple[str, str]],
        runs: list[dict],
        show_links: bool = False,
        tag_kb=None,
        keyboard_on_last: bool = True,
    ) -> Optional[types.Message]:
        """Send every run in order, attaching tag_kb (and source link) to the last."""
        source_link = _post_link(post) if show_links else ""
        last_message: Optional[types.Message] = None

        # Pre-download all media (lets the API queue batch downloads).
        prepared: list[Optional[tuple[Path, str]]] = [None] * len(media_items)
        try:
            for idx in range(len(media_items)):
                url, mtype = media_items[idx]
                try:
                    path, mime = await self._prepare_media(url, mtype)
                except Exception as e:
                    if "HTTP 404" in str(e) and self.post_service is not None:
                        resolved = await self.post_service.client.resolve_media_via_post_page(post.id)
                        if resolved and idx < len(resolved):
                            url2, mtype2 = resolved[idx]
                            path, mime = await self._prepare_media(url2, mtype2)
                        else:
                            raise
                    else:
                        raise
                prepared[idx] = (path, mime)

            for i, run in enumerate(runs):
                is_last = i == len(runs) - 1
                with_kb = bool(keyboard_on_last and is_last and tag_kb)
                if run["kind"] == "text":
                    msg = await self._send_text_run(
                        chat_id, run["text"], source_link=source_link, with_kb=with_kb, tag_kb=tag_kb,
                    )
                else:
                    # Resolve 1-based indices to actual prepared media.
                    chunk: list[tuple[int, Path, str]] = []
                    for idx_1based, _, _ in run["items"]:
                        if 1 <= idx_1based <= len(prepared) and prepared[idx_1based - 1]:
                            chunk.append((idx_1based, prepared[idx_1based - 1][0], prepared[idx_1based - 1][1]))
                    if not chunk:
                        continue
                    msg = await self._send_media_run(
                        chat_id, chunk, caption=run["caption"], with_kb=with_kb, tag_kb=tag_kb,
                    )
                if msg:
                    last_message = msg

            if last_message is None and source_link:
                last_message = await self.bot.send_message(
                    chat_id=chat_id, text=f"🔗 {source_link}",
                    disable_web_page_preview=True,
                )
            return last_message
        finally:
            for item in prepared:
                if item:
                    path, _ = item
                    await self.media_manager.cleanup_file(path)

    async def _send_text_run(
        self,
        chat_id: int,
        text: str,
        source_link: str = "",
        with_kb: bool = False,
        tag_kb=None,
    ) -> Optional[types.Message]:
        if not text.strip():
            return None
        text_to_send = text.strip()
        if with_kb and source_link and not text_to_send.rstrip().endswith(source_link):
            text_to_send = text_to_send.rstrip() + f"\n\n🔗 {source_link}"
        elif not text_to_send and source_link:
            text_to_send = f"🔗 {source_link}"
        return await self.bot.send_message(
            chat_id=chat_id,
            text=text_to_send,
            disable_web_page_preview=True,
            reply_markup=(tag_kb if with_kb else None),
        )

    async def _send_media_run(
        self,
        chat_id: int,
        chunk: list[tuple[int, Path, str]],
        caption: Optional[str] = None,
        with_kb: bool = False,
        tag_kb=None,
    ) -> Optional[types.Message]:
        if not chunk:
            return None
        cap = caption if caption else None
        if cap and len(cap) > TELEGRAM_CAPTION_LIMIT:
            cap = cap[: TELEGRAM_CAPTION_LIMIT - 1] + "…"
        # NOTE: aiogram's send_media_group does NOT accept reply_markup.
        # We send the album first, then edit the last message's markup if
        # requested.
        messages = await self._send_media_group_raw(chat_id, chunk, caption=cap)
        last_msg = None
        if isinstance(messages, list) and messages:
            last_msg = messages[-1]
        elif messages is not None:
            last_msg = messages
        if with_kb and tag_kb and last_msg is not None:
            try:
                await last_msg.edit_reply_markup(reply_markup=tag_kb)
            except Exception as e:
                # Some messages (e.g. documents in older groups) cannot be
                # edited for markup. Fall back to a separate small message.
                logger.warning(
                    "media_group_edit_kb_failed",
                    chat_id=chat_id, message_id=getattr(last_msg, "message_id", None),
                    error=str(e),
                )
                try:
                    await self.bot.send_message(
                        chat_id=chat_id, text="🏷 Теги поста:", reply_markup=tag_kb,
                    )
                except Exception:
                    pass
        return last_msg

    # ----------------------------------------------------- collapsed delivery

    # In-process stash: maps a short token -> (expires_at, payload_dict).
    # Production deployments with multiple workers should swap this for Redis
    # (`collapsed_post_stash_ttl_seconds` is already configurable).
    _collapsed_stash: dict[str, tuple[float, dict]] = {}
    _collapsed_stash_lock = asyncio.Lock()

    @classmethod
    async def _stash_put(cls, payload: dict) -> str:
        token = secrets.token_urlsafe(8)
        ttl = int(getattr(settings, "collapsed_post_stash_ttl_seconds", 3600) or 3600)
        expires_at = _time.time() + ttl
        async with cls._collapsed_stash_lock:
            cls._collapsed_stash[token] = (expires_at, payload)
        return token

    @classmethod
    async def _stash_pop(cls, token: str) -> Optional[dict]:
        async with cls._collapsed_stash_lock:
            entry = cls._collapsed_stash.pop(token, None)
        if not entry:
            return None
        expires_at, payload = entry
        if expires_at < _time.time():
            return None
        return payload

    @classmethod
    async def _stash_gc(cls) -> int:
        """Drop expired entries; returns the count removed."""
        now = _time.time()
        removed = 0
        async with cls._collapsed_stash_lock:
            expired = [t for t, (exp, _) in cls._collapsed_stash.items() if exp < now]
            for t in expired:
                cls._collapsed_stash.pop(t, None)
                removed += 1
        return removed

    @staticmethod
    def _merge_keyboards(*markups) -> Optional[InlineKeyboardMarkup]:
        """Concatenate InlineKeyboardMarkup inline rows, skipping Nones."""
        from aiogram.types import InlineKeyboardMarkup
        rows: list[list] = []
        for kb in markups:
            if not kb:
                continue
            for row in kb.inline_keyboard:
                rows.append(list(row))
        if not rows:
            return None
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def _collapsed_button(post_num: str, chat_id: int, token: str, total_runs: int) -> InlineKeyboardButton:
        """The "show full post" button. The token is the stash key — small
        random bytes encoded as URL-safe base64 (11 chars), which leaves
        plenty of room under Telegram's 64-byte callback_data limit."""
        from aiogram.types import InlineKeyboardButton
        cb = f"post_full:{chat_id}:{post_num}:{token}"
        return InlineKeyboardButton(
            text=f"📖 Показать весь пост ({total_runs})",
            callback_data=cb,
        )

    async def _send_collapsed(
        self,
        chat_id: int,
        post: Post,
        media_items: list[tuple[str, str]],
        runs: list[dict],
        show_links: bool,
        tag_kb,
    ) -> Optional[types.Message]:
        """Send only the FIRST run of a long post, with the tag keyboard
        plus a "show full" button. Stash the rest in memory; the button
        callback handler will pick them up and send them on demand."""
        from app.bot.post_tag_keyboard import short_post_id

        await self._stash_gc()

        post_num = short_post_id(post.id)
        total_runs = len(runs)

        # Stash everything we need to reconstruct the rest later; the token
        # is the lookup key and goes into the button's callback_data.
        token = await self._stash_put({
            "chat_id": chat_id,
            "post_id": post.id,
            "post_num": post_num,
            "media_items": media_items,
            "remaining_runs": runs[1:],
            "show_links": show_links,
        })

        # Send JUST the first run, with both keyboards merged.
        first_run = runs[0]
        collapse_btn = self._collapsed_button(post_num, chat_id, token, total_runs)
        extra_kb = InlineKeyboardMarkup(inline_keyboard=[[collapse_btn]])
        merged_kb = self._merge_keyboards(tag_kb, extra_kb)

        return await self._send_runs(
            chat_id, post, media_items, [first_run],
            show_links=show_links,
            tag_kb=merged_kb,
            keyboard_on_last=True,
        )

    async def expand_collapsed_post(
        self,
        token_payload: dict,
        post: Post,
        tag_kb,
    ) -> int:
        """Send the runs stashed under `token_payload`. Called from the
        'show full post' callback after the user has deleted the placeholder.
        Returns the number of runs successfully sent."""
        chat_id = token_payload["chat_id"]
        media_items = token_payload["media_items"]
        runs: list[dict] = token_payload["remaining_runs"]
        show_links = token_payload.get("show_links", False)

        if not runs:
            return 0

        await self._send_runs(
            chat_id, post, media_items, runs,
            show_links=show_links,
            tag_kb=tag_kb,
            keyboard_on_last=True,
        )
        return len(runs)

    async def send_collapsed_from_token(
        self,
        token: str,
        post: Post,
        tag_kb,
        chat_id_to_delete: int,
        message_id_to_delete: int,
        bot,
    ) -> int:
        """All-in-one helper for the 'show full post' callback:
        pops the stash by token, deletes the placeholder, and sends the
        remaining runs. Returns the number of runs sent, or 0 if the token
        has expired."""
        payload = await self._stash_pop(token)
        if not payload:
            return 0
        try:
            await bot.delete_message(chat_id=chat_id_to_delete, message_id=message_id_to_delete)
        except Exception:
            pass
        return await self.expand_collapsed_post(payload, post, tag_kb)

    async def _send_media_group_raw(
        self,
        chat_id: int,
        chunk: list[tuple[int, Path, str]],
        caption: Optional[str] = None,
    ):
        """Send a media group. aiogram's send_media_group does NOT accept
        reply_markup — the caller must attach the keyboard afterwards via
        edit_reply_markup on the last message of the returned album."""
        from aiogram.utils.media_group import MediaGroupBuilder

        if not chunk:
            return None
        builder = MediaGroupBuilder(caption=caption)
        for _, path, mime in chunk:
            if mime.startswith("image/"):
                builder.add_photo(media=types.FSInputFile(path))
            else:
                builder.add_video(media=types.FSInputFile(path))
        return await self.bot.send_media_group(
            chat_id=chat_id, media=builder.build(),
        )

    async def send_media_group_with_tags(self, chat_id: int, processed: list[tuple[Path, str]], post, include_tags: list | None = None, exclude_tags: list | None = None, caption: str | None = None) -> types.Message:
        """Send album with tag keyboard on the last message."""
        caption = caption if caption is not None else _make_caption(post.text)
        message = await self._send_media_group(chat_id, processed, caption)
        # Media group returns a list; attach tag keyboard to a follow-up mini message
        # is not possible on an album — send tags as a separate light message.
        tag_kb = build_post_tags_keyboard(chat_id, post, include_tags, exclude_tags)
        if tag_kb and isinstance(message, list) and message:
            await self.bot.send_message(chat_id=chat_id, text="🏷 Теги поста:", reply_markup=tag_kb)
        return message[0] if isinstance(message, list) else message

    async def _prepare_media(self, media_url: str, media_type: str) -> tuple[Path, str]:
        # TS #22: ALL site traffic goes through the single APIQueue — including
        # media downloads from the CDN. Priority 3 (lowest): users may wait.
        # When no post_service is wired (e.g. expand-collapsed-post path), go
        # straight to MediaManager; the disk cache will skip re-downloading
        # anyway.
        if self.post_service is not None and getattr(self.post_service, "queue", None) is not None:
            prepared, mime = await self.post_service.queue.enqueue(
                self.media_manager.process_media, media_url, media_type, priority=3
            )
        else:
            prepared, mime = await self.media_manager.process_media(media_url, media_type)
        return prepared, mime

    async def _send_media_group(self, chat_id: int, processed: list[tuple[Path, str]], caption: str) -> types.Message:
        """TS #83: text + multiple media -> send as an album. Caption goes on the first item."""
        from aiogram.utils.media_group import MediaGroupBuilder

        builder = MediaGroupBuilder(caption=caption)
        for i, (path, mime) in enumerate(processed):
            if mime.startswith("image/"):
                builder.add_photo(media=types.FSInputFile(path))
            else:
                builder.add_video(media=types.FSInputFile(path))
        return await self.bot.send_media_group(chat_id=chat_id, media=builder.build())

    async def _retry_bounded(self, chat_id: int, include_tags: list[str], exclude_tags: list[str], ignore_history: bool, depth: int, show_links: bool = False) -> Optional[types.Message]:
        if depth >= MAX_SKIP_DEPTH:
            return None
        return await self.send_next_post(chat_id, include_tags, exclude_tags, ignore_history=ignore_history, _depth=depth + 1, show_links=show_links)

    async def _unlock_post(self, chat_id: int, post_id: str):
        async with async_session() as session:
            from app.db.models.history import PostHistory
            stmt = delete(PostHistory).where(
                and_(PostHistory.chat_id == chat_id, PostHistory.post_id == post_id)
            )
            await session.execute(stmt)
            await session.commit()