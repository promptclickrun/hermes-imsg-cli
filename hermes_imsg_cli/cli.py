"""Command composition + subprocess execution for the local ``imsg`` CLI.

The ``build_*`` functions are pure (no I/O) so command composition can be
unit-tested without invoking the binary or importing Hermes.  The ``run_*``
helpers execute the binary and are the single choke-point for subprocess use.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any, Iterable, Optional, Union


STANDARD_REACTIONS = frozenset({"love", "like", "dislike", "laugh", "emphasis", "question"})
TAPBACK_REACTIONS = frozenset({"love", "like", "dislike", "laugh", "emphasize", "question"})
VALID_SERVICES = frozenset({"imessage", "sms", "auto"})
VALID_SEND_TRANSPORTS = frozenset({"auto", "bridge", "applescript"})

# SQLite (and therefore Messages chat.db) rowids fit in a signed 64-bit
# integer (max ~9.2e18, 19 digits).  A "chat_id" value with more digits than
# this can never be a real rowid, so it is rejected before it is ever handed
# to the imsg binary rather than silently shelled out.
_MAX_ROWID_DIGITS = 19

# Bounded grace period between SIGTERM and SIGKILL for subprocesses started by
# this module.  Short enough to keep bounded-timeout callers responsive, long
# enough for a well-behaved child to exit cleanly on SIGTERM.
DEFAULT_TERMINATE_GRACE = 2.0


class ImsgJSONError(ValueError):
    """Raised when ``imsg --json`` stdout violates its NDJSON contract."""


def _validate_rowid_target(value: Any, *, label: str = "chat_id") -> str:
    """Validate *value* as a positive-integer Messages chat rowid string.

    Shared by the plain-send ``--chat-id`` path and the poll target
    validator so a malformed or out-of-range target fails fast, before any
    subprocess is started, instead of being passed through to the imsg
    binary on faith.
    """
    text = str(value).strip()
    if not text or not text.isascii() or not text.isdigit():
        raise ValueError(f"{label} must be a positive integer rowid, not a participant handle")
    if len(text) > _MAX_ROWID_DIGITS:
        raise ValueError(f"{label} exceeds the maximum rowid length ({_MAX_ROWID_DIGITS} digits)")
    if int(text) <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return text


def _required(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} must not be empty")
    if "\x00" in text:
        raise ValueError(f"{label} contains an invalid null character")
    return text


def _validate_part_index(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("part_index must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("part_index must be a non-negative integer") from exc
    if normalized < 0:
        raise ValueError("part_index must be a non-negative integer")
    return normalized


def parse_send_target(
    target: Any,
    *,
    is_phone: bool = False,
    service: Optional[str] = None,
) -> tuple[str, str, Optional[str]]:
    """Return ``(flag, value, effective_service)`` for an outbound target."""
    raw = str(target).strip()
    if "\x00" in raw:
        raise ValueError("target contains an invalid null character")
    lowered = raw.lower()
    explicit_service: Optional[str] = None

    target_prefixes = (
        (("chat_id:", "chatid:", "chat:"), "--chat-id", "chat_id"),
        (("chat_guid:", "chatguid:", "guid:"), "--chat-guid", "chat_guid"),
        (
            ("chat_identifier:", "chatidentifier:", "chatident:"),
            "--chat-identifier",
            "chat_identifier",
        ),
    )
    for prefixes, flag, label in target_prefixes:
        for prefix in prefixes:
            if lowered.startswith(prefix):
                value = raw[len(prefix):].strip()
                if label == "chat_id":
                    value = _validate_rowid_target(value, label=label)
                else:
                    value = _required(value, label)
                return flag, value, service

    for prefix in ("imessage", "sms", "auto"):
        marker = prefix + ":"
        if lowered.startswith(marker):
            explicit_service = prefix
            raw = _required(raw[len(marker):], "recipient")
            break

    normalized_service: Optional[str] = None
    if service:
        normalized_service = str(service).strip().lower()
        if normalized_service not in VALID_SERVICES:
            raise ValueError("service must be imessage, sms, or auto")
    if explicit_service and normalized_service and explicit_service != normalized_service:
        raise ValueError(
            f"target service {explicit_service} conflicts with explicit service {normalized_service}"
        )
    effective_service = explicit_service or normalized_service

    use_to = explicit_service is not None or is_phone or raw.startswith("+") or "@" in raw
    if use_to:
        return "--to", _required(raw, "recipient"), effective_service
    return "--chat-id", _validate_rowid_target(raw, label="chat_id"), effective_service


# ---------------------------------------------------------------------------
# Pure argument builders
# ---------------------------------------------------------------------------

def build_plain_send_args(
    target: str,
    text: str,
    *,
    is_phone: bool = False,
    media_files: Optional[Iterable[tuple[str, bool]]] = None,
    service: Optional[str] = None,
    region: Optional[str] = None,
    no_sms_fallback: bool = False,
) -> list[str]:
    """Build ``imsg send`` args.

    ``is_phone`` routes to ``--to`` (E.164 / email handle); otherwise the target
    is treated as a Messages chat rowid via ``--chat-id``; the reply-routing
    key that must never be guessed.  A leading ``+`` or an ``@`` in ``target``
    also forces ``--to`` for safety even if the caller forgot to set is_phone.
    A ``--chat-id`` target is validated as a positive-integer rowid within a
    sane digit range before it is ever handed to the imsg binary; a malformed
    or out-of-range value fails fast instead of being shelled out on faith.
    """
    target_flag, target_value, effective_service = parse_send_target(
        target, is_phone=is_phone, service=service
    )
    args = ["send", target_flag, target_value]
    if text:
        args.extend(["--text", text])
    files = list(media_files or [])
    if len(files) > 1:
        raise ValueError("imsg send accepts one attachment per invocation")
    for media_path, _is_voice in files:
        args.extend(["--file", str(media_path)])
    if not text and not files:
        raise ValueError("imsg send requires text or one attachment")
    if effective_service:
        args.extend(["--service", effective_service])
    if region:
        args.extend(["--region", str(region)])
    if no_sms_fallback:
        args.append("--no-sms-fallback")
    args.append("--json")
    return args


def build_rich_send_args(
    chat_guid: str,
    text: str,
    *,
    reply_to: Optional[str] = None,
    effect: Optional[str] = None,
    part_index: int = 0,
    file_path: Optional[str] = None,
    formatting: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    """Build bridge-backed rich text, reply, effect, and attachment arguments."""
    content = str(text or "")
    if not content and not file_path:
        raise ValueError("rich send requires text or one attachment")
    args = [
        "send-rich",
        "--chat", _required(chat_guid, "chat_guid"),
        "--text", content,
        "--part", str(_validate_part_index(part_index)),
    ]
    if effect:
        args.extend(["--effect", _required(effect, "effect")])
    if reply_to:
        args.extend(["--reply-to", _required(reply_to, "reply_to")])
    if formatting:
        args.extend(["--format", json.dumps(formatting, separators=(",", ":"))])
    if file_path:
        args.extend(["--file", _required(file_path, "file_path")])
    args.append("--json")
    return args


def build_rich_reply_args(chat_guid: str, reply_to_guid: str, text: str) -> list[str]:
    """Compatibility wrapper for an in-thread reply to a specific GUID."""
    return build_rich_send_args(chat_guid, text, reply_to=reply_to_guid)


def build_send_rpc_request(
    request_id: Any,
    *,
    text: str,
    chat_id: Optional[Union[str, int]] = None,
    chat_guid: Optional[str] = None,
    chat_identifier: Optional[str] = None,
    to: Optional[str] = None,
    service: str = "auto",
    region: Optional[str] = None,
    transport: str = "auto",
    reply_to: Optional[str] = None,
    file_path: Optional[str] = None,
    formatting: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build a single exact-target ``send`` JSON-RPC request."""
    supplied = [
        chat_id is not None and str(chat_id).strip() != "",
        bool(str(chat_guid or "").strip()),
        bool(str(chat_identifier or "").strip()),
        bool(str(to or "").strip()),
    ]
    if sum(supplied) != 1:
        raise ValueError("exactly one of chat_id, chat_guid, chat_identifier, or to is required")
    normalized_service = str(service or "auto").strip().lower()
    if normalized_service not in VALID_SERVICES:
        raise ValueError("service must be imessage, sms, or auto")
    normalized_transport = str(transport or "auto").strip().lower()
    if normalized_transport not in VALID_SEND_TRANSPORTS:
        raise ValueError("transport must be auto, bridge, or applescript")
    if not str(text or "") and not file_path:
        raise ValueError("send requires text or one attachment")
    params: dict[str, Any] = {
        "text": str(text or ""),
        "service": normalized_service,
        "transport": normalized_transport,
    }
    if chat_id is not None and str(chat_id).strip():
        params["chat_id"] = int(_validate_rowid_target(chat_id, label="chat_id"))
    elif chat_guid:
        params["chat_guid"] = _required(chat_guid, "chat_guid")
    elif chat_identifier:
        params["chat_identifier"] = _required(chat_identifier, "chat_identifier")
    else:
        params["to"] = _required(to, "to")
    if region:
        params["region"] = str(region)
    if reply_to:
        params["reply_to"] = _required(reply_to, "reply_to")
    if file_path:
        params["file"] = _required(file_path, "file_path")
    if formatting:
        params["formatting"] = formatting
    return {"jsonrpc": "2.0", "id": request_id, "method": "send", "params": params}


def build_tapback_args(
    chat_guid: str,
    message_guid: str,
    kind: str,
    *,
    remove: bool = False,
    part_index: int = 0,
) -> list[str]:
    reaction = str(kind or "").strip().lower().replace("emphasis", "emphasize")
    if reaction not in TAPBACK_REACTIONS:
        raise ValueError(
            "tapback must be love, like, dislike, laugh, emphasize, or question"
        )
    args = [
        "tapback", "--chat", _required(chat_guid, "chat_guid"),
        "--message", _required(message_guid, "message_id"),
        "--kind", reaction,
        "--part", str(_validate_part_index(part_index)),
    ]
    if remove:
        args.append("--remove")
    args.append("--json")
    return args


def build_edit_args(
    chat_guid: str,
    message_guid: str,
    text: str,
    *,
    backwards_compatibility_message: Optional[str] = None,
    part_index: int = 0,
) -> list[str]:
    content = _required(text, "text")
    backwards = (
        _required(backwards_compatibility_message, "backwards_compatibility_message")
        if backwards_compatibility_message is not None
        else content
    )
    return [
        "edit", "--chat", _required(chat_guid, "chat_guid"),
        "--message", _required(message_guid, "message_id"),
        "--new-text", content,
        "--bc-text", backwards,
        "--part", str(_validate_part_index(part_index)),
        "--json",
    ]


def build_unsend_args(
    chat_guid: str, message_guid: str, *, part_index: int = 0
) -> list[str]:
    return [
        "unsend", "--chat", _required(chat_guid, "chat_guid"),
        "--message", _required(message_guid, "message_id"),
        "--part", str(_validate_part_index(part_index)),
        "--json",
    ]


def build_send_attachment_args(
    chat_guid: str, file_path: str, *, as_voice: bool = False
) -> list[str]:
    args = [
        "send-attachment", "--chat", _required(chat_guid, "chat_guid"),
        "--file", _required(file_path, "file_path"),
    ]
    if as_voice:
        args.append("--audio")
    args.append("--json")
    return args


def build_chat_name_args(chat_guid: str, display_name: str) -> list[str]:
    return [
        "chat-name", "--chat", _required(chat_guid, "chat_guid"),
        "--name", _required(display_name, "display_name"), "--json",
    ]


def build_chat_photo_args(chat_guid: str, file_path: str) -> list[str]:
    return [
        "chat-photo", "--chat", _required(chat_guid, "chat_guid"),
        "--file", _required(file_path, "file_path"), "--json",
    ]


def build_chat_member_args(action: str, chat_guid: str, address: str) -> list[str]:
    normalized = str(action or "").strip().lower()
    if normalized not in {"add", "remove"}:
        raise ValueError("chat member action must be add or remove")
    return [
        "chat-add-member" if normalized == "add" else "chat-remove-member",
        "--chat", _required(chat_guid, "chat_guid"),
        "--address", _required(address, "address"), "--json",
    ]


def build_chat_leave_args(chat_guid: str) -> list[str]:
    return ["chat-leave", "--chat", _required(chat_guid, "chat_guid"), "--json"]


def build_watch_args(
    debounce: str,
    since_rowid: Optional[int] = None,
    *,
    chat_id: Optional[str] = None,
    attachments: bool = True,
    convert_attachments: bool = True,
    reactions: bool = False,
    participants: Optional[Iterable[str]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[str]:
    """Build ``imsg watch`` args (JSON stream, attachments converted)."""
    args = ["watch", "--json"]
    if attachments:
        args.append("--attachments")
    if convert_attachments:
        args.append("--convert-attachments")
    if reactions:
        args.append("--reactions")
    if chat_id is not None:
        args.extend(["--chat-id", str(chat_id)])
    participant_values = [str(value) for value in (participants or []) if str(value)]
    if participant_values:
        args.extend(["--participants", ",".join(participant_values)])
    if start:
        args.extend(["--start", str(start)])
    if end:
        args.extend(["--end", str(end)])
    args.extend(["--debounce", str(debounce)])
    if since_rowid is not None:
        args.extend(["--since-rowid", str(since_rowid)])
    return args


def build_read_args(chat_id: str) -> list[str]:
    return ["read", "--chat-id", str(chat_id), "--json"]


def build_typing_args(
    chat_id: str,
    duration_seconds: Optional[float] = None,
    *,
    stop: bool = False,
) -> list[str]:
    args = ["typing", "--chat-id", str(chat_id)]
    if stop:
        args.extend(["--stop", "true"])
    elif duration_seconds:
        args.extend(["--duration", f"{duration_seconds}s"])
    args.append("--json")
    return args


def build_chats_args(limit: int = 20, unread_only: bool = False) -> list[str]:
    args = ["chats", "--limit", str(limit)]
    if unread_only:
        args.append("--unread-only")
    args.append("--json")
    return args


def build_group_args(chat_id: str) -> list[str]:
    return ["group", "--chat-id", str(chat_id), "--json"]


def build_history_args(
    chat_id: str,
    *,
    limit: int = 50,
    attachments: bool = True,
    convert_attachments: bool = False,
    participants: Optional[Iterable[str]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[str]:
    args = ["history", "--chat-id", str(chat_id), "--limit", str(limit)]
    if attachments:
        args.append("--attachments")
    if convert_attachments:
        args.append("--convert-attachments")
    participant_values = [str(value) for value in (participants or []) if str(value)]
    if participant_values:
        args.extend(["--participants", ",".join(participant_values)])
    if start:
        args.extend(["--start", str(start)])
    if end:
        args.extend(["--end", str(end)])
    args.append("--json")
    return args


def build_search_args(query: str, *, match: str = "contains", limit: int = 50) -> list[str]:
    match = str(match).strip().lower()
    if match not in {"contains", "exact"}:
        raise ValueError("search match must be contains or exact")
    if not str(query):
        raise ValueError("search query must not be empty")
    return [
        "search", "--query", str(query), "--match", match,
        "--limit", str(limit), "--json",
    ]


def build_status_args() -> list[str]:
    return ["status", "--json"]


def build_react_args(chat_id: str, reaction: str) -> list[str]:
    if str(reaction) not in STANDARD_REACTIONS:
        raise ValueError("reaction must be a standard reaction: " + ", ".join(sorted(STANDARD_REACTIONS)))
    return ["react", "--chat-id", str(chat_id), "--reaction", str(reaction), "--json"]


def _poll_target_args(
    *, chat_id: Optional[Union[str, int]] = None, chat_guid: Optional[str] = None
) -> tuple[list[str], dict[str, Any]]:
    """Validate a poll target and return CLI args plus RPC params.

    Polls intentionally accept only an exact local rowid or an exact chat GUID.
    Participant handles are not accepted, so an inbound operation cannot drift
    from its originating conversation through recipient resolution.
    """
    has_id = chat_id is not None and str(chat_id).strip() != ""
    has_guid = chat_guid is not None and str(chat_guid).strip() != ""
    if has_id == has_guid:
        raise ValueError("exactly one of chat_id or chat_guid is required")
    if has_id:
        # ``int('+1555…')`` is valid Python but a leading plus denotes a
        # participant handle in this domain. Accept decimal rowids only,
        # bounded to a sane digit range (see ``_validate_rowid_target``).
        value = _validate_rowid_target(chat_id, label="chat_id")
        return ["--chat-id", value], {"chat_id": int(value)}
    value = str(chat_guid).strip()
    return ["--chat", value], {"chat_guid": value}


def _poll_options(options: Iterable[str]) -> list[str]:
    values = [str(value).strip() for value in options if str(value).strip()]
    if len(values) < 2:
        raise ValueError("at least two non-empty poll options are required")
    if len(values) > 12:
        raise ValueError("at most 12 poll options are allowed")
    if len(set(values)) != len(values):
        raise ValueError("poll options must be distinct")
    return values


def build_poll_send_args(
    *,
    question: str,
    options: Iterable[str],
    chat_id: Optional[Union[str, int]] = None,
    chat_guid: Optional[str] = None,
    comment: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> list[str]:
    """Build pinned-upstream ``imsg poll send`` arguments."""
    target, _ = _poll_target_args(chat_id=chat_id, chat_guid=chat_guid)
    question_value = str(question).strip()
    if not question_value:
        raise ValueError("poll question must not be empty")
    option_values = _poll_options(options)
    args = ["poll", "send", *target, "--question", question_value]
    if comment is not None and str(comment).strip():
        args.extend(["--comment", str(comment).strip()])
    if reply_to is not None and str(reply_to).strip():
        args.extend(["--reply-to", str(reply_to).strip()])
    for option in option_values:
        args.extend(["--option", option])
    args.append("--json")
    return args


def build_poll_vote_args(
    action: str,
    *,
    poll_guid: str,
    chat_id: Optional[Union[str, int]] = None,
    chat_guid: Optional[str] = None,
    option_id: Optional[str] = None,
    option_index: Optional[int] = None,
    option: Optional[str] = None,
) -> list[str]:
    """Build ``imsg poll vote|unvote`` with exactly one option selector."""
    action_value = str(action).strip().lower()
    if action_value not in {"vote", "unvote"}:
        raise ValueError("poll action must be vote or unvote")
    target, _ = _poll_target_args(chat_id=chat_id, chat_guid=chat_guid)
    poll_value = str(poll_guid).strip()
    if not poll_value:
        raise ValueError("poll_guid must not be empty")
    id_value = str(option_id).strip() if option_id is not None else ""
    text_value = str(option).strip() if option is not None else ""
    supplied = [bool(id_value), option_index is not None, bool(text_value)]
    if sum(supplied) != 1:
        raise ValueError("exactly one of option_id, option_index, or option is required")
    args = ["poll", action_value, *target, "--poll", poll_value]
    if id_value:
        args.extend(["--option-id", id_value])
    elif option_index is not None:
        if isinstance(option_index, bool) or int(option_index) < 1:
            raise ValueError("option_index must be a 1-based positive integer")
        args.extend(["--option-index", str(int(option_index))])
    else:
        args.extend(["--option", text_value])
    args.append("--json")
    return args


def build_poll_send_rpc_request(
    request_id: Any,
    *,
    question: str,
    options: Iterable[str],
    chat_id: Optional[Union[str, int]] = None,
    chat_guid: Optional[str] = None,
    comment: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> dict[str, Any]:
    """Build (but do not execute) the pinned ``poll.send`` JSON-RPC request."""
    _, params = _poll_target_args(chat_id=chat_id, chat_guid=chat_guid)
    question_value = str(question).strip()
    if not question_value:
        raise ValueError("poll question must not be empty")
    params.update(question=question_value, options=_poll_options(options))
    if comment is not None and str(comment).strip():
        params["comment"] = str(comment).strip()
    if reply_to is not None and str(reply_to).strip():
        params["reply_to"] = str(reply_to).strip()
    return {"jsonrpc": "2.0", "id": request_id, "method": "poll.send", "params": params}


def build_poll_vote_rpc_request(
    request_id: Any,
    action: str,
    *,
    poll_guid: str,
    option_id: Optional[str] = None,
    option_index: Optional[int] = None,
    option: Optional[str] = None,
    chat_id: Optional[Union[str, int]] = None,
    chat_guid: Optional[str] = None,
) -> dict[str, Any]:
    """Build the RPC vote/unvote request (RPC accepts option IDs only)."""
    action_value = str(action).strip().lower()
    if action_value not in {"vote", "unvote"}:
        raise ValueError("poll action must be vote or unvote")
    if option_index is not None or (option is not None and str(option).strip()):
        raise ValueError("poll vote JSON-RPC requires option_id; index/text are CLI-only")
    id_value = str(option_id).strip() if option_id is not None else ""
    if not id_value:
        raise ValueError("poll vote JSON-RPC requires option_id")
    poll_value = str(poll_guid).strip()
    if not poll_value:
        raise ValueError("poll_guid must not be empty")
    _, params = _poll_target_args(chat_id=chat_id, chat_guid=chat_guid)
    params.update(poll_guid=poll_value, option_id=id_value)
    method = "poll.vote" if action_value == "vote" else "polls.unvote"
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def build_launch_args() -> list[str]:
    return ["launch"]


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

def parse_json_output(stdout: str) -> list[Any]:
    """Parse imsg's one-object-per-line JSON output.

    A single JSON array is accepted for compatibility with older wrappers and
    test fixtures.  Errors identify the line but intentionally omit its content
    because message JSON can contain private text and handles.
    """
    text = str(stdout or "")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ImsgJSONError("invalid imsg JSON array") from exc
        if not isinstance(value, list):
            raise ImsgJSONError("invalid imsg JSON array")
        return value
    values: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ImsgJSONError(f"invalid imsg JSON at line {line_number}") from exc
    return values

def extract_message_id(stdout: str) -> Optional[str]:
    """Pull a message identifier from a JSON ``imsg`` send response."""
    try:
        data = json.loads(stdout or "{}")
    except Exception:
        return None
    if isinstance(data, dict):
        for key in ("guid", "message_guid", "messageGuid", "id", "rowid"):
            if data.get(key) is not None:
                return str(data.get(key))
    return None


# ---------------------------------------------------------------------------
# Subprocess execution (single choke-point)
# ---------------------------------------------------------------------------

async def _terminate_then_kill(
    proc: "asyncio.subprocess.Process", *, grace: Optional[float] = None
) -> None:
    """Bounded child-process shutdown: SIGTERM, wait *grace* seconds, then SIGKILL.

    A cancelled/timed-out ``imsg`` invocation must never leave an orphaned
    child running.  ``terminate()`` gives a well-behaved process a chance to
    exit cleanly; a process that ignores SIGTERM (hung, wedged, or adversarial)
    is bounded by *grace* and then force-killed so cleanup can never hang
    indefinitely.  ``grace`` defaults to the module-level
    ``DEFAULT_TERMINATE_GRACE`` (resolved at call time, not import time, so
    tests can shrink it via monkeypatching for fast deterministic runs).
    """
    if proc.returncode is not None:
        return
    proc.terminate()
    effective_grace = DEFAULT_TERMINATE_GRACE if grace is None else grace
    try:
        await asyncio.wait_for(proc.wait(), timeout=effective_grace)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


async def run_imsg(binary: str, args: list[str]) -> tuple[int, str, str]:
    """Run ``binary <args>`` asynchronously, returning ``(code, stdout, stderr)``."""
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await proc.communicate()
    except asyncio.CancelledError:
        # ``ImsgClient`` and the adapter impose timeouts with wait_for().  Do not
        # leave a timed-out send/read child running after its coroutine is
        # cancelled: terminate first, then bound the wait before killing so a
        # child that ignores SIGTERM can never hang cleanup indefinitely.
        await _terminate_then_kill(proc)
        raise
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


async def run_imsg_rpc(binary: str, request: dict[str, Any]) -> tuple[int, str, str]:
    """Send one JSON-RPC request through a single ``imsg rpc`` process.

    Poll mutations intentionally use this path instead of ``imsg poll``. The
    upstream/OpenClaw integration keeps one RPC transport and calls
    ``poll.send`` directly; the CLI command has an additional caption-echo
    path that can be interpreted as a second native poll by some Messages
    bridge versions.
    """
    proc = await asyncio.create_subprocess_exec(
        binary,
        "rpc",
        "--json",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    try:
        proc.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        if not line:
            stderr_b = await proc.stderr.read()
            await proc.wait()
            return proc.returncode or 1, "", stderr_b.decode("utf-8", errors="replace")
        proc.stdin.close()
        await proc.wait()
        stderr_b = await proc.stderr.read()
        return proc.returncode or 0, line.decode("utf-8", errors="replace"), stderr_b.decode(
            "utf-8", errors="replace"
        )
    except asyncio.CancelledError:
        # Bounded terminate-then-kill: never leave an orphaned `imsg rpc`
        # process running past a short grace period.
        await _terminate_then_kill(proc)
        raise
    except Exception:
        await _terminate_then_kill(proc)
        raise


def run_imsg_sync(binary: str, args: list[str], *, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run ``binary <args>`` synchronously (used by requirements probes)."""
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return 1, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""
