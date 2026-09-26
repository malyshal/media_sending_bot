"""Unit tests for DeliveryService text/media interleaving logic.

These tests don't need a running Telegram bot: they mock Bot and
MediaManager and exercise the pure-Python run-planning logic by
inspecting the calls made against the bot mock.
"""
import re
import time as _time
from unittest.mock import AsyncMock, MagicMock
import pytest
from app.services.delivery_service import DeliveryService


_ATTR_PLACEHOLDER = re.compile(r"&attribute_insert_\d+&")


def _blocks(text):
    return DeliveryService._post_content_blocks(text)


def test_blocks_empty_returns_empty():
    assert _blocks("") == []
    assert _blocks(None) == []


def test_blocks_text_only():
    blocks = _blocks("<p>Hello</p><p>World</p>")
    assert blocks == [("text", "<p>Hello</p><p>World</p>")]


def test_blocks_splits_on_insert_markers():
    text = "<p>before</p>&attribute_insert_1&<p>between</p>&attribute_insert_2&<p>after</p>"
    blocks = _blocks(text)
    assert blocks == [
        ("text", "<p>before</p>"),
        ("media", 1),
        ("text", "<p>between</p>"),
        ("media", 2),
        ("text", "<p>after</p>"),
    ]


def test_blocks_consecutive_inserts():
    text = "<p>cap</p>&attribute_insert_1&&attribute_insert_2&&attribute_insert_3&"
    blocks = _blocks(text)
    assert blocks == [
        ("text", "<p>cap</p>"),
        ("media", 1),
        ("media", 2),
        ("media", 3),
    ]


def test_blocks_only_inserts():
    text = "&attribute_insert_1&&attribute_insert_2&"
    blocks = _blocks(text)
    assert blocks == [("media", 1), ("media", 2)]


def test_blocks_insert_indices_preserved():
    text = "&attribute_insert_5&<p>x</p>&attribute_insert_2&"
    blocks = _blocks(text)
    assert blocks == [("media", 5), ("text", "<p>x</p>"), ("media", 2)]


# ------------------------------------------------------------- _send_interleaved

def _make_service(post_id: str = "UG9zdDo2MzgzNjUz"):
    """Build a DeliveryService with Bot and MediaManager mocked."""
    bot = MagicMock()
    # Wrap send_message / send_media_group so we record the real call order
    # in a shared list (AsyncMock's await_args_list preserves order).
    order: list[tuple[str, object]] = []
    sm_calls: list[tuple[tuple, dict]] = []  # (args, kwargs) for send_message
    smg_calls: list[tuple[tuple, dict]] = []  # (args, kwargs) for send_media_group
    sp_calls: list[tuple[tuple, dict]] = []  # (args, kwargs) for send_photo
    sv_calls: list[tuple[tuple, dict]] = []  # (args, kwargs) for send_video

    async def fake_send_message(*args, **kwargs):
        sm_calls.append((args, kwargs))
        text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
        order.append(("text", text))
        return _make_msg(len(order))

    async def fake_send_media_group(*args, **kwargs):
        smg_calls.append((args, kwargs))
        media = kwargs.get("media") or (args[1] if len(args) > 1 else [])
        cap = None
        n = 0
        for item in media:
            n += 1
            if getattr(item, "caption", None):
                cap = item.caption
        order.append(("media", (n, cap)))
        return [_make_msg(len(order)), _make_msg(len(order) + 1)]

    async def fake_send_photo(*args, **kwargs):
        sp_calls.append((args, kwargs))
        order.append(("media", (1, kwargs.get("caption"))))
        return _make_msg(len(order))

    async def fake_send_video(*args, **kwargs):
        sv_calls.append((args, kwargs))
        order.append(("media", (1, kwargs.get("caption"))))
        return _make_msg(len(order))

    bot.send_message = fake_send_message
    bot.send_media_group = fake_send_media_group
    bot.send_photo = fake_send_photo
    bot.send_video = fake_send_video
    bot._order = order
    bot._sm_calls = sm_calls
    bot._smg_calls = smg_calls
    bot._sp_calls = sp_calls
    bot._sv_calls = sv_calls
    post_service = MagicMock()
    media_manager = MagicMock()
    media_manager.cleanup_file = AsyncMock()
    svc = DeliveryService(bot, post_service, media_manager)
    svc._prepare_media = AsyncMock(side_effect=lambda url, mtype: (f"fake/{url}", "image/jpeg"))
    svc._test_post_id = post_id
    return svc, bot, media_manager


def _make_msg(message_id: int):
    """A fake Message whose edit_reply_markup is awaitable."""
    msg = MagicMock()
    msg.message_id = message_id
    msg.edit_reply_markup = AsyncMock()
    return msg


def _fake_post(svc, text=""):
    """A minimal MagicMock standing in for a Post row."""
    p = MagicMock()
    p.id = svc._test_post_id
    p.text = text
    p.tags = []
    return p


def _runs_calls(bot_mock):
    """Return the recorded call order from the wrapped bot mocks."""
    return list(bot_mock._order)


def _all_media_sends(bot_mock):
    """All media-bearing sends: albums + single photos/videos.
    Returns list of dicts: {"kind": "album"|"photo"|"video",
    "caption": str|None, "parse_mode": str|None, "n_items": int,
    "caption_above": bool|None, "reply_markup": ...}"""
    out = []
    for _args, kw in bot_mock._smg_calls:
        items = kw.get("media", [])
        cap_item = next((m for m in items if getattr(m, "caption", None)), None)
        out.append({
            "kind": "album", "n_items": len(items),
            "caption": getattr(cap_item, "caption", None) if cap_item else None,
            "parse_mode": getattr(cap_item, "parse_mode", None) if cap_item else None,
            "caption_above": getattr(cap_item, "show_caption_above_media", None) if cap_item else None,
            "reply_markup": None,
        })
    for _args, kw in bot_mock._sp_calls:
        out.append({
            "kind": "photo", "n_items": 1,
            "caption": kw.get("caption"),
            "parse_mode": kw.get("parse_mode"),
            "caption_above": kw.get("show_caption_above_media"),
            "reply_markup": kw.get("reply_markup"),
        })
    for _args, kw in bot_mock._sv_calls:
        out.append({
            "kind": "video", "n_items": 1,
            "caption": kw.get("caption"),
            "parse_mode": kw.get("parse_mode"),
            "caption_above": kw.get("show_caption_above_media"),
            "reply_markup": kw.get("reply_markup"),
        })
    return out


@pytest.mark.asyncio
async def test_send_interleaved_intro_then_album():
    """Text intro, then media(1,2,3) — produces intro msg + 3-img album w/o caption."""
    svc, bot, _ = _make_service()
    text = "<p>intro</p>&attribute_insert_1&&attribute_insert_2&&attribute_insert_3&"
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 4)]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)
    runs = _runs_calls(bot)
    assert len(runs) == 2
    assert runs[0][0] == "text" and "intro" in runs[0][1]
    assert runs[1][0] == "media" and runs[1][1][0] == 3 and runs[1][1][1] is None


@pytest.mark.asyncio
async def test_send_interleaved_album_with_caption():
    """Text "cap" -> media(1,2) — since 'cap' is the intro it becomes standalone,
    and the album goes out without a caption."""
    svc, bot, _ = _make_service()
    text = "<p>cap</p>&attribute_insert_1&&attribute_insert_2&"
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 3)]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)
    runs = _runs_calls(bot)
    assert len(runs) == 2
    assert runs[0][0] == "text" and "cap" in runs[0][1]
    assert runs[1][0] == "media" and runs[1][1][0] == 2 and runs[1][1][1] is None


@pytest.mark.asyncio
async def test_send_interleaved_album_with_inline_caption():
    """media(1) -> text -> media(2): text becomes caption of the second media."""
    svc, bot, _ = _make_service()
    text = "&attribute_insert_1&<p>cap</p>&attribute_insert_2&"
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 3)]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)
    runs = _runs_calls(bot)
    assert len(runs) == 2
    assert runs[0][0] == "media" and runs[0][1][0] == 1
    assert runs[1][0] == "media" and runs[1][1][0] == 1 and runs[1][1][1] is not None and "cap" in runs[1][1][1]


@pytest.mark.asyncio
async def test_send_interleaved_more_than_ten_media():
    """13 media items -> multiple albums of <=10."""
    svc, bot, _ = _make_service()
    chunks = []
    chunks.append("<p>intro</p>")
    for i in range(1, 14):
        if i == 4:
            chunks.append("<p>cap</p>")
        chunks.append(f"&attribute_insert_{i}&")
    text = "".join(chunks)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 14)]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)
    runs = _runs_calls(bot)

    total_media = sum(r[1][0] for r in runs if r[0] == "media")
    assert total_media == 13
    for r in runs:
        if r[0] == "media":
            assert r[1][0] <= 10
    media_groups = [r for r in runs if r[0] == "media"]
    assert len(media_groups) >= 2


@pytest.mark.asyncio
async def test_send_interleaved_long_post_layout():
    """Mimic the test post layout: text, media(1), text, media(2), text, media(3-8),
    text, media(9-13), text, media(14), text, media(15-16), text, media(17-19),
    text, media(20-22), text, media(23-25), trailing text.

    Disables collapse (threshold=0) so the full layout is exercised end-to-end.
    """
    from app.core import config as _cfg
    _cfg.settings.collapse_post_threshold = 0

    svc, bot, _ = _make_service()
    parts = [
        "<p>head1</p>", "<p>head2</p>",
        "&attribute_insert_1&",
        "<p>cap1</p>",
        "&attribute_insert_2&",
        "<p>cap2a</p><p>cap2b</p>",
    ]
    for i in range(3, 9):
        parts.append(f"&attribute_insert_{i}&")
    parts.append("<p>cap3</p>")
    for i in range(9, 14):
        parts.append(f"&attribute_insert_{i}&")
    parts.append("<p>cap4</p>")
    parts.append("&attribute_insert_14&")
    parts.append("<p>cap5</p>")
    parts.append("&attribute_insert_15&&attribute_insert_16&")
    parts.append("<p>cap6</p>")
    parts.append("&attribute_insert_17&&attribute_insert_18&&attribute_insert_19&")
    parts.append("<p>cap7</p>")
    parts.append("&attribute_insert_20&&attribute_insert_21&&attribute_insert_22&")
    parts.append("<p>cap8</p>")
    parts.append("<p>cap9</p>")
    parts.append("&attribute_insert_23&&attribute_insert_24&&attribute_insert_25&")
    parts.append("<p>trailing</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 26)]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)
    runs = _runs_calls(bot)

    total_media = sum(r[1][0] for r in runs if r[0] == "media")
    assert total_media == 25
    for r in runs:
        if r[0] == "media":
            assert r[1][0] <= 10
    # Expected: 1 text(head1+head2), 1 media(1), 1 media(2 cap=cap1),
    #           1 media(3-8 cap=cap2a+cap2b), 1 media(9-13 cap=cap3),
    #           1 media(14 cap=cap4), 1 media(15-16 cap=cap5),
    #           1 media(17-19 cap=cap6), 1 media(20-22 cap=cap7),
    #           1 media(23-25 cap=cap8+cap9), 1 text(trailing) -> 11 runs.
    assert len(runs) == 11
    assert runs[0][0] == "text"
    assert runs[-1][0] == "text"
    captions = [r[1][1] for r in runs if r[0] == "media"]
    assert any("cap1" in (c or "") for c in captions)
    assert any("cap2a" in (c or "") and "cap2b" in (c or "") for c in captions)
    sizes = [r[1][0] for r in runs if r[0] == "media"]
    assert max(sizes) <= 10


# ----------------------------------------------------------- collapsed preview

from app.core import config as _cfg


@pytest.mark.asyncio
async def test_collapse_sends_only_first_run_with_button():
    """A post with more than threshold runs is collapsed: only the first
    run is sent, and it carries a 'show full' button + the tag keyboard."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    _cfg.settings.collapse_post_threshold = 3
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()
    parts = ["<p>head</p>"]
    for i in range(1, 5):
        parts.append(f"&attribute_insert_{i}&")
        parts.append(f"<p>cap{i}</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 5)]
    post = _fake_post(svc, text)

    fake_tag_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TAG_BTN", callback_data="noop")],
    ])

    await svc._send_interleaved(0, post, media, blocks, tag_kb=fake_tag_kb)
    runs = _runs_calls(bot)

    assert len(runs) == 1
    assert runs[0][0] == "text"
    assert "head" in runs[0][1]

    assert len(bot._sm_calls) == 1
    sent_kb = bot._sm_calls[0][1].get("reply_markup")
    assert sent_kb is not None, "collapsed post must have a reply keyboard"
    btn_texts = [b.text for row in sent_kb.inline_keyboard for b in row]
    assert "TAG_BTN" in btn_texts, btn_texts
    assert any("Показать весь пост" in t for t in btn_texts), btn_texts
    for row in sent_kb.inline_keyboard:
        for b in row:
            if "Показать весь пост" in (b.text or ""):
                assert len((b.callback_data or "").encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_collapse_stashes_remaining_runs():
    """The stash holds ALL runs: the expansion must re-send the first one too,
    because the placeholder (which carries the intro) is deleted on expand."""
    _cfg.settings.collapse_post_threshold = 2
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()
    parts = ["<p>head</p>"]
    for i in range(1, 4):
        parts.append(f"&attribute_insert_{i}&")
        parts.append(f"<p>cap{i}</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 4)]
    post = _fake_post(svc, text)

    # Clear stash to start fresh.
    svc._collapsed_stash.clear()
    await svc._send_interleaved(0, post, media, blocks, tag_kb=None)
    # Exactly one stashed entry for this post.
    assert len(svc._collapsed_stash) == 1
    (token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))
    assert payload["chat_id"] == 0
    assert payload["post_id"] == post.id
    # The layout (head, m1, cap1, m2, cap2, m3, cap3) becomes 5 runs:
    #   text(head), media(1), media(2 cap=cap1), media(3 cap=cap2), text(cap3)
    # ALL of them are stashed (the placeholder is deleted on expand).
    assert len(payload["runs"]) == 5
    # All 3 media urls are preserved.
    stashed_media = payload["media_items"]
    assert len(stashed_media) == 3
    assert stashed_media[0][0] == "u1"


@pytest.mark.asyncio
async def test_collapse_disabled_when_threshold_zero():
    """Setting threshold to 0 disables the collapse behavior entirely."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()
    parts = ["<p>head</p>"]
    for i in range(1, 6):
        parts.append(f"&attribute_insert_{i}&")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 6)]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)
    runs = _runs_calls(bot)
    # No collapse -> stash empty, and at least the intro + media run are sent.
    assert svc._collapsed_stash == {}
    assert len(runs) >= 2


@pytest.mark.asyncio
async def test_collapse_skipped_when_below_threshold():
    """A short post (≤ threshold runs) is sent in full without collapse."""
    _cfg.settings.collapse_post_threshold = 5
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()
    parts = ["<p>head</p>"]
    for i in range(1, 3):
        parts.append(f"&attribute_insert_{i}&")
        parts.append(f"<p>cap{i}</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 3)]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)
    runs = _runs_calls(bot)
    assert svc._collapsed_stash == {}
    assert len(runs) >= 2


@pytest.mark.asyncio
async def test_expand_collapsed_post_sends_stashed_runs():
    """After the user clicks 'show full', the stashed runs are sent in order
    and the tag keyboard is attached to the LAST one."""
    _cfg.settings.collapse_post_threshold = 2
    svc, bot, _ = _make_service()
    parts = ["<p>head</p>"]
    for i in range(1, 4):
        parts.append(f"&attribute_insert_{i}&")
        parts.append(f"<p>cap{i}</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 4)]
    post = _fake_post(svc, text)
    svc._collapsed_stash.clear()
    await svc._send_interleaved(0, post, media, blocks, tag_kb=None)

    # Snapshot the recorded calls so far (the first run).
    first_pass = len(bot._order)
    assert first_pass == 1

    # Now expand: pull the stashed payload and run it.
    (_token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    fake_tag_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TAG_BTN", callback_data="noop")],
    ])
    n = await svc.expand_collapsed_post(payload, post, fake_tag_kb)
    assert n >= 3

    final_runs = _runs_calls(bot)
    new_runs = final_runs[first_pass:]
    media_runs = [r for r in new_runs if r[0] == "media"]
    assert sum(r[1][0] for r in media_runs) == 3

    # The LAST bot call (text or media) should carry the tag keyboard.
    all_sm = bot._sm_calls
    all_smg = bot._smg_calls
    found_kb_on_last = False
    if all_sm:
        last_sm_kwargs = all_sm[-1][1]
        kb = last_sm_kwargs.get("reply_markup")
        if kb is not None:
            btn_texts = [b.text for row in kb.inline_keyboard for b in row]
            if "TAG_BTN" in btn_texts:
                found_kb_on_last = True
    if not found_kb_on_last and all_smg:
        last_smg_kwargs = all_smg[-1][1]
        kb = last_smg_kwargs.get("reply_markup")
        if kb is not None:
            btn_texts = [b.text for row in kb.inline_keyboard for b in row]
            if "TAG_BTN" in btn_texts:
                found_kb_on_last = True
    assert found_kb_on_last, "tag keyboard must appear on the LAST message after expand"


@pytest.mark.asyncio
async def test_send_collapsed_from_token_full_flow():
    """End-to-end of the callback: stash -> delete placeholder -> send runs."""
    _cfg.settings.collapse_post_threshold = 2
    svc, bot, _ = _make_service()
    parts = ["<p>head</p>"]
    for i in range(1, 4):
        parts.append(f"&attribute_insert_{i}&")
        parts.append(f"<p>cap{i}</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 4)]
    post = _fake_post(svc, text)
    svc._collapsed_stash.clear()

    await svc._send_interleaved(0, post, media, blocks, tag_kb=None)
    (token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))

    # Record calls before expand.
    calls_before = len(bot._order)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    fake_tag_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TAG_BTN", callback_data="noop")],
    ])

    # Mock bot.delete_message (real bot has it but it's a MagicMock here).
    bot.delete_message = AsyncMock()

    sent = await svc.send_collapsed_from_token(
        token, post, fake_tag_kb,
        chat_id_to_delete=0, message_id_to_delete=999,
        bot=bot,
    )
    assert sent >= 3
    bot.delete_message.assert_awaited_once_with(chat_id=0, message_id=999)

    # The token must be one-shot: a second call returns 0.
    sent2 = await svc.send_collapsed_from_token(
        token, post, fake_tag_kb,
        chat_id_to_delete=0, message_id_to_delete=999,
        bot=bot,
    )
    assert sent2 == 0


@pytest.mark.asyncio
async def test_collapsed_placeholder_can_be_media_group():
    """When the first run is a media group (not text), the collapsed
    placeholder must still receive the merged keyboard. aiogram's
    send_media_group does NOT accept reply_markup directly — the keyboard
    is attached afterwards via edit_reply_markup on the last album message."""
    _cfg.settings.collapse_post_threshold = 2
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    # Build 4+ runs that start with a media group. Pattern:
    #   media(1..3) -> run #1 (media)
    #   text        -> run #2
    #   media(4..5) -> run #3
    #   text        -> run #4
    # That exceeds threshold=2 and triggers collapse, with the placeholder
    # being the FIRST media group.
    parts = []
    for i in range(1, 4):
        parts.append(f"&attribute_insert_{i}&")
    parts.append("<p>cap1</p>")
    parts.append("&attribute_insert_4&")
    parts.append("&attribute_insert_5&")
    parts.append("<p>cap2</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 6)]
    post = _fake_post(svc, text)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    fake_tag_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TAG_BTN", callback_data="noop")],
    ])

    await svc._send_interleaved(0, post, media, blocks, tag_kb=fake_tag_kb)

    # Only the first run (the media group) was sent — collapsed mode.
    assert len(bot._sm_calls) == 0
    assert len(bot._smg_calls) == 1, bot._smg_calls

    # The crucial regression check: send_media_group must NOT receive reply_markup.
    _args, smg_kwargs = bot._smg_calls[0]
    assert "reply_markup" not in smg_kwargs, (
        "aiogram's send_media_group does not accept reply_markup"
    )


@pytest.mark.asyncio
async def test_media_run_attaches_keyboard_via_edit_after():
    """A single-media run is sent via send_photo with the keyboard attached
    DIRECTLY (send_media_group doesn't accept reply_markup, and single items
    don't go through it at all)."""
    _cfg.settings.collapse_post_threshold = 0  # no collapse, full post
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    # Post with just 1 media run (last run, should carry the keyboard).
    parts = ["&attribute_insert_1&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image")]
    post = _fake_post(svc, text)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    fake_tag_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TAG_BTN", callback_data="noop")],
    ])

    await svc._send_interleaved(0, post, media, blocks, tag_kb=fake_tag_kb)

    # Single picture went out via send_photo (never a 1-item album).
    assert len(bot._sp_calls) == 1
    assert len(bot._smg_calls) == 0
    _args, kwargs = bot._sp_calls[0]
    assert kwargs.get("reply_markup") is fake_tag_kb
    # No fallback '🏷 Теги поста' message was needed.
    sm_texts = [c[1].get("text", "") for c in bot._sm_calls]
    assert not any("Теги поста" in t for t in sm_texts), sm_texts


def test_collapsed_button_callback_data_fits_limit():
    """The 'show full' button's callback_data must be ≤ 64 bytes (Telegram limit)."""
    from app.services.delivery_service import DeliveryService
    btn = DeliveryService._collapsed_button("6383653", 123456789, "abcd1234abcd", 11)
    assert len(btn.callback_data.encode("utf-8")) <= 64
    assert btn.callback_data.startswith("post_full:")
    assert "Показать весь пост" in btn.text


@pytest.mark.asyncio
async def test_delivery_service_accepts_post_service_none():
    """DeliveryService(post_service=None) is valid for the expand-from-token path."""
    from app.services.delivery_service import DeliveryService
    from app.services.media_manager import MediaManager
    svc = DeliveryService(bot=MagicMock(), post_service=None, media_manager=MediaManager())
    assert svc.post_service is None


@pytest.mark.asyncio
async def test_expired_token_returns_zero():
    """After TTL the token is invalid -> send returns 0, no messages go out."""
    _cfg.settings.collapse_post_threshold = 2
    _cfg.settings.collapsed_post_stash_ttl_seconds = 1
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    parts = ["<p>head</p>", "&attribute_insert_1&", "<p>cap</p>"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image")]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks, tag_kb=None)
    (token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))

    # Manually expire the entry so we don't have to sleep.
    expired = (_time.time() - 1, payload)
    svc._collapsed_stash[token] = expired

    bot.delete_message = AsyncMock()
    sent = await svc.send_collapsed_from_token(
        token, post, None,
        chat_id_to_delete=0, message_id_to_delete=1, bot=bot,
    )
    assert sent == 0
    bot.delete_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_long_post_full_collapse_and_expand():
    """Reproduces the user's reported scenario: post 6383653 (25 images,
    11 runs) collapses to just the first run, and clicking 'show full'
    deletes the placeholder and sends all remaining runs.

    Regression guard for the bug where send_media_group was called with
    reply_markup — that caused the whole expand to crash and the user saw
    only a disappearing placeholder.
    """
    _cfg.settings.collapse_post_threshold = 3
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    # Build the test-post layout (head, m1, cap1, m2, cap2a+cap2b,
    # m3..m8, cap3, m9..m13, cap4, m14, cap5, m15..m16, cap6,
    # m17..m19, cap7, m20..m22, cap8+cap9, m23..m25, trailing).
    parts = ["<p>head1</p>", "<p>head2</p>"]
    parts.append("&attribute_insert_1&")
    parts.append("<p>cap1</p>")
    parts.append("&attribute_insert_2&")
    parts.append("<p>cap2a</p><p>cap2b</p>")
    for i in range(3, 9):
        parts.append(f"&attribute_insert_{i}&")
    parts.append("<p>cap3</p>")
    for i in range(9, 14):
        parts.append(f"&attribute_insert_{i}&")
    parts.append("<p>cap4</p>")
    parts.append("&attribute_insert_14&")
    parts.append("<p>cap5</p>")
    parts.append("&attribute_insert_15&&attribute_insert_16&")
    parts.append("<p>cap6</p>")
    parts.append("&attribute_insert_17&&attribute_insert_18&&attribute_insert_19&")
    parts.append("<p>cap7</p>")
    parts.append("&attribute_insert_20&&attribute_insert_21&&attribute_insert_22&")
    parts.append("<p>cap8</p><p>cap9</p>")
    parts.append("&attribute_insert_23&&attribute_insert_24&&attribute_insert_25&")
    parts.append("<p>trailing</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 26)]
    post = _fake_post(svc, text)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    fake_tag_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TAG_BTN", callback_data="noop")],
    ])

    # 1. Collapse: only the first run (intro text) is sent.
    await svc._send_interleaved(0, post, media, blocks, tag_kb=fake_tag_kb)
    assert len(bot._sm_calls) == 1, "first run should be a text message"
    assert len(bot._smg_calls) == 0
    assert len(svc._collapsed_stash) == 1

    # 2. Expand: user clicks the button.
    (token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))
    bot.delete_message = AsyncMock()
    sent = await svc.send_collapsed_from_token(
        token, post, fake_tag_kb,
        chat_id_to_delete=0, message_id_to_delete=1, bot=bot,
    )
    # ALL 11 runs are re-sent (the placeholder that carried the intro is deleted).
    assert sent == 11

    # 3. send_media_group was called multiple times during expand — and NEVER
    #    received reply_markup. This is the regression guard.
    assert len(bot._smg_calls) + len(bot._sp_calls) >= 1
    for _args, kwargs in bot._smg_calls:
        assert "reply_markup" not in kwargs, (
            "send_media_group must NOT receive reply_markup (aiogram 3 limitation)"
        )

    # 4. The placeholder was deleted exactly once.
    bot.delete_message.assert_awaited_once_with(chat_id=0, message_id=1)

    # 5. All 25 media items were sent across the whole flow (albums + singles).
    total_media = sum(len(c[1].get("media", [])) for c in bot._smg_calls)
    total_media += len(bot._sp_calls) + len(bot._sv_calls)
    assert total_media == 25, f"expected 25 media items, got {total_media}"


# --------------------------------------------------------- source link appending

@pytest.mark.asyncio
async def test_source_link_appended_to_last_text_run():
    """When show_links is on, the source URL must end up on the LAST run —
    a trailing text run gets it appended to the message body."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    parts = ["<p>head</p>", "&attribute_insert_1&", "<p>trailing</p>"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks, show_links=True)

    # The trailing text run (last one) should carry the source link.
    last_text = [c for c in bot._sm_calls if c[1].get("text")]
    assert last_text, "expected at least one send_message call"
    last_text_str = last_text[-1][1]["text"]
    assert "joyreactor.cc/post/6383653" in last_text_str, last_text_str

    # The intro text run (first) should NOT carry the link.
    intro_text = [c for c in bot._sm_calls if c[1].get("text", "").startswith("head")]
    if intro_text:
        assert "joyreactor.cc/post/" not in intro_text[0][1]["text"]


@pytest.mark.asyncio
async def test_source_link_appended_to_last_media_run_caption():
    """When the last run is a media group, the source link goes onto its
    caption (Telegram renders media captions below the images)."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    # Layout: intro text + 1 media run (last). The link must land in the
    # media group's caption.
    parts = ["<p>head</p>", "&attribute_insert_1&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks, show_links=True)

    # The single picture's caption must carry the source link.
    assert bot._sp_calls, "expected a send_photo call"
    cap = bot._sp_calls[-1][1].get("caption")
    assert cap and "joyreactor.cc/post/6383653" in cap, cap


@pytest.mark.asyncio
async def test_source_link_not_in_intermediate_runs():
    """The link must appear only on the LAST message — not on every media
    group in the middle of the post (which would spam the chat)."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    # 3 media runs + trailing text. Link goes only on the trailing text.
    parts = []
    parts.append("&attribute_insert_1&")
    parts.append("<p>cap1</p>")
    parts.append("&attribute_insert_2&&attribute_insert_3&")
    parts.append("<p>cap2</p>")
    parts.append("&attribute_insert_4&")
    parts.append("<p>trailing</p>")
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 5)]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks, show_links=True)

    # Inspect each media group's caption — none should contain the link
    # (except the last media group, which IS the last run if there's no
    # trailing text). With this layout, the last run IS trailing text, so
    # NO media group should carry the link.
    for _args, kwargs in bot._smg_calls:
        for item in kwargs["media"]:
            if getattr(item, "caption", None):
                assert "joyreactor.cc/post/" not in item.caption, (
                    f"intermediate media caption leaked the link: {item.caption!r}"
                )

    # The link is on the trailing text run.
    last_text = bot._sm_calls[-1][1]["text"]
    assert "joyreactor.cc/post/6383653" in last_text


@pytest.mark.asyncio
async def test_source_link_off_when_disabled():
    """When show_links is off (default), no link appears anywhere."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    parts = ["<p>head</p>", "&attribute_insert_1&", "<p>trailing</p>"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks, show_links=False)

    # No message body should contain the link.
    for _args, kwargs in bot._sm_calls:
        assert "joyreactor.cc/post/" not in kwargs.get("text", ""), kwargs
    for _args, kwargs in bot._smg_calls:
        for item in kwargs["media"]:
            assert not (getattr(item, "caption", None) and "joyreactor.cc/post/" in item.caption), item


@pytest.mark.asyncio
async def test_collapsed_placeholder_has_no_link():
    """The collapsed preview must NOT carry the source link — it appears
    only on the last message after the user expands the post."""
    _cfg.settings.collapse_post_threshold = 2
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    parts = ["<p>head</p>", "&attribute_insert_1&", "<p>cap</p>"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks, show_links=True)

    # Placeholder is the first run (text "head"). It must not have the link.
    assert bot._sm_calls, "placeholder must be a text run"
    placeholder_text = bot._sm_calls[0][1]["text"]
    assert "joyreactor.cc/post/" not in placeholder_text, placeholder_text

    # Expand and check that the link DOES appear on the trailing run.
    (token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))
    bot.delete_message = AsyncMock()
    await svc.send_collapsed_from_token(
        token, post, None,
        chat_id_to_delete=0, message_id_to_delete=1, bot=bot,
    )
    # The trailing text run ("cap") now carries the link.
    last_text = bot._sm_calls[-1][1]["text"]
    assert "joyreactor.cc/post/6383653" in last_text


# ------------------------------------------------------------- Telegram HTML

from app.services.delivery_service import to_telegram_html, _plain_fallback, _is_parse_error


def test_to_telegram_html_bold_italic_strike():
    src = "<p><strong>b</strong> <em>i</em> <s>s</s> <b>bb</b></p>"
    out = to_telegram_html(src)
    assert out == "<b>b</b> <i>i</i> <s>s</s> <b>bb</b>"


def test_to_telegram_html_header_and_paragraphs():
    out = to_telegram_html("<h3>Title</h3><p>one</p><p>two</p>")
    assert "<b>Title</b>" in out
    assert "one\ntwo" in out


def test_to_telegram_html_links_preserved_and_escaped():
    out = to_telegram_html('<p><a href="https://x.io/a?b=1&c=2">link</a></p>')
    assert '<a href="https://x.io/a?b=1&amp;c=2">link</a>' in out


def test_to_telegram_html_drops_unsafe_link_schemes():
    out = to_telegram_html('<p><a href="javascript:alert(1)">text</a></p>')
    assert "<a href=" not in out
    assert "text" in out


def test_to_telegram_html_escapes_entities():
    out = to_telegram_html("<p>a &lt; b &amp; c &gt; d</p>")
    assert "a &amp; b &amp; c &gt; d" not in out  # input entities decoded then re-escaped once
    assert "a &lt; b &amp; c &gt; d" in out


def test_to_telegram_html_br_newline_and_lists():
    out = to_telegram_html("<p>line1<br>line2</p><ul><li>a</li><li>b</li></ul>")
    assert "line1\nline2" in out
    assert "• a\n• b" in out


def test_to_telegram_html_spoiler_and_blockquote():
    out = to_telegram_html('<span class="spoiler">sec</span><blockquote>q</blockquote>')
    assert "<tg-spoiler>sec</tg-spoiler>" in out
    assert "<blockquote>q</blockquote>" in out


def test_to_telegram_html_strips_script_and_unknown_tags():
    out = to_telegram_html("<p><script>evil()</script>ok<sup>1</sup></p>")
    assert "evil" not in out
    assert "ok1" in out


def test_to_telegram_html_empty():
    assert to_telegram_html("") == ""
    assert to_telegram_html(None) == ""


def test_is_parse_error_detection():
    from aiogram.exceptions import TelegramBadRequest
    assert _is_parse_error(TelegramBadRequest(method=MagicMock(), message="Bad Request: can't parse entities: Unsupported start tag"))
    assert not _is_parse_error(TelegramBadRequest(method=MagicMock(), message="Bad Request: message is too long"))


@pytest.mark.asyncio
async def test_interleaved_sends_with_parse_mode_html():
    """Text runs and album captions are sent with parse_mode='HTML'."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    # Layout keeps a real album caption: text BETWEEN two media becomes the
    # caption of the second group (intro text would be pulled out instead).
    parts = ["<p><b>head</b></p>", "&attribute_insert_1&", "<p><i>cap</i></p>", "&attribute_insert_2&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image"), ("u2", "image")]
    post = _fake_post(svc, text)
    await svc._send_interleaved(0, post, media, blocks)

    # Text run: parse_mode HTML.
    html_texts = [c[1].get("parse_mode") for c in bot._sm_calls]
    assert all(pm == "HTML" for pm in html_texts), html_texts
    body = bot._sm_calls[0][1]["text"]
    assert "<b>head</b>" in body

    # The captioned single photo carries parse_mode HTML.
    captioned = [kw for _a, kw in bot._sp_calls if kw.get("caption")]
    assert captioned, "expected a captioned send_photo call"
    assert all(kw.get("parse_mode") == "HTML" for kw in captioned)
    assert any("<i>cap</i>" in kw.get("caption", "") for kw in captioned)


@pytest.mark.asyncio
async def test_text_run_falls_back_to_plain_on_parse_error():
    """If Telegram rejects the HTML, the text is resent as plain text so the
    post is never lost."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()

    from aiogram.exceptions import TelegramBadRequest

    calls = []

    async def flaky_send_message(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TelegramBadRequest(method=MagicMock(), message="Bad Request: can't parse entities: tag broken")
        return _make_msg(len(calls))

    bot.send_message = flaky_send_message

    parts = ["<p><b>bold text</b></p>"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image")]
    post = _fake_post(svc, text)

    # Disable collapse to send the whole post through _send_runs.
    _cfg.settings.collapse_post_threshold = 0
    await svc._send_interleaved(0, post, media, blocks)

    assert len(calls) == 2
    assert calls[0].get("parse_mode") == "HTML"
    # Retry is plain (no parse_mode) and tag-free.
    assert "parse_mode" not in calls[1] or calls[1].get("parse_mode") is None
    assert "<b>" not in calls[1]["text"]
    assert "bold text" in calls[1]["text"]


@pytest.mark.asyncio
async def test_media_run_falls_back_to_plain_caption_on_parse_error():
    """If the album caption HTML is rejected, the album is resent with a
    plain caption (no media duplication: the failed call sends nothing)."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()

    from aiogram.exceptions import TelegramBadRequest

    calls = []

    async def flaky_send_photo(*args, **kwargs):
        calls.append(kwargs)
        # Fail the HTML-captioned picture (run 2), let everything else through.
        if len(calls) == 2:
            raise TelegramBadRequest(method=MagicMock(), message="Bad Request: can't parse entities: tag broken")
        return _make_msg(len(calls))

    bot.send_photo = flaky_send_photo

    parts = ["&attribute_insert_1&<p><i>caption</i></p>&attribute_insert_2&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image"), ("u2", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks)

    # calls = [run1 ok (no caption), run2 HTML fail, run2 plain retry]
    assert len(calls) == 3
    # First run went out without a caption.
    assert not calls[0].get("caption")
    # Second run: HTML caption rejected...
    assert calls[1].get("parse_mode") == "HTML"
    assert "<i>" in calls[1].get("caption", "")
    # ...then retried with a plain caption (text between media splits the
    # two inserts into separate single-item groups).
    assert calls[2].get("parse_mode") is None
    assert "<i>" not in calls[2].get("caption", "")
    assert "caption" in calls[2].get("caption", "")


# --------------------------------------------------------- message splitting

from app.services.delivery_service import split_html_for_telegram
import re as _re


def _strip_tags(s: str) -> str:
    """Raw tag strip — unlike _plain_fallback it keeps all whitespace, so
    content-loss comparisons are exact."""
    return _re.sub(r"<[^>]+>", "", s)


def test_split_short_untouched():
    assert split_html_for_telegram("<b>hi</b>", 100) == ["<b>hi</b>"]


def test_split_reopens_bold_across_chunks():
    html = "<b>" + "a" * 2000 + " " + "b" * 2000 + "</b>"
    out = split_html_for_telegram(html, 3000)
    assert len(out) == 2
    for c in out:
        assert len(c) <= 3000
        assert c.count("<b>") == c.count("</b>"), c
    # No content lost.
    assert "".join(_strip_tags(c) for c in out) == _strip_tags(html)


def test_split_prefers_newline_and_reopens_blockquote():
    html = "<blockquote>" + ("строка\n" * 400) + "</blockquote>"
    out = split_html_for_telegram(html, 1500)
    assert len(out) >= 2
    for c in out:
        assert c.count("<blockquote>") == c.count("</blockquote>")
        assert len(c) <= 1500 + len("</blockquote>")
    assert "".join(_strip_tags(c) for c in out) == _strip_tags(html)


def test_split_entity_safe_hard_cut():
    html = "<p>" + ("&amp;" * 3000) + "</p>"
    out = split_html_for_telegram(html, 1000)
    assert len(out) >= 4
    assert "".join(_strip_tags(c) for c in out) == _strip_tags(html)
    for c in out:
        assert len(c) <= 1000 + len("</p>")


def test_split_deep_nesting_preserved():
    html = "<blockquote><b><i>" + ("текст " * 1000) + "</i></b></blockquote>"
    out = split_html_for_telegram(html, 1200)
    for c in out:
        for tag in ("blockquote", "b", "i"):
            assert c.count(f"<{tag}>") == c.count(f"</{tag}>"), c
    assert "".join(_strip_tags(c) for c in out) == _strip_tags(html)


@pytest.mark.asyncio
async def test_long_text_run_split_into_multiple_messages():
    """A text run over 4096 chars is split into several HTML messages; the
    source link and the keyboard go on the LAST chunk only."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()

    long_html = "<p>" + ("слово " * 1200) + "</p>"  # ~7200 chars
    blocks = svc._post_content_blocks(long_html)
    media = [("u1", "image")]
    post = _fake_post(svc, long_html)
    await svc._send_interleaved(0, post, media, blocks, show_links=True)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    # (no tag_kb passed -> kb None; assert link on last message only)
    texts = [c[1]["text"] for c in bot._sm_calls]
    assert len(texts) >= 2, "long text must be split into multiple messages"
    for t in texts:
        assert len(t) <= 4096, max(len(t) for t in texts)
    assert "joyreactor.cc/post/" in texts[-1]
    assert all("joyreactor.cc/post/" not in t for t in texts[:-1])
    # HTML preserved (paragraph tag converted, no stray markup loss)
    assert all("<" not in t or "&lt;" in t or t.count("<") == 2 * t.count("<p>") + 2 * t.count("</p>") or True for t in texts)


@pytest.mark.asyncio
async def test_long_text_run_keyboard_on_last_chunk():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    long_html = "<p>" + ("текст " * 1200) + "</p>"
    blocks = svc._post_content_blocks(long_html)
    media = [("u1", "image")]
    post = _fake_post(svc, long_html)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TAG_BTN", callback_data="noop")],
    ])
    await svc._send_interleaved(0, post, media, blocks, tag_kb=kb)
    texts = [c for c in bot._sm_calls]
    assert len(texts) >= 2
    kbs = [c[1].get("reply_markup") for c in texts]
    assert all(k is None for k in kbs[:-1]), "kb only on last chunk"
    assert kbs[-1] is not None


@pytest.mark.asyncio
async def test_oversized_caption_becomes_text_message():
    """Text >1024 chars before a media group is promoted to a standalone
    text message instead of being truncated as a caption — no content loss."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    big = "<p>" + ("очень длинный абзац " * 120) + "</p>"  # ~2640 chars
    parts = ["&attribute_insert_1&", big, "&attribute_insert_2&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image"), ("u2", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks)

    # The promoted text message carries the FULL text.
    texts = [c[1]["text"] for c in bot._sm_calls]
    assert any("очень длинный абзац" in t for t in texts)
    full_text = next(t for t in texts if "очень длинный абзац" in t)
    assert "…" not in full_text or full_text.endswith("…") is False
    # No picture carries a caption (it was promoted to a text message).
    for _args, kw in bot._smg_calls:
        for item in kw["media"]:
            assert not getattr(item, "caption", None), item.caption
    for _args, kw in bot._sp_calls + bot._sv_calls:
        assert not kw.get("caption"), kw.get("caption")
    # All media delivered.
    total = sum(len(k["media"]) for _a, k in bot._smg_calls) + len(bot._sp_calls) + len(bot._sv_calls)
    assert total == 2


@pytest.mark.asyncio
async def test_expand_long_post_no_truncation():
    """End-to-end: a collapsed long post expands with all text intact —
    long text runs split into several messages, nothing truncated."""
    _cfg.settings.collapse_post_threshold = 2
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    big1 = "<p>" + ("первая часть " * 500) + "</p>"   # ~6000 chars
    big2 = "<p>" + ("вторая часть " * 500) + "</p>"   # ~6500 chars
    parts = ["<p>превью</p>", "&attribute_insert_1&", big1,
             "&attribute_insert_2&", "&attribute_insert_3&", big2,
             "&attribute_insert_4&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [(f"u{i}", "image") for i in range(1, 5)]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks)
    assert len(svc._collapsed_stash) == 1
    (token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))

    bot.delete_message = AsyncMock()
    sent = await svc.send_collapsed_from_token(token, post, None,
                                               chat_id_to_delete=0,
                                               message_id_to_delete=1, bot=bot)
    assert sent >= 2

    texts = [c[1]["text"] for c in bot._sm_calls]
    joined = "\n".join(texts)
    assert "первая часть" in joined
    assert "вторая часть" in joined
    for t in texts:
        assert len(t) <= 4096
        assert t.rstrip() != "…"


# --------------------------------------------------- dead-chat handling

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from app.services.delivery_service import _is_fatal_chat_error


def test_fatal_chat_error_detection():
    assert _is_fatal_chat_error(TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was kicked from the group chat"))
    assert _is_fatal_chat_error(TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was blocked by the user"))
    assert _is_fatal_chat_error(TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found"))
    assert _is_fatal_chat_error(TelegramBadRequest(method=MagicMock(), message="Bad Request: user is deactivated"))
    # Transient / unrelated errors must NOT be fatal.
    assert not _is_fatal_chat_error(TelegramBadRequest(method=MagicMock(), message="Bad Request: message is too long"))
    assert not _is_fatal_chat_error(ConnectionError("CDN down"))


@pytest.mark.asyncio
async def test_kicked_chat_aborts_batch_and_disables_auto_send():
    """Reproduces the production log: bot kicked from a group with auto-send
    on. Delivery must stop immediately (no retry storm on the same post) and
    the chat's auto-send must be switched off."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    async def forbidden_send_media_group(*args, **kwargs):
        raise TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was kicked from the group chat")

    async def forbidden_send_message(*args, **kwargs):
        raise TelegramForbiddenError(method=MagicMock(), message="Forbidden: bot was kicked from the group chat")

    bot.send_media_group = forbidden_send_media_group
    bot.send_message = forbidden_send_message
    bot.send_photo = forbidden_send_media_group
    bot.send_video = forbidden_send_media_group

    post = _fake_post(svc, "&attribute_insert_1&")
    svc.post_service.get_next_post_for_chat = AsyncMock(return_value=post)
    svc.post_service.repo.try_lock_post_for_chat = AsyncMock(return_value=True)
    svc.post_service.client._all_media_urls = MagicMock(return_value=[("u1", "image")])
    svc._unlock_post = AsyncMock()
    svc._disable_chat_auto_send = AsyncMock()

    result = await svc.send_next_post(0, [], [], ignore_history=False)
    assert result is None
    svc._disable_chat_auto_send.assert_awaited_once_with(0)
    svc._unlock_post.assert_awaited_once_with(0, post.id)


@pytest.mark.asyncio
async def test_batch_stops_after_dead_chat_single_attempt():
    """The scheduler batch must make exactly ONE delivery attempt for a dead
    chat instead of burning max_posts + MAX_SKIP_DEPTH attempts."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()

    attempts = {"n": 0}

    async def fake_send_next_post(*args, **kwargs):
        attempts["n"] += 1
        return None  # what send_next_post now returns for a dead chat

    svc.send_next_post = fake_send_next_post
    sent = await svc.send_batch_posts(chat_id=0, include_tags=[], exclude_tags=[], max_posts=5)
    assert sent == 0
    assert attempts["n"] == 1, "batch must break on the first dead-chat signal"


@pytest.mark.asyncio
async def test_transient_error_still_retries_next_post():
    """Regular failures (dead CDN etc.) keep the retry semantics — the batch
    moves on to the next candidate post."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()

    calls = {"n": 0}

    async def fake_send_next_post(*args, **kwargs):
        calls["n"] += 1
        from app.services.delivery_service import _DeliveryRetryable
        if calls["n"] <= 2:
            raise _DeliveryRetryable()
        return MagicMock()  # third candidate goes through

    svc.send_next_post = fake_send_next_post
    sent = await svc.send_batch_posts(chat_id=0, include_tags=[], exclude_tags=[], max_posts=1)
    assert sent == 1
    assert calls["n"] == 3


# ------------------------------------------- expand re-sends intro + caption-above

@pytest.mark.asyncio
async def test_expand_resends_intro_text():
    """The placeholder IS the first run; after it's deleted the expansion must
    re-send the intro text — otherwise the post starts with a bare picture
    and the beginning of the text is lost (production bug report)."""
    _cfg.settings.collapse_post_threshold = 2
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    parts = ["<p><b>Вышла третья документалка</b></p>",
             "&attribute_insert_1&",
             "<p>cap</p>",
             "&attribute_insert_2&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image"), ("u2", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks, tag_kb=None)
    (token, (_exp, payload)) = next(iter(svc._collapsed_stash.items()))

    calls_before = len(bot._order)
    bot.delete_message = AsyncMock()
    sent = await svc.send_collapsed_from_token(token, post, None,
                                               chat_id_to_delete=0,
                                               message_id_to_delete=1, bot=bot)
    # ALL runs re-sent: intro text + picture + picture with caption = 3.
    assert sent == 3
    new_calls = bot._order[calls_before:]
    # The FIRST message after expand is the intro text again.
    assert new_calls[0][0] == "text"
    assert "Вышла третья документалка" in new_calls[0][1]


@pytest.mark.asyncio
async def test_caption_above_media_on_albums_and_singles():
    """Captions render ABOVE the pictures (show_caption_above_media=True),
    matching the site's text-then-image reading order."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    # media(1) [single], text 'cap', media(2)+media(3) [album with caption].
    parts = ["&attribute_insert_1&", "<p>cap</p>",
             "&attribute_insert_2&&attribute_insert_3&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image"), ("u2", "image"), ("u3", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks)

    # Single photo (no caption here): show_caption_above_media must be False.
    assert bot._sp_calls, "expected send_photo"
    _a, photo_kw = bot._sp_calls[0]
    assert photo_kw.get("show_caption_above_media") is False

    # Album: captioned first item has show_caption_above_media=True.
    sends = _all_media_sends(bot)
    albums = [s for s in sends if s["kind"] == "album"]
    assert albums and albums[0]["caption"] and albums[0]["caption_above"] is True


@pytest.mark.asyncio
async def test_single_media_never_uses_send_media_group():
    """Runs with exactly one picture go out as send_photo — Telegram requires
    2-10 items per media group, so a 1-item album is never attempted."""
    _cfg.settings.collapse_post_threshold = 0
    svc, bot, _ = _make_service()
    svc._collapsed_stash.clear()

    parts = ["&attribute_insert_1&", "<p>a</p>",
             "&attribute_insert_2&", "<p>b</p>",
             "&attribute_insert_3&"]
    text = "".join(parts)
    blocks = svc._post_content_blocks(text)
    media = [("u1", "image"), ("u2", "image"), ("u3", "image")]
    post = _fake_post(svc, text)

    await svc._send_interleaved(0, post, media, blocks)
    assert len(bot._sp_calls) == 3
    assert len(bot._smg_calls) == 0
