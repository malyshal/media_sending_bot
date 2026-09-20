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
        return MagicMock(message_id=len(order))

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
        return [MagicMock(message_id=len(order)), MagicMock(message_id=len(order) + 1)]

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
async def test_collapsed_button_callback_data_fits_limit():
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
