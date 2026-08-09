"""Adapter tests: event conversion, access control, rule evaluation, participant
lookup, send/read composition.  Requires Hermes core on PYTHONPATH."""

import asyncio
import json
import os
import sys

import pytest

pytest.importorskip("gateway", reason="Hermes core not on PYTHONPATH")

from gateway.config import PlatformConfig, Platform  # noqa: E402
from gateway.platforms.base import MessageType, SendResult  # noqa: E402

from hermes_imsg_cli.adapter import (  # noqa: E402
    ImsgAdapter,
    check_imsg_requirements,
    handle_imsg_poll_tool,
    send_imsg_direct,
)
from hermes_imsg_cli.rules import ImsgRule, parse_imsg_rules  # noqa: E402


def make_adapter(tmp_path, **extra):
    extra.setdefault("state_file", str(tmp_path / "imsg_state.json"))
    extra.setdefault("binary", "/usr/bin/false")
    return ImsgAdapter(PlatformConfig(enabled=True, extra=extra))


# ---------------------------------------------------------------------------
# Platform identity (dynamic member via registry, no Platform.IMSG in core)
# ---------------------------------------------------------------------------

def test_platform_resolves_via_registry(tmp_path):
    adapter = make_adapter(tmp_path)
    assert adapter.platform is Platform("imsg")
    assert adapter.platform.value == "imsg"


# ---------------------------------------------------------------------------
# Legacy flat allowlist (sync shim)
# ---------------------------------------------------------------------------

def test_allowlist_drops_unknown_and_self_messages(tmp_path):
    adapter = make_adapter(tmp_path, allowed_handles=["+12025550101"])
    assert adapter._should_process({"chat_id": 1, "sender": "+12025550102", "text": "hi"}) is False
    assert adapter._should_process(
        {"chat_id": 1, "sender": "+12025550101", "is_from_me": True, "text": "hi"}
    ) is False


def test_allowlist_passes_handle_and_chat(tmp_path):
    adapter = make_adapter(tmp_path, allowed_handles=["+12025550101"], allowed_chats=["16"])
    assert adapter._should_process({"chat_id": 1, "sender": "+12025550101", "text": "hi"}) is True
    assert adapter._should_process(
        {"chat_id": 16, "sender": "+12025550102", "is_group": True, "text": "hi"}
    ) is True


def test_group_allowlist_is_separate_from_dm_allowlist(tmp_path):
    adapter = make_adapter(
        tmp_path,
        allowed_handles=["+12025550101", "+12025550102"],
        group_allowed_handles=["+12025550101", "+12025550102", "+12025550103"],
    )
    assert adapter._should_process(
        {"chat_id": 9, "sender": "+12025550103", "is_group": True, "text": "hi"}
    ) is True
    assert adapter._should_process(
        {"chat_id": 1, "sender": "+12025550103", "is_group": False, "text": "hi"}
    ) is False


@pytest.mark.asyncio
async def test_group_require_mention(tmp_path):
    adapter = make_adapter(tmp_path, allowed_chats=["16"], require_mention=True)
    assert await adapter._should_process_async(
        {"chat_id": 16, "is_group": True, "sender": "+1", "text": "hello"}
    ) is False
    assert await adapter._should_process_async(
        {"chat_id": 16, "is_group": True, "sender": "+1", "text": "Gordie: hello"}
    ) is True


# ---------------------------------------------------------------------------
# Event → MessageEvent (safety invariant: reply routes to chat rowid)
# ---------------------------------------------------------------------------

def test_event_to_message_event_uses_chat_rowid_as_source_chat_id(tmp_path):
    adapter = make_adapter(tmp_path, allowed_handles=["+12025550101"])
    event = {
        "id": 99,
        "chat_id": 16,
        "chat_guid": "any;+;abc",
        "guid": "MSG-GUID",
        "sender": "+12025550101",
        "text": "hello",
        "is_group": True,
        "chat_name": "Family",
    }
    message = adapter._to_message_event(event)
    assert message is not None
    assert message.source.platform is Platform("imsg")
    # The reply-routing key is the exact inbound chat rowid, never the GUID.
    assert message.source.chat_id == "16"
    assert message.source.chat_id_alt == "any;+;abc"
    assert message.message_id == "MSG-GUID"
    assert message.message_type is MessageType.TEXT
    assert message.source.chat_type == "group"


def test_event_to_message_event_prefers_identity_alias(tmp_path):
    adapter = make_adapter(
        tmp_path,
        allowed_chats=["9"],
        identity_aliases={"+12025550104": "Family Member"},
    )
    message = adapter._to_message_event({
        "id": 100,
        "chat_id": 9,
        "sender": "+12025550104",
        "sender_name": "Wrong Name",
        "text": "hey",
        "is_group": True,
    })
    assert message is not None
    assert message.source.user_id == "+12025550104"
    assert message.source.user_name == "Family Member"


def _poll_vote_event(*, is_group=False, event="imessage.poll.voted"):
    return {
        "id": 101,
        "chat_id": 16 if is_group else 3,
        "guid": "VOTE-EVENT",
        "sender": "+155****0001",
        "sender_name": "Family Member",
        "text": " ",
        "is_from_me": False,
        "is_group": is_group,
        "poll": {
            "kind": "vote",
            "event": event,
            "poll_guid": "POLL-GUID",
            "vote": {"option_text": "Yes", "option_id": "OPTION-YES", "event_type": "selected"},
            "question": "Does this work?",
        },
    }


def _reaction_event(*, target="BOT-MESSAGE", add=True, reaction="like"):
    return {
        "id": 102,
        "chat_id": 3,
        "chat_guid": "iMessage;-;+12025550101",
        "guid": "REACTION-EVENT",
        "sender": "+12025550101",
        "sender_name": "Family Member",
        "text": 'Liked "hello"',
        "is_from_me": False,
        "is_reaction": True,
        "reaction_type": reaction,
        "reaction_emoji": "👍" if reaction == "like" else None,
        "is_reaction_add": add,
        "reacted_to_guid": target,
    }


def _poll_created_event():
    return {
        "id": 103,
        "chat_id": 3,
        "chat_guid": "iMessage;-;+12025550101",
        "guid": "POLL-MESSAGE",
        "sender": "+12025550101",
        "text": " ",
        "is_from_me": False,
        "poll": {
            "kind": "created",
            "event": "imessage.poll.created",
            "poll_guid": "POLL-MESSAGE",
            "question": "Dinner?",
            "options": [
                {"id": "PIZZA", "text": "Pizza"},
                {"id": "SUSHI", "text": "Sushi"},
            ],
            "votes": [
                {"option_id": "PIZZA", "event_type": "selected"},
                {"option_id": "PIZZA", "event_type": "selected"},
                {"option_id": "SUSHI", "event_type": "selected"},
            ],
        },
    }


def test_reaction_notifications_own_accepts_only_bot_target(tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    adapter._sent_message_guids["BOT-MESSAGE"] = "3"
    accepted = adapter._to_message_event(_reaction_event())
    assert accepted is not None
    assert accepted.text == "Reaction: Like added to your message"
    assert accepted.raw_message["reacted_to_guid"] == "BOT-MESSAGE"
    assert accepted.metadata["imsg_event_type"] == "reaction"
    assert adapter._to_message_event(_reaction_event(target="SOMEONE-ELSE")) is None


def test_reaction_notifications_all_and_off_modes(tmp_path):
    all_adapter = make_adapter(
        tmp_path, allow_all=True, reaction_notifications="all"
    )
    removed = all_adapter._to_message_event(
        _reaction_event(target="ANY", add=False, reaction="love")
    )
    assert removed is not None
    assert removed.text == "Reaction: Love removed from a message"
    off_adapter = make_adapter(
        tmp_path, allow_all=True, reaction_notifications="off"
    )
    assert off_adapter._to_message_event(_reaction_event()) is None


def test_reaction_notification_environment_and_invalid_default(monkeypatch, tmp_path):
    monkeypatch.setenv("IMSG_REACTION_NOTIFICATIONS", "all")
    assert make_adapter(tmp_path).reaction_notifications == "all"
    monkeypatch.setenv("IMSG_REACTION_NOTIFICATIONS", "invalid")
    assert make_adapter(tmp_path).reaction_notifications == "own"


@pytest.mark.asyncio
async def test_process_reaction_own_resolves_historical_bot_target(
    monkeypatch, tmp_path
):
    adapter = make_adapter(
        tmp_path,
        binary="imsg",
        allowed_handles=["+12025550101"],
        send_read_receipts=False,
    )
    delivered = []
    calls = []

    async def fake_run(args):
        calls.append(args)
        assert args[0] == "history"
        return 0, json.dumps({
            "guid": "BOT-MESSAGE",
            "is_from_me": True,
            "chat_id": 3,
        }), ""

    async def fake_handle(message):
        delivered.append(message)

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    monkeypatch.setattr(adapter, "handle_message", fake_handle)
    await adapter._process_watch_event(_reaction_event())
    assert calls[0][:5] == ["history", "--chat-id", "3", "--limit", "1000"]
    assert len(delivered) == 1
    assert delivered[0].text == "Reaction: Like added to your message"
    assert adapter._sent_message_guids["BOT-MESSAGE"] == "3"


@pytest.mark.asyncio
async def test_unauthorized_reaction_stops_before_ownership_lookup(monkeypatch, tmp_path):
    adapter = make_adapter(
        tmp_path,
        binary="imsg",
        allowed_handles=["+12025550199"],
        send_read_receipts=False,
    )
    called = False

    async def fake_run(args):
        nonlocal called
        called = True
        return 0, "", ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    await adapter._process_watch_event(_reaction_event())
    assert called is False


def test_native_poll_renders_numbered_options_and_counts(tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    raw = _poll_created_event()
    message = adapter._to_message_event(raw)
    assert message is not None
    assert "Poll: Dinner?" in message.text
    assert "1. Pizza (2 votes)" in message.text
    assert "2. Sushi (1 vote)" in message.text
    assert "Poll ID: POLL-MESSAGE" in message.text
    assert message.raw_message is raw
    assert message.metadata["imsg_event_type"] == "poll"


def test_poll_vote_events_are_disabled_by_default(tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    assert adapter._to_message_event(_poll_vote_event()) is None
    assert adapter._to_message_event(_poll_vote_event(is_group=True)) is None


def test_poll_vote_events_can_be_enabled_for_dm_only(tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True, poll_vote_events={"dm": True, "group": False})
    message = adapter._to_message_event(_poll_vote_event())
    assert message is not None
    assert message.text == 'Poll vote: Yes on "Does this work?"'
    assert adapter._to_message_event(_poll_vote_event(is_group=True)) is None


def test_poll_vote_event_environment_switches(monkeypatch, tmp_path):
    monkeypatch.setenv("IMSG_POLL_VOTES_DM", "true")
    monkeypatch.setenv("IMSG_POLL_VOTES_GROUP", "false")
    monkeypatch.setenv("IMSG_POLL_UNVOTES", "true")
    adapter = make_adapter(tmp_path, allow_all=True)
    assert adapter._to_message_event(_poll_vote_event()) is not None
    assert adapter._to_message_event(
        _poll_vote_event(event="imessage.poll.unvoted")
    ) is not None
    assert adapter._to_message_event(_poll_vote_event(is_group=True)) is None


def test_poll_vote_events_can_be_enabled_for_groups_and_unvotes(tmp_path):
    adapter = make_adapter(
        tmp_path,
        allow_all=True,
        poll_vote_events={"dm": False, "group": True, "include_unvotes": True},
    )
    vote = adapter._to_message_event(_poll_vote_event(is_group=True))
    unvote = adapter._to_message_event(
        _poll_vote_event(is_group=True, event="imessage.poll.unvoted")
    )
    assert vote is not None and vote.text == 'Poll vote: Yes on "Does this work?"'
    assert unvote is not None and unvote.text == 'Poll vote removed: Yes on "Does this work?"'


def test_poll_vote_event_chat_allowlist_limits_enabled_scopes(tmp_path):
    adapter = make_adapter(
        tmp_path,
        allow_all=True,
        poll_vote_events={"dm": True, "group": True, "chats": ["3", "16"]},
    )
    assert adapter._to_message_event(_poll_vote_event()) is not None
    assert adapter._to_message_event(_poll_vote_event(is_group=True)) is not None
    other_dm = _poll_vote_event()
    other_dm["chat_id"] = 28
    assert adapter._to_message_event(other_dm) is None
    other_group = _poll_vote_event(is_group=True)
    other_group["chat_id"] = 31
    assert adapter._to_message_event(other_group) is None


def test_attachment_event_sets_media_type(tmp_path):
    adapter = make_adapter(tmp_path, allowed_chats=["16"])
    message = adapter._to_message_event({
        "id": 99,
        "chat_id": 16,
        "sender": "+1",
        "text": "",
        "attachments": [{"path": "/tmp/pic.jpg", "mime_type": "image/jpeg"}],
    })
    assert message is not None
    assert message.message_type is MessageType.PHOTO
    assert message.media_urls == ["/tmp/pic.jpg"]


def test_attachment_filename_only_is_not_expanded_into_a_path(tmp_path):
    """A bare filename/file field is a display name, not a resolvable path;
    it must never be handed to Hermes as if it were a usable media_url."""
    from hermes_imsg_cli.adapter import _attachment_path

    adapter = make_adapter(tmp_path, allowed_chats=["16"])
    message = adapter._to_message_event({
        "id": 99,
        "chat_id": 16,
        "sender": "+1",
        "text": "",
        "attachments": [{"filename": "IMG_0001.jpg", "file": "IMG_0001.jpg"}],
    })
    # No resolvable path -> omitted from media_urls, same as a missing attachment.
    assert message is None
    assert _attachment_path({"filename": "IMG_0001.jpg"}) is None
    assert _attachment_path({"file": "IMG_0001.jpg"}) is None
    # Converted/original paths are still preferred and used when present.
    assert _attachment_path({
        "filename": "IMG_0001.jpg", "path": "/private/var/tmp/IMG_0001.jpg",
    }) == "/private/var/tmp/IMG_0001.jpg"


def test_unresolved_zero_chat_id_is_dropped_fail_closed(tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    assert adapter._to_message_event({"id": 99, "chat_id": 0, "text": "race"}) is None
    assert adapter._should_process({"id": 99, "chat_id": 0, "text": "race"}) is False


def test_rowid_persistence(tmp_path):
    adapter = make_adapter(tmp_path)
    adapter._last_rowid = 123
    adapter._persist_last_rowid()
    assert json.loads((tmp_path / "imsg_state.json").read_text())["last_rowid"] == 123
    adapter2 = make_adapter(tmp_path)
    assert adapter2._last_rowid == 123


def test_rowid_persistence_is_atomic_with_restrictive_permissions(tmp_path):
    import stat as _stat

    adapter = make_adapter(tmp_path)
    adapter._last_rowid = 456
    adapter._persist_last_rowid()
    state_path = tmp_path / "imsg_state.json"
    mode = _stat.S_IMODE(state_path.stat().st_mode)
    assert mode == 0o600, f"expected owner-only 0600, got {oct(mode)}"
    # No leftover temp file from the atomic write.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".imsg_state.json.")]
    assert leftovers == []
    assert json.loads(state_path.read_text())["last_rowid"] == 456


def test_rowid_persistence_cleans_up_temp_file_on_write_failure(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path)
    adapter._last_rowid = 789

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hermes_imsg_cli.adapter.os.fsync", boom)
    adapter._persist_last_rowid()  # must not raise (best-effort, logged at debug)
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"temp file leaked on failure: {leftovers}"


# ---------------------------------------------------------------------------
# Sending (chat-rowid routing; no GUID guessing on the plain path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_plain_uses_chat_id_not_guid(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    seen = {}

    async def fake_run(args):
        seen["args"] = args
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send("16", "hello")
    assert result.success is True
    assert result.message_id == "OUT"
    assert seen["args"][:3] == ["send", "--chat-id", "16"]
    assert "--chat-guid" not in seen["args"]


@pytest.mark.asyncio
async def test_send_uses_rich_reply_only_for_cached_same_chat_reference(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    adapter._recent_chat_guids["16"] = "CHAT-16"
    adapter._recent_message_guids["16"] = "MSG-16"
    seen_args = []

    async def fake_run(args):
        seen_args.append(args)
        if args[0] == "status":
            return 0, json.dumps({
                "advanced_features": True,
                "v2_ready": True,
                "rpc_methods": ["send.rich"],
                "selectors": {},
            }), ""
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send(
        "16",
        "**reply**",
        reply_to="MSG-16",
        metadata={"chat_guid": "FORGED-OTHER-CHAT"},
    )
    assert result.success is True
    assert seen_args[0] == ["status", "--json"]
    assert seen_args[1][:5] == ["send-rich", "--chat", "CHAT-16", "--text", "reply"]
    assert seen_args[1][seen_args[1].index("--reply-to") + 1] == "MSG-16"
    assert "FORGED-OTHER-CHAT" not in seen_args[1]
    assert result.message_id == "OUT"
    assert adapter._sent_message_guids["OUT"] == "16"


@pytest.mark.asyncio
async def test_send_ignores_cross_chat_reply_reference(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    adapter._recent_chat_guids["16"] = "CHAT-16"
    adapter._recent_message_guids["16"] = "MSG-16"
    calls = []

    async def fake_run(args):
        calls.append(args)
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send(
        "16",
        "reply",
        reply_to="MSG-OTHER",
        metadata={"chat_guid": "FORGED-OTHER-CHAT"},
    )
    assert result.success is True
    assert calls == [
        ["status", "--json"],
        ["send", "--chat-id", "16", "--text", "reply", "--json"],
    ]


@pytest.mark.asyncio
async def test_safe_reply_capability_failure_does_not_retry_plain(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    adapter._recent_chat_guids["16"] = "CHAT-16"
    adapter._recent_message_guids["16"] = "MSG-16"
    calls = []

    async def fake_run(args):
        calls.append(args)
        return 0, json.dumps({
            "advanced_features": True,
            "v2_ready": True,
            "rpc_methods": [],
            "selectors": {},
        }), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send("16", "reply", reply_to="MSG-16")
    assert result.success is False
    assert "send.rich" in result.error
    assert calls == [["status", "--json"]]


@pytest.mark.asyncio
async def test_formatted_plain_send_uses_rowid_rpc_without_cli_retry(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    rpc_calls = []

    async def fail_cli(args):
        raise AssertionError(f"plain CLI must not run: {args}")

    async def fake_rpc(request):
        rpc_calls.append(request)
        return 0, json.dumps({
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"guid": "FORMATTED"},
        }), ""

    monkeypatch.setattr(adapter, "_run_imsg", fail_cli)
    monkeypatch.setattr(adapter, "_run_imsg_rpc", fake_rpc)
    result = await adapter.send("16", "**bold**")
    assert result.success is True
    assert result.message_id == "FORMATTED"
    assert rpc_calls[0]["params"]["chat_id"] == 16
    assert rpc_calls[0]["params"]["text"] == "bold"
    assert rpc_calls[0]["params"]["formatting"][0]["styles"] == ["bold"]


@pytest.mark.asyncio
async def test_reply_attachment_uses_same_chat_rich_send(monkeypatch, tmp_path):
    attachment = tmp_path / "reply.txt"
    attachment.write_text("synthetic")
    adapter = make_adapter(tmp_path, binary="imsg")
    adapter._recent_chat_guids["16"] = "CHAT-16"
    adapter._recent_message_guids["16"] = "MSG-16"
    calls = []

    async def fake_run(args):
        calls.append(args)
        if args[0] == "status":
            return 0, json.dumps({
                "advanced_features": True,
                "v2_ready": True,
                "rpc_methods": ["send.rich"],
                "selectors": {},
            }), ""
        return 0, json.dumps({"guid": "ATTACHMENT-REPLY"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send_document(
        "16", str(attachment), caption="file", reply_to="MSG-16"
    )
    assert result.success is True
    assert calls[1][0] == "send-rich"
    assert calls[1][calls[1].index("--file") + 1] == str(attachment)


def test_adapter_watch_args_enable_reaction_rows(tmp_path):
    adapter = make_adapter(tmp_path)
    assert "--reactions" in adapter._build_watch_args()


@pytest.mark.asyncio
async def test_send_plain_fails_closed_on_empty_stdout(monkeypatch, tmp_path):
    """A zero exit code alone must never be treated as a successful send."""
    adapter = make_adapter(tmp_path, binary="imsg")

    async def fake_run(args):
        return 0, "", ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send("16", "hello")
    assert result.success is False
    assert "no JSON confirmation" in result.error


@pytest.mark.asyncio
async def test_send_plain_fails_closed_on_malformed_json_stdout(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")

    async def fake_run(args):
        return 0, "not-json-at-all", ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send("16", "hello")
    assert result.success is False


@pytest.mark.asyncio
async def test_send_plain_fails_closed_on_non_object_json_stdout(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")

    async def fake_run(args):
        return 0, json.dumps(["not", "an", "object"]), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send("16", "hello")
    assert result.success is False


@pytest.mark.asyncio
async def test_send_plain_converts_subprocess_exception_to_failure(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")

    async def fake_run(args):
        raise TimeoutError("recipient +12025550100 timed out")

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send("16", "hello")
    assert result.success is False
    assert "+12025550100" not in result.error
    assert "timed out" in result.error


# ---------------------------------------------------------------------------
# Outbound attachment safety: regular files only, no symlinks/specials
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_document_rejects_symlink_attachment(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    real_file = tmp_path / "real.jpg"
    real_file.write_bytes(b"data")
    link = tmp_path / "link.jpg"
    link.symlink_to(real_file)
    called = False

    async def fake_run(args):
        nonlocal called
        called = True
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send_document("16", str(link))
    assert result.success is False
    assert "symlink" in result.error
    assert called is False


@pytest.mark.asyncio
async def test_send_document_rejects_directory_attachment(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    directory = tmp_path / "adir"
    directory.mkdir()
    called = False

    async def fake_run(args):
        nonlocal called
        called = True
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send_document("16", str(directory))
    assert result.success is False
    assert "regular file" in result.error
    assert called is False


@pytest.mark.asyncio
async def test_send_document_rejects_fifo_attachment(monkeypatch, tmp_path):
    if sys.platform not in ("darwin", "linux"):
        pytest.skip("mkfifo requires a POSIX platform")
    adapter = make_adapter(tmp_path, binary="imsg")
    fifo_path = tmp_path / "afifo"
    os.mkfifo(fifo_path)
    called = False

    async def fake_run(args):
        nonlocal called
        called = True
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send_document("16", str(fifo_path))
    assert result.success is False
    assert "regular file" in result.error
    assert called is False


@pytest.mark.asyncio
async def test_send_document_rejects_missing_attachment(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")

    async def fake_run(args):
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send_document("16", str(tmp_path / "nonexistent.jpg"))
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_send_document_accepts_regular_file(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    real_file = tmp_path / "real.jpg"
    real_file.write_bytes(b"data")
    seen = {}

    async def fake_run(args):
        seen["args"] = args
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send_document("16", str(real_file))
    assert result.success is True
    assert seen["args"][seen["args"].index("--file") + 1] == str(real_file)


@pytest.mark.asyncio
async def test_send_imsg_direct_uses_adapter(monkeypatch, tmp_path):
    cli_calls = []
    rpc_calls = []

    async def fake_run(self, args):
        cli_calls.append(args)
        return 0, json.dumps({
            "advanced_features": True,
            "v2_ready": True,
            "rpc_methods": ["send"],
            "selectors": {},
        }), ""

    async def fake_rpc(self, request):
        rpc_calls.append(request)
        return 0, json.dumps({
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"guid": "OUT"},
        }), ""

    monkeypatch.setattr(ImsgAdapter, "_run_imsg", fake_run)
    monkeypatch.setattr(ImsgAdapter, "_run_imsg_rpc", fake_rpc)
    result = await send_imsg_direct(
        {"binary": "imsg", "state_file": str(tmp_path / "state.json")}, "16", "hello"
    )
    assert result["success"] is True
    assert cli_calls == [["status", "--json"]]
    assert rpc_calls[0]["method"] == "send"
    assert rpc_calls[0]["params"]["chat_id"] == 16
    assert rpc_calls[0]["params"]["transport"] == "bridge"


def test_check_requirements_non_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert check_imsg_requirements("imsg") is False


@pytest.mark.asyncio
async def test_mark_read_uses_chat_id(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    seen = {}

    async def fake_run(args):
        seen["args"] = args
        return 0, json.dumps({"success": True, "marked_as_read": True}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    ok = await adapter.mark_read("3")
    assert ok is True
    assert seen["args"][:3] == ["read", "--chat-id", "3"]


@pytest.mark.asyncio
async def test_adapter_read_surfaces_parse_ndjson(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    responses = [
        (0, '{"id":1}\n{"id":2}\n', ""),
        (0, '{"id":9,"chat_id":16}\n', ""),
        (0, '{"id":10,"text":"pizza"}\n', ""),
    ]

    async def fake_run(args):
        return responses.pop(0)

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    assert [c["id"] for c in await adapter.list_chats(limit=2)] == [1, 2]
    assert (await adapter.get_history("16", limit=1))[0]["chat_id"] == 16
    assert (await adapter.search_messages("pizza", limit=1))[0]["text"] == "pizza"


@pytest.mark.asyncio
async def test_unknown_reaction_is_rejected_without_subprocess(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    called = False

    async def fake_run(args):
        nonlocal called
        called = True
        return 0, "{}", ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.add_reaction("16", "🔥")
    assert "standard" in result["error"]
    assert called is False


@pytest.mark.asyncio
async def test_inbound_marks_read_when_enabled(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg", allowed_handles=["+12025550101"])
    marked = []

    async def fake_mark_read(chat_id):
        marked.append(chat_id)
        return True

    async def fake_handle(message_event):
        return None

    monkeypatch.setattr(adapter, "mark_read", fake_mark_read)
    monkeypatch.setattr(adapter, "handle_message", fake_handle)
    await adapter._process_watch_event({
        "id": 100, "chat_id": 3, "sender": "+12025550101", "text": "hi",
    })
    await asyncio.sleep(0)
    assert marked == ["3"]


@pytest.mark.asyncio
async def test_inbound_skips_read_when_disabled(monkeypatch, tmp_path):
    adapter = make_adapter(
        tmp_path, binary="imsg", allowed_handles=["+12025550101"], send_read_receipts=False
    )
    marked = []

    async def fake_mark_read(chat_id):
        marked.append(chat_id)
        return True

    async def fake_handle(message_event):
        return None

    monkeypatch.setattr(adapter, "mark_read", fake_mark_read)
    monkeypatch.setattr(adapter, "handle_message", fake_handle)
    await adapter._process_watch_event({
        "id": 101, "chat_id": 3, "sender": "+12025550101", "text": "hi",
    })
    await asyncio.sleep(0)
    assert marked == []


def test_read_receipts_default_on(tmp_path):
    adapter = make_adapter(tmp_path)
    assert adapter.send_read_receipts is True
    assert adapter.auto_launch is True
    assert adapter.command_timeout == 30.0


@pytest.mark.asyncio
async def test_bounded_command_timeout(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, command_timeout=0.001)

    async def slow(binary, args):
        await asyncio.sleep(1)
        return 0, "", ""

    monkeypatch.setattr("hermes_imsg_cli.adapter.cli.run_imsg", slow)
    with pytest.raises(TimeoutError, match="timed out"):
        await adapter._run_imsg(["status", "--json"])


# ---------------------------------------------------------------------------
# Native polls (all subprocesses mocked; no Messages mutation)
# ---------------------------------------------------------------------------

def _poll_status():
    return json.dumps({
        "advanced_features": True,
        "v2_ready": True,
        "selectors": {"pollPayloadMessage": True, "pollVoteMessage": True},
        "rpc_methods": ["poll.send", "poll.vote", "polls.unvote"],
    })


@pytest.mark.asyncio
async def test_adapter_poll_send_binds_inbound_operation_to_exact_rowid(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    calls = []

    async def fake_run(args):
        calls.append(args)
        if args[0] == "status":
            return 0, _poll_status(), ""
        return 0, json.dumps({"messageGuid": "POLL"}), ""

    async def fake_rpc(request):
        calls.append(request)
        return 0, json.dumps({
            "jsonrpc": "2.0", "id": request["id"],
            "result": {"messageGuid": "POLL"},
        }), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    monkeypatch.setattr(adapter, "_run_imsg_rpc", fake_rpc)
    result = await adapter.send_poll("16", "Dinner?", ["Pizza", "Sushi"], reply_to="PARENT")
    assert result == {
        "success": True, "action": "send", "chat_id": "16", "chat_guid": None,
        "result": {"messageGuid": "POLL"},
    }
    assert calls[1]["method"] == "poll.send"
    assert calls[1]["params"]["chat_id"] == 16
    assert calls[1]["params"]["reply_to"] == "PARENT"


@pytest.mark.asyncio
async def test_adapter_poll_vote_and_selective_unvote_dispatch(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")
    calls = []

    async def fake_run(args):
        calls.append(args)
        if args[0] == "status":
            return 0, _poll_status(), ""
        return 0, json.dumps({"messageGuid": args[1].upper()}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    vote = await adapter.vote_poll("16", "POLL", option_index=2)
    unvote = await adapter.unvote_poll("16", "POLL", option_id="OPT")
    assert vote["success"] is True and vote["action"] == "vote"
    assert unvote["success"] is True and unvote["action"] == "unvote"
    assert calls[1][1] == "vote" and calls[1][-3:] == ["--option-index", "2", "--json"]
    assert calls[3][1] == "unvote" and calls[3][-3:] == ["--option-id", "OPT", "--json"]


@pytest.mark.asyncio
async def test_adapter_poll_error_is_structured_redacted_and_never_false_success(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, binary="imsg")

    async def fake_run(args):
        if args[0] == "status":
            return 0, _poll_status(), ""
        return 5, "", "failed for +12025550100"

    async def fake_rpc(request):
        return 5, "", "failed for +141****1212"

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    monkeypatch.setattr(adapter, "_run_imsg_rpc", fake_rpc)
    result = await adapter.send_poll("16", "Q", ["a", "b"])
    assert result["success"] is False
    assert result["action"] == "send"
    assert result["exit_code"] == 5
    assert "+12025550100" not in result["error"]


@pytest.mark.asyncio
async def test_poll_tool_dispatches_explicit_action_and_rejects_bare_handle(monkeypatch):
    calls = []

    class FakeClient:
        async def group(self, chat_id):
            return {"id": chat_id, "guid": "CHAT-16", "is_group": False}

        async def chats(self, *, limit=20, unread_only=False):
            return [{"id": 16, "guid": "iMessage;+;g", "is_group": True}]

        async def history(self, chat_id, **kwargs):
            return [{"guid": "P", "is_from_me": False}]

        async def send_poll(self, chat_id, question, options, **kwargs):
            calls.append(("send", chat_id, question, options, kwargs))
            return {"messageGuid": "P"}

        async def vote_poll(self, chat_id, poll_guid, **kwargs):
            calls.append(("vote", chat_id, poll_guid, kwargs))
            return {"messageGuid": "V"}

        async def unvote_poll(self, chat_id, poll_guid, **kwargs):
            calls.append(("unvote", chat_id, poll_guid, kwargs))
            return {"messageGuid": "U"}

    monkeypatch.setattr("hermes_imsg_cli.actions._action_tool_client", lambda: FakeClient())
    result = await handle_imsg_poll_tool({
        "action": "send", "chat_id": "16", "question": "Q", "options": ["a", "b"]
    })
    assert result["success"] is True and calls[0][0:2] == ("send", "16")
    result = await handle_imsg_poll_tool({
        "action": "vote", "chat_guid": "iMessage;+;g", "poll_guid": "P", "option": "a"
    })
    assert result["success"] is True and calls[1][3]["chat_guid"] == "iMessage;+;g"
    result = await handle_imsg_poll_tool({
        "action": "unvote", "chat_id": "16", "poll_guid": "P", "option_id": "O"
    })
    assert result["success"] is True and calls[2][0] == "unvote"
    denied = await handle_imsg_poll_tool({
        "action": "send", "chat_id": "+12025550100", "question": "Q", "options": ["a", "b"]
    })
    assert denied["success"] is False
    assert "positive integer" in denied["error"]


@pytest.mark.asyncio
async def test_poll_tool_requires_explicit_known_action():
    result = await handle_imsg_poll_tool({"action": "delete", "chat_id": "16"})
    assert result["success"] is False
    assert "send, vote, or unvote" in result["error"]


# ---------------------------------------------------------------------------
# __init__ wiring: rules, fallback_action, participant_validation
# ---------------------------------------------------------------------------

def test_adapter_loads_rules_from_extra(tmp_path):
    adapter = make_adapter(tmp_path, rules=[
        {"name": "owners", "context": "dm", "senders": ["+111"], "action": "allow"}
    ])
    assert len(adapter.rules) == 1
    assert adapter.rules[0].name == "owners"
    assert adapter.rules[0].context == "dm"


def test_adapter_defaults_when_no_rules(tmp_path):
    adapter = make_adapter(tmp_path)
    assert adapter.rules == []
    assert adapter.fallback_action == "silent_drop"
    assert adapter.participant_validation == "fast"
    assert adapter.participant_cache_ttl == 300


def test_adapter_custom_fallback_and_validation(tmp_path):
    adapter = make_adapter(
        tmp_path,
        fallback_action="deny",
        participant_validation="strict",
        participant_cache_ttl=60,
    )
    assert adapter.fallback_action == "deny"
    assert adapter.participant_validation == "strict"
    assert adapter.participant_cache_ttl == 60


def test_adapter_invalid_fallback_defaults_to_silent_drop(tmp_path):
    adapter = make_adapter(tmp_path, fallback_action="explode")
    assert adapter.fallback_action == "silent_drop"


def test_adapter_invalid_validation_defaults_to_fast(tmp_path):
    adapter = make_adapter(tmp_path, participant_validation="turbo")
    assert adapter.participant_validation == "fast"


def test_adapter_participant_cache_starts_empty(tmp_path):
    adapter = make_adapter(tmp_path)
    assert adapter._participant_cache == {}


# ---------------------------------------------------------------------------
# _get_participants() async + fast/strict + TTL cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_participants_fast_from_event(tmp_path):
    adapter = make_adapter(tmp_path, participant_validation="fast")
    event = {"chat_id": 16, "participants": ["+111", "+222"]}
    result = await adapter._get_participants("16", event)
    assert result == {"+111", "+222"}


@pytest.mark.asyncio
async def test_get_participants_fast_validates_entry_types(tmp_path):
    """Non-string entries (dict/list/int/bool/None) cannot be genuine
    participant handles; they must be dropped rather than silently
    stringified into something that could match a rule's handle set."""
    adapter = make_adapter(tmp_path, participant_validation="fast")
    event = {
        "chat_id": 16,
        "participants": [
            "+111",
            42,
            True,
            False,
            None,
            {"handle": "+999"},
            ["+888"],
            "  ",
        ],
    }
    result = await adapter._get_participants("16", event)
    assert result == {"+111"}


@pytest.mark.asyncio
async def test_get_participants_fast_returns_empty_when_missing(tmp_path):
    adapter = make_adapter(tmp_path, participant_validation="fast")
    result = await adapter._get_participants("16", {"chat_id": 16})
    assert result == set()


@pytest.mark.asyncio
async def test_get_participants_strict_calls_cli(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, participant_validation="strict")
    payload = json.dumps({"participants": ["+111", "+222"], "is_group": True})

    async def fake_run(args):
        assert args[:3] == ["group", "--chat-id", "16"]
        return 0, payload, ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter._get_participants("16", {})
    assert result == {"+111", "+222"}


@pytest.mark.asyncio
async def test_get_participants_strict_uses_cache(monkeypatch, tmp_path):
    import time as _time
    adapter = make_adapter(tmp_path, participant_validation="strict", participant_cache_ttl=300)
    handles = {"+111"}
    adapter._participant_cache["16"] = (handles, _time.time() + 300)
    call_count = {"n": 0}

    async def fake_run(args):
        call_count["n"] += 1
        return 0, json.dumps({"participants": ["+999"]}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter._get_participants("16", {})
    assert result == handles
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_get_participants_strict_refreshes_expired_cache(monkeypatch, tmp_path):
    import time as _time
    adapter = make_adapter(tmp_path, participant_validation="strict", participant_cache_ttl=300)
    adapter._participant_cache["16"] = ({"+stale"}, _time.time() - 1)

    async def fake_run(args):
        return 0, json.dumps({"participants": ["+fresh"]}), ""

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter._get_participants("16", {})
    assert result == {"+fresh"}


@pytest.mark.asyncio
async def test_get_participants_strict_cli_error_returns_empty(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, participant_validation="strict")

    async def fake_run(args):
        return 1, "", "error"

    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter._get_participants("16", {})
    assert result == set()


# ---------------------------------------------------------------------------
# _evaluate_rule() pure sync logic
# ---------------------------------------------------------------------------

def make_rule(**kwargs) -> ImsgRule:
    kwargs.setdefault("name", "test_rule")
    kwargs.setdefault("action", "allow")
    return ImsgRule(**kwargs)


def test_evaluate_rule_dm_context_matches_dm(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="dm")
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "sender": "+1"}, set()) is True


def test_evaluate_rule_dm_context_rejects_group(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="dm")
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "is_group": True, "sender": "+1"}, set()) is False


def test_evaluate_rule_group_context_matches_group(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group")
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "is_group": True, "sender": "+1"}, set()) is True


def test_evaluate_rule_group_context_rejects_dm(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group")
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "sender": "+1"}, set()) is False


def test_evaluate_rule_any_context_matches_both(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="any")
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "sender": "+1"}, set()) is True
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "is_group": True, "sender": "+1"}, set()) is True


def test_evaluate_rule_sender_match(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(senders={"+111"})
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "sender": "+111"}, set()) is True


def test_evaluate_rule_sender_no_match(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(senders={"+111"})
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "sender": "+999"}, set()) is False


def test_evaluate_rule_sender_any(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(senders=None)
    assert adapter._evaluate_rule(rule, {"chat_id": 1, "sender": "+999"}, set()) is True


def test_evaluate_rule_chat_match(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(chats={"16"})
    assert adapter._evaluate_rule(rule, {"chat_id": 16, "sender": "+1"}, set()) is True


def test_evaluate_rule_chat_no_match(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(chats={"16"})
    assert adapter._evaluate_rule(rule, {"chat_id": 99, "sender": "+1"}, set()) is False


def test_evaluate_rule_require_participants_any_of_pass(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group", require_participants={"any_of": ["+eve"]})
    event = {"chat_id": 1, "is_group": True, "sender": "+bob"}
    assert adapter._evaluate_rule(rule, event, {"+eve", "+bob"}) is True


def test_evaluate_rule_require_participants_any_of_fail(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group", require_participants={"any_of": ["+eve"]})
    event = {"chat_id": 1, "is_group": True, "sender": "+bob"}
    assert adapter._evaluate_rule(rule, event, {"+bob", "+mallory"}) is False


def test_evaluate_rule_require_participants_all_of_pass(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group", require_participants={"all_of": ["+alice", "+carol"]})
    event = {"chat_id": 1, "is_group": True, "sender": "+bob"}
    assert adapter._evaluate_rule(rule, event, {"+alice", "+carol", "+bob"}) is True


def test_evaluate_rule_require_participants_all_of_fail(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group", require_participants={"all_of": ["+alice", "+carol"]})
    event = {"chat_id": 1, "is_group": True, "sender": "+bob"}
    assert adapter._evaluate_rule(rule, event, {"+alice", "+bob"}) is False


def test_evaluate_rule_participants_ignored_for_dm(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="dm", require_participants={"any_of": ["+must_be_here"]})
    event = {"chat_id": 1, "is_group": False, "sender": "+alice"}
    assert adapter._evaluate_rule(rule, event, set()) is True


def test_evaluate_rule_require_mention_pass(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group", require_mention=True)
    event = {"chat_id": 1, "is_group": True, "sender": "+1", "text": "Gordie: hey"}
    assert adapter._evaluate_rule(rule, event, set()) is True


def test_evaluate_rule_require_mention_fail(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="group", require_mention=True)
    event = {"chat_id": 1, "is_group": True, "sender": "+1", "text": "just chatting"}
    assert adapter._evaluate_rule(rule, event, set()) is False


def test_evaluate_rule_require_mention_ignored_for_dm(tmp_path):
    adapter = make_adapter(tmp_path)
    rule = make_rule(context="dm", require_mention=True)
    event = {"chat_id": 1, "is_group": False, "sender": "+1", "text": "no mention"}
    assert adapter._evaluate_rule(rule, event, set()) is True


# ---------------------------------------------------------------------------
# _should_process_async() + legacy synthesis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_should_process_async_rules_engine_allow(tmp_path):
    adapter = make_adapter(tmp_path, rules=[
        {"name": "owners", "context": "dm", "senders": ["+111"], "action": "allow"}
    ])
    event = {"chat_id": 1, "sender": "+111", "text": "hi"}
    assert await adapter._should_process_async(event) is True


@pytest.mark.asyncio
async def test_should_process_async_rules_engine_silent_drop(tmp_path):
    adapter = make_adapter(tmp_path, rules=[
        {"name": "owners", "context": "dm", "senders": ["+111"], "action": "allow"}
    ], fallback_action="silent_drop")
    event = {"chat_id": 1, "sender": "+999", "text": "hi"}
    assert await adapter._should_process_async(event) is False


@pytest.mark.asyncio
async def test_should_process_async_self_message_always_dropped(tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    event = {"chat_id": 1, "sender": "+1", "is_from_me": True, "text": "hi"}
    assert await adapter._should_process_async(event) is False


@pytest.mark.asyncio
async def test_should_process_async_legacy_allowed_handles(tmp_path):
    adapter = make_adapter(tmp_path, allowed_handles=["+111"])
    event = {"chat_id": 1, "sender": "+111", "text": "hi"}
    assert await adapter._should_process_async(event) is True


@pytest.mark.asyncio
async def test_should_process_async_legacy_unknown_sender_drops(tmp_path):
    adapter = make_adapter(tmp_path, allowed_handles=["+111"])
    event = {"chat_id": 1, "sender": "+999", "text": "hi"}
    assert await adapter._should_process_async(event) is False


@pytest.mark.asyncio
async def test_should_process_async_participant_gate(tmp_path):
    adapter = make_adapter(tmp_path, participant_validation="fast", rules=[{
        "name": "bob_family",
        "context": "group",
        "senders": ["+bob"],
        "require_participants": {"any_of": ["+alice"]},
        "action": "allow",
    }])
    event_ok = {"chat_id": 16, "is_group": True, "sender": "+bob",
                "text": "hi", "participants": ["+alice", "+bob"]}
    assert await adapter._should_process_async(event_ok) is True
    event_bad = {"chat_id": 17, "is_group": True, "sender": "+bob",
                 "text": "hi", "participants": ["+bob", "+mallory"]}
    assert await adapter._should_process_async(event_bad) is False


@pytest.mark.asyncio
async def test_should_process_async_deny_sends_message(monkeypatch, tmp_path):
    sent = []
    adapter = make_adapter(tmp_path, rules=[{"name": "deny_all", "action": "deny"}])

    async def fake_send(chat_id, content, **kw):
        sent.append(content)
        return SendResult(success=True)

    monkeypatch.setattr(adapter, "send", fake_send)
    event = {"chat_id": 1, "sender": "+999", "text": "hi"}
    result = await adapter._should_process_async(event)
    assert result is False
    await asyncio.sleep(0)
    assert sent


def test_synthesise_legacy_rules_allow_all(tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    rules = adapter._synthesise_legacy_rules()
    assert len(rules) == 1
    assert rules[0].action == "allow"
    assert rules[0].senders is None


def test_synthesise_legacy_rules_handles(tmp_path):
    adapter = make_adapter(tmp_path, allowed_handles=["+111"])
    rules = adapter._synthesise_legacy_rules()
    dm_rule = next(r for r in rules if r.context == "dm")
    assert dm_rule.senders is not None
    assert "+111" in dm_rule.senders


def test_synthesise_legacy_rules_group_chats(tmp_path):
    adapter = make_adapter(tmp_path, allowed_chats=["16", "8"])
    rules = adapter._synthesise_legacy_rules()
    group_rule = next(r for r in rules if r.chats is not None)
    assert group_rule.chats == {"16", "8"}


# ---------------------------------------------------------------------------
# Reliability: background-task retention + watch stderr draining
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spawn_bg_retains_then_clears_task(tmp_path):
    adapter = make_adapter(tmp_path)
    ran = []

    async def work():
        ran.append(True)

    task = adapter._spawn_bg(work())
    # Retained while pending so the loop can't GC it mid-flight.
    assert task in adapter._background_tasks
    await asyncio.sleep(0)
    await task
    await asyncio.sleep(0)
    # Dropped from the set once complete (done-callback).
    assert task not in adapter._background_tasks
    assert ran == [True]


@pytest.mark.asyncio
async def test_spawn_bg_swallows_and_records_exception(tmp_path):
    adapter = make_adapter(tmp_path)

    async def boom():
        raise RuntimeError("boom")

    task = adapter._spawn_bg(boom())
    await asyncio.sleep(0)
    # Exception is observed by the done-callback (no "never retrieved" warning)
    # and does not propagate out of the fire-and-forget path.
    assert task.done()
    assert task.exception() is not None
    await asyncio.sleep(0)  # let the done-callback run
    assert task not in adapter._background_tasks


class _FakeStderr:
    """Minimal async-iterable stand-in for ``proc.stderr``."""

    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, stderr_lines):
        self.stderr = _FakeStderr(stderr_lines)


@pytest.mark.asyncio
async def test_drain_watch_stderr_captures_redacted_tail(tmp_path):
    adapter = make_adapter(tmp_path)
    proc = _FakeProc([
        b"watch: connected\n",
        b"warning from +12025550109 failed\n",
        b"\n",
        b"bye\n",
    ])
    await adapter._drain_watch_stderr(proc)
    tail = list(adapter._watch_stderr_tail)
    # Blank line dropped; phone number redacted; order preserved.
    assert tail == ["watch: connected", "warning from [REDACTED] failed", "bye"]
    assert "+12025550109" not in adapter._stderr_tail_suffix()


@pytest.mark.asyncio
async def test_drain_watch_stderr_bounded(tmp_path):
    adapter = make_adapter(tmp_path)
    proc = _FakeProc([f"line {i}\n".encode() for i in range(50)])
    await adapter._drain_watch_stderr(proc)
    # deque(maxlen=20) keeps only the most recent 20 lines.
    assert len(adapter._watch_stderr_tail) == 20
    assert list(adapter._watch_stderr_tail)[-1] == "line 49"


def test_stderr_tail_suffix_empty(tmp_path):
    adapter = make_adapter(tmp_path)
    assert adapter._stderr_tail_suffix() == ""


# ---------------------------------------------------------------------------
# Watch line handling never logs raw content (sentinel tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_watch_line_never_logs_raw_content_on_parse_failure(tmp_path, caplog):
    adapter = make_adapter(tmp_path, allow_all=True)
    sensitive = (
        '{"bad json missing closing brace" "sender": "+12025550100", '
        '"text": "SENTINEL-SECRET-PAYLOAD-DO-NOT-LOG"'
    )
    with caplog.at_level("DEBUG", logger="hermes_imsg_cli.adapter"):
        await adapter._handle_watch_line(sensitive)
    assert "SENTINEL-SECRET-PAYLOAD-DO-NOT-LOG" not in caplog.text
    assert "+12025550100" not in caplog.text
    assert "chars)" in caplog.text  # only the redacted length metadata is logged


@pytest.mark.asyncio
async def test_rule_decision_log_omits_chat_rowid_and_redacts_rule_name(tmp_path, caplog):
    adapter = make_adapter(tmp_path)
    sensitive_rule_name = "confidential-contact-label +12025550100"
    with caplog.at_level("DEBUG", logger="hermes_imsg_cli.adapter"):
        allowed = await adapter._execute_rule_action(
            ImsgRule(name=sensitive_rule_name, action="silent_drop"), {}, "424242"
        )
    assert allowed is False
    assert sensitive_rule_name not in caplog.text
    assert "confidential-contact-label" not in caplog.text
    assert "+12025550100" not in caplog.text
    assert "424242" not in caplog.text


def test_invalid_mention_pattern_log_omits_configured_value(caplog):
    sensitive_pattern = "confidential-contact-label["
    with caplog.at_level("WARNING", logger="hermes_imsg_cli.adapter"):
        assert ImsgAdapter._compile_mention_patterns([sensitive_pattern]) == []
    assert sensitive_pattern not in caplog.text
    assert "Invalid mention pattern" in caplog.text


@pytest.mark.asyncio
async def test_mark_read_failure_log_redacts_exception_and_omits_chat_rowid(
    monkeypatch, tmp_path, caplog
):
    adapter = make_adapter(tmp_path)

    async def fail(_args):
        raise OSError("failed at /Users/exampleuser/chat.db for +12025550100")

    monkeypatch.setattr(adapter, "_run_imsg", fail)
    with caplog.at_level("DEBUG", logger="hermes_imsg_cli.adapter"):
        assert await adapter.mark_read("424242") is False
    assert "/Users/exampleuser" not in caplog.text
    assert "+12025550100" not in caplog.text
    assert "424242" not in caplog.text
    assert "[REDACTED_PATH]" in caplog.text


@pytest.mark.asyncio
async def test_handle_watch_line_dispatches_valid_json(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    seen = []

    async def fake_process(event):
        seen.append(event)

    monkeypatch.setattr(adapter, "_process_watch_event", fake_process)
    await adapter._handle_watch_line('{"chat_id": 16, "text": "hi"}')
    assert seen == [{"chat_id": 16, "text": "hi"}]


@pytest.mark.asyncio
async def test_handle_watch_line_ignores_non_object_json(monkeypatch, tmp_path):
    adapter = make_adapter(tmp_path, allow_all=True)
    called = False

    async def fake_process(event):
        nonlocal called
        called = True

    monkeypatch.setattr(adapter, "_process_watch_event", fake_process)
    await adapter._handle_watch_line('[1, 2, 3]')
    assert called is False


@pytest.mark.asyncio
async def test_state_file_path_is_redacted_in_persist_failure_logs(monkeypatch, tmp_path, caplog):
    """Home-directory-shaped state paths must never appear raw in logs."""
    home_like = tmp_path / "Users" / "exampleuser" / ".hermes"
    home_like.mkdir(parents=True)
    adapter = make_adapter(tmp_path, state_file=str(home_like / "imsg_state.json"))

    def boom_mkstemp(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr("hermes_imsg_cli.adapter.tempfile.mkstemp", boom_mkstemp)
    with caplog.at_level("DEBUG", logger="hermes_imsg_cli.adapter"):
        adapter._last_rowid = 1
        adapter._persist_last_rowid()
    assert "/Users/exampleuser" not in caplog.text
    assert "[REDACTED_PATH]" in caplog.text
