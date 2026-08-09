"""Standalone/direct send tests.

The pure command-composition + target-normalization tests need no Hermes
checkout.  The ``send_imsg_direct`` / ``_standalone_send`` composition tests
require ``gateway`` and are skipped when it is unavailable.
"""

import json
import sys

import pytest

from hermes_imsg_cli import cli
from hermes_imsg_cli.config import normalize_imsg_target, parse_imsg_target


# ---------------------------------------------------------------------------
# Pure CLI argument composition
# ---------------------------------------------------------------------------

def test_build_plain_send_uses_chat_id_for_rowid():
    args = cli.build_plain_send_args("16", "hello")
    assert args[:3] == ["send", "--chat-id", "16"]
    assert "--to" not in args
    assert "--json" in args


def test_build_plain_send_uses_to_for_e164():
    args = cli.build_plain_send_args("+12025550105", "hi", is_phone=True)
    assert args[:3] == ["send", "--to", "+12025550105"]
    assert "--chat-id" not in args


def test_build_plain_send_uses_to_for_leading_plus_even_without_flag():
    # Safety: a leading '+' forces --to even if the caller forgot is_phone.
    args = cli.build_plain_send_args("+12025550105", "hi")
    assert args[:3] == ["send", "--to", "+12025550105"]


def test_build_plain_send_uses_to_for_email_handle():
    args = cli.build_plain_send_args("user@example.com", "hi")
    assert args[:3] == ["send", "--to", "user@example.com"]


def test_build_plain_send_attaches_files():
    args = cli.build_plain_send_args("16", "cap", media_files=[("/tmp/x.jpg", False)])
    assert "--file" in args
    assert args[args.index("--file") + 1] == "/tmp/x.jpg"


def test_build_rich_reply_args():
    args = cli.build_rich_reply_args("chatGUID", "msgGUID", "yo")
    assert args[0] == "send-rich"
    assert args[args.index("--chat") + 1] == "chatGUID"
    assert args[args.index("--reply-to") + 1] == "msgGUID"


def test_build_watch_args_includes_since_rowid():
    args = cli.build_watch_args("250ms", 42)
    assert args[0] == "watch"
    assert "--json" in args and "--attachments" in args
    assert args[args.index("--since-rowid") + 1] == "42"


def test_build_watch_args_omits_since_rowid_when_none():
    args = cli.build_watch_args("250ms", None)
    assert "--since-rowid" not in args


def test_build_read_typing_chats_group_args():
    assert cli.build_read_args("3")[:3] == ["read", "--chat-id", "3"]
    assert cli.build_typing_args("3")[:3] == ["typing", "--chat-id", "3"]
    assert cli.build_typing_args("3", 5)[-3:] == ["--duration", "5s", "--json"]
    assert cli.build_chats_args(3)[:3] == ["chats", "--limit", "3"]
    assert cli.build_group_args("3")[:3] == ["group", "--chat-id", "3"]


def test_extract_message_id():
    assert cli.extract_message_id(json.dumps({"guid": "OUT"})) == "OUT"
    assert cli.extract_message_id(json.dumps({"rowid": 5})) == "5"
    assert cli.extract_message_id("not json") is None
    assert cli.extract_message_id(json.dumps({})) is None


# ---------------------------------------------------------------------------
# E.164 target normalization (works around core's '+'-stripping gap)
# ---------------------------------------------------------------------------

def test_normalize_rowid_stays_chat_id():
    assert normalize_imsg_target("16") == ("16", False)
    assert normalize_imsg_target("9") == ("9", False)


def test_normalize_explicit_e164_is_phone():
    assert normalize_imsg_target("+12025550105") == ("+12025550105", True)


def test_normalize_email_handle_is_phone():
    assert normalize_imsg_target("gordie@example.com") == ("gordie@example.com", True)


def test_normalize_bare_11_digit_gets_plus():
    # 11-digit US number without '+': re-hydrate to E.164.
    assert normalize_imsg_target("12025550105") == ("+12025550105", True)


def test_normalize_bare_10_digit_gets_country_code():
    # 10-digit US number: prepend default country code.
    assert normalize_imsg_target("2025550105", default_country_code="1") == ("+12025550105", True)


def test_normalize_respects_phone_min_digits():
    # A short numeric under the threshold is treated as a chat rowid.
    assert normalize_imsg_target("12345", phone_min_digits=10) == ("12345", False)
    # Raising the threshold keeps a 10-digit value as a rowid.
    assert normalize_imsg_target("2025550105", phone_min_digits=15) == ("2025550105", False)


def test_normalize_empty():
    assert normalize_imsg_target("") == ("", False)
    assert normalize_imsg_target(None) == ("", False)


@pytest.mark.parametrize(
    ("raw", "kind", "value", "service", "normalized"),
    [
        ("chat_id:16", "chat_id", "16", None, "chat_id:16"),
        ("chat_guid:iMessage;+;group", "chat_guid", "iMessage;+;group", None, "chat_guid:iMessage;+;group"),
        ("chat_identifier:group", "chat_identifier", "group", None, "chat_identifier:group"),
        ("imessage:+12025550105", "recipient", "+12025550105", "imessage", "imessage:+12025550105"),
        ("sms:+12025550105", "recipient", "+12025550105", "sms", "sms:+12025550105"),
        ("auto:user@example.com", "recipient", "user@example.com", "auto", "auto:user@example.com"),
    ],
)
def test_parse_imsg_target_preserves_explicit_target_kind(
    raw, kind, value, service, normalized
):
    target = parse_imsg_target(raw)
    assert (target.kind, target.value, target.service) == (kind, value, service)
    assert target.cli_target == normalized
    assert normalize_imsg_target(raw) == (normalized, kind == "recipient")


# ---------------------------------------------------------------------------
# send_imsg_direct / _standalone_send composition (requires gateway)
# ---------------------------------------------------------------------------

pytest.importorskip("gateway", reason="Hermes core not on PYTHONPATH")


@pytest.mark.asyncio
async def test_send_imsg_direct_rowid_uses_chat_id(monkeypatch, tmp_path):
    from hermes_imsg_cli.adapter import ImsgAdapter, send_imsg_direct

    calls = []

    async def fake_run(self, args):
        calls.append(args)
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(ImsgAdapter, "_run_imsg", fake_run)
    result = await send_imsg_direct(
        {"binary": "imsg", "state_file": str(tmp_path / "state.json")}, "16", "hello"
    )
    assert result["success"] is True
    assert result["message_id"] == "OUT"
    assert calls[0][:3] == ["send", "--chat-id", "16"]


@pytest.mark.asyncio
async def test_send_imsg_direct_bare_phone_normalized_to_to(monkeypatch, tmp_path):
    from hermes_imsg_cli.adapter import ImsgAdapter, send_imsg_direct

    calls = []

    async def fake_run(self, args):
        calls.append(args)
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(ImsgAdapter, "_run_imsg", fake_run)
    # Bare 11-digit phone (as core's parser would hand it to us) → --to +E.164.
    result = await send_imsg_direct(
        {"binary": "imsg", "state_file": str(tmp_path / "state.json")}, "12025550105", "hi"
    )
    assert result["success"] is True
    assert calls[0][:3] == ["send", "--to", "+12025550105"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("chat_guid:iMessage;+;group", ["send", "--chat-guid", "iMessage;+;group"]),
        ("chat_identifier:group", ["send", "--chat-identifier", "group"]),
        ("sms:+12025550105", ["send", "--to", "+12025550105"]),
    ],
)
async def test_send_imsg_direct_preserves_explicit_targets(
    monkeypatch, tmp_path, target, expected
):
    from hermes_imsg_cli.adapter import ImsgAdapter, send_imsg_direct

    calls = []

    async def fake_run(self, args):
        calls.append(args)
        return 0, json.dumps({"guid": "OUT"}), ""

    monkeypatch.setattr(ImsgAdapter, "_run_imsg", fake_run)
    result = await send_imsg_direct(
        {"binary": "imsg", "state_file": str(tmp_path / "state.json")},
        target,
        "hello",
    )
    assert result["success"] is True
    assert calls[0][:3] == expected
    if target.startswith("sms:"):
        assert calls[0][-3:] == ["--service", "sms", "--json"]


@pytest.mark.asyncio
async def test_send_imsg_direct_reports_error(monkeypatch, tmp_path):
    from hermes_imsg_cli.adapter import ImsgAdapter, send_imsg_direct

    async def fake_run(self, args):
        return 1, "", "boom"

    monkeypatch.setattr(ImsgAdapter, "_run_imsg", fake_run)
    result = await send_imsg_direct(
        {"binary": "imsg", "state_file": str(tmp_path / "state.json")}, "16", "hi"
    )
    assert "error" in result
    assert result.get("success") is None


@pytest.mark.asyncio
async def test_send_imsg_direct_multiple_files_send_caption_only_once(monkeypatch, tmp_path):
    from hermes_imsg_cli.adapter import ImsgAdapter, send_imsg_direct

    calls = []

    async def fake_run(self, args):
        calls.append(args)
        return 0, json.dumps({"guid": f"OUT-{len(calls)}"}), ""

    monkeypatch.setattr(ImsgAdapter, "_run_imsg", fake_run)
    file_a = tmp_path / "a.jpg"
    file_b = tmp_path / "b.jpg"
    file_a.write_bytes(b"fake-jpg-a")
    file_b.write_bytes(b"fake-jpg-b")
    result = await send_imsg_direct(
        {"binary": "imsg", "state_file": str(tmp_path / "state.json")},
        "16",
        "caption",
        media_files=[(str(file_a), False), (str(file_b), False)],
    )
    assert result["success"] is True
    assert len(calls) == 2
    assert calls[0][calls[0].index("--text") + 1] == "caption"
    assert "--text" not in calls[1]
    # Deterministic reporting: every attachment's confirmed message id is
    # surfaced, not just the last one.
    assert result["message_ids"] == ["OUT-1", "OUT-2"]
    assert result["message_id"] == "OUT-1"


@pytest.mark.asyncio
async def test_send_imsg_direct_partial_failure_reports_deterministic_state(monkeypatch, tmp_path):
    """Multi-attachment ambiguity resolution: fail-fast with an explicit report
    of exactly which attachment failed and which ones already sent."""
    from hermes_imsg_cli.adapter import ImsgAdapter, send_imsg_direct

    calls = []

    async def fake_run(self, args):
        calls.append(args)
        if len(calls) == 1:
            return 0, json.dumps({"guid": "OUT-1"}), ""
        return 1, "", "boom"

    monkeypatch.setattr(ImsgAdapter, "_run_imsg", fake_run)
    file_a = tmp_path / "a.jpg"
    file_b = tmp_path / "b.jpg"
    file_c = tmp_path / "c.jpg"
    for f in (file_a, file_b, file_c):
        f.write_bytes(b"fake")
    result = await send_imsg_direct(
        {"binary": "imsg", "state_file": str(tmp_path / "state.json")},
        "16",
        "caption",
        media_files=[(str(file_a), False), (str(file_b), False), (str(file_c), False)],
    )
    assert "success" not in result
    assert result["failed_index"] == 1
    assert result["attempted_attachments"] == 3
    assert result["succeeded_message_ids"] == ["OUT-1"]
    # Fail-fast: the third attachment is never attempted after the second fails.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_standalone_send_missing_attachment_errors(monkeypatch, tmp_path):
    from gateway.config import PlatformConfig
    from hermes_imsg_cli.adapter import _standalone_send

    if sys.platform != "darwin":
        pytest.skip("imsg standalone requires macOS")

    pconfig = PlatformConfig(enabled=True, extra={"binary": "imsg", "state_file": str(tmp_path / "s.json")})
    result = await _standalone_send(
        pconfig, "16", "hi", media_files=[("/nonexistent/file.jpg", False)]
    )
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_standalone_send_rejects_symlink_attachment(monkeypatch, tmp_path):
    from gateway.config import PlatformConfig
    from hermes_imsg_cli.adapter import _standalone_send

    if sys.platform != "darwin":
        pytest.skip("imsg standalone requires macOS")

    real_file = tmp_path / "real.jpg"
    real_file.write_bytes(b"data")
    link = tmp_path / "link.jpg"
    link.symlink_to(real_file)

    pconfig = PlatformConfig(enabled=True, extra={"binary": "imsg", "state_file": str(tmp_path / "s.json")})
    result = await _standalone_send(pconfig, "16", "hi", media_files=[(str(link), False)])
    assert "error" in result
    assert "symlink" in result["error"]


@pytest.mark.asyncio
async def test_standalone_send_rejects_directory_attachment(monkeypatch, tmp_path):
    from gateway.config import PlatformConfig
    from hermes_imsg_cli.adapter import _standalone_send

    if sys.platform != "darwin":
        pytest.skip("imsg standalone requires macOS")

    directory = tmp_path / "adir"
    directory.mkdir()

    pconfig = PlatformConfig(enabled=True, extra={"binary": "imsg", "state_file": str(tmp_path / "s.json")})
    result = await _standalone_send(pconfig, "16", "hi", media_files=[(str(directory), False)])
    assert "error" in result
    assert "regular file" in result["error"]


@pytest.mark.asyncio
async def test_standalone_send_non_darwin_returns_error(monkeypatch, tmp_path):
    from gateway.config import PlatformConfig
    from hermes_imsg_cli import adapter as adapter_mod

    monkeypatch.setattr(adapter_mod.sys, "platform", "linux")
    pconfig = PlatformConfig(enabled=True, extra={"binary": "imsg"})
    result = await adapter_mod._standalone_send(pconfig, "16", "hi")
    assert "error" in result
    assert "macOS" in result["error"]
