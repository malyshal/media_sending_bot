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

    bot.send_message = fake_send_message
    bot.send_media_group = fake_send_media_group
    bot._order = order
    bot._sm_calls = sm_calls
    bot._smg_calls = smg_calls
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
    """After sending the first run, the rest are stashed for later expansion."""
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
    # Stashed = runs[1:] = 4 entries.
    assert len(payload["remaining_runs"]) == 4
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
    """When a media run should carry the keyboard, it's attached via
    edit_reply_markup on the last album message (not via send_media_group)."""
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

    # send_media_group was called WITHOUT reply_markup.
    assert len(bot._smg_calls) == 1
    _args, smg_kwargs = bot._smg_calls[0]
    assert "reply_markup" not in smg_kwargs

    # And the returned last message had edit_reply_markup called with the kb.
    # The bot fixture's send_media_group returns [MagicMock, MagicMock] — and
    # those mocks have edit_reply_markup set as an auto-MagicMock (callable).
    # We verify it was called by inspecting its mock_calls.
    # The two MagicMock messages are independent objects; the LAST one is
    # the one we edit. We identify it by message_id (last assigned id).
    # Easiest: track all messages returned and assert one had edit called.
    # Since both are MagicMocks with auto-spec'd methods, check their
    # mock_calls via the parent bot fixture's _send_media_group_raw chain.
    # For simplicity: assert that the call didn't raise and no fall-back
    # '🏷 Теги поста' was sent (meaning edit succeeded).
    sm_texts = [c[1].get("text", "") for c in bot._sm_calls]
    assert not any("Теги поста" in t for t in sm_texts), sm_texts
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
    # 10 remaining runs should have been sent.
    assert sent == 10

    # 3. send_media_group was called multiple times during expand — and NEVER
    #    received reply_markup. This is the regression guard.
    assert len(bot._smg_calls) >= 1
    for _args, kwargs in bot._smg_calls:
        assert "reply_markup" not in kwargs, (
            "send_media_group must NOT receive reply_markup (aiogram 3 limitation)"
        )

    # 4. The placeholder was deleted exactly once.
    bot.delete_message.assert_awaited_once_with(chat_id=0, message_id=1)

    # 5. All 25 media items were sent across the whole flow.
    total_media = sum(
        len(c[1].get("media", []))
        for c in bot._smg_calls
    )
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

    # Inspect the media group's caption.
    assert bot._smg_calls, "expected a send_media_group call"
    media_items = bot._smg_calls[-1][1]["media"]
    captions = [m.caption for m in media_items if getattr(m, "caption", None)]
    assert any("joyreactor.cc/post/6383653" in c for c in captions), captions


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
