"""Hermes-facing native iMessage actions with exact target resolution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from typing import Any, Optional

from . import cli
from .capabilities import ACTION_NAMES
from .client import ImsgClient, ImsgCommandError
from .config import (
    ENV_ALLOWED_USERS,
    ENV_BINARY,
    ENV_COMMAND_TIMEOUT,
    ENV_GROUP_ACTION_ADMINS,
    resolve_binary,
)
from .rules import _csv_set, _redact


_TARGET_KEYS = ("chat_id", "chat_guid", "chat_identifier", "to")
_MESSAGE_ACTIONS = frozenset({"react", "edit", "unsend", "reply"})
_GROUP_ACTIONS = frozenset(
    {
        "renameGroup",
        "setGroupIcon",
        "addParticipant",
        "removeParticipant",
        "leaveGroup",
    }
)
_DIRECT_SYNTHESIS_ACTIONS = frozenset(
    {"sendWithEffect", "upload-file", "poll"}
)
_LOCAL_OPERATOR_SURFACES = frozenset(
    {"", "cli", "codex", "desktop", "local", "tui"}
)

_ACTION_FIELDS: dict[str, frozenset[str]] = {
    "react": frozenset({"message_id", "reaction", "remove", "part_index"}),
    "edit": frozenset(
        {
            "message_id",
            "text",
            "backwards_compatibility_message",
            "part_index",
        }
    ),
    "unsend": frozenset({"message_id", "part_index"}),
    "reply": frozenset({"message_id", "text", "file_path", "part_index"}),
    "sendWithEffect": frozenset({"text", "effect", "part_index"}),
    "upload-file": frozenset({"file_path", "as_voice"}),
    "renameGroup": frozenset({"display_name"}),
    "setGroupIcon": frozenset({"file_path"}),
    "addParticipant": frozenset({"address"}),
    "removeParticipant": frozenset({"address"}),
    "leaveGroup": frozenset(),
    "poll": frozenset({"question", "options", "comment", "reply_to"}),
    "poll-vote": frozenset(
        {"poll_guid", "option_id", "option_index", "option"}
    ),
    "poll-unvote": frozenset(
        {"poll_guid", "option_id", "option_index", "option"}
    ),
}

_EFFECT_ALIASES = {
    "slam": "com.apple.MobileSMS.expressivesend.impact",
    "impact": "com.apple.MobileSMS.expressivesend.impact",
    "loud": "com.apple.MobileSMS.expressivesend.loud",
    "gentle": "com.apple.MobileSMS.expressivesend.gentle",
    "invisibleink": "com.apple.MobileSMS.expressivesend.invisibleink",
    "invisible-ink": "com.apple.MobileSMS.expressivesend.invisibleink",
    "confetti": "com.apple.MobileSMS.expressivesend.confetti",
    "lasers": "com.apple.MobileSMS.expressivesend.lasers",
    "fireworks": "com.apple.MobileSMS.expressivesend.fireworks",
    "balloon": "com.apple.MobileSMS.expressivesend.balloon",
    "balloons": "com.apple.MobileSMS.expressivesend.balloon",
    "heart": "com.apple.MobileSMS.expressivesend.heart",
    "echo": "com.apple.messages.effect.CKEchoEffect",
    "happybirthday": "com.apple.messages.effect.CKHappyBirthdayEffect",
    "happy-birthday": "com.apple.messages.effect.CKHappyBirthdayEffect",
    "shootingstar": "com.apple.messages.effect.CKShootingStarEffect",
    "shooting-star": "com.apple.messages.effect.CKShootingStarEffect",
    "sparkles": "com.apple.messages.effect.CKSparklesEffect",
    "spotlight": "com.apple.messages.effect.CKSpotlightEffect",
}
_CANONICAL_EFFECT_IDS = {
    effect_id.lower(): effect_id for effect_id in _EFFECT_ALIASES.values()
}


IMSG_ACTION_TOOL_SCHEMA = {
    "name": "imsg_action",
    "description": (
        "Perform a native Apple Messages action in one exact conversation. "
        "Supply exactly one of chat_id, chat_guid, chat_identifier, or to. "
        "Message references are verified inside that conversation before mutation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTION_NAMES)},
            "chat_id": {
                "type": "string",
                "description": "Exact positive Messages chat rowid.",
            },
            "chat_guid": {
                "type": "string",
                "description": "Exact Messages chat GUID.",
            },
            "chat_identifier": {
                "type": "string",
                "description": "Exact identifier from imsg chat metadata.",
            },
            "to": {
                "type": "string",
                "description": "Direct imessage:, sms:, phone, or email target.",
            },
            "message_id": {"type": "string"},
            "reaction": {"type": "string"},
            "remove": {"type": "boolean"},
            "part_index": {"type": "integer", "minimum": 0},
            "text": {"type": "string"},
            "backwards_compatibility_message": {"type": "string"},
            "effect": {"type": "string"},
            "file_path": {"type": "string"},
            "as_voice": {"type": "boolean"},
            "display_name": {"type": "string"},
            "address": {"type": "string"},
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 12,
            },
            "comment": {"type": "string"},
            "reply_to": {"type": "string"},
            "poll_guid": {"type": "string"},
            "option_id": {"type": "string"},
            "option_index": {"type": "integer", "minimum": 1},
            "option": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


IMSG_POLL_TOOL_SCHEMA = {
    "name": "imsg_poll",
    "description": (
        "Compatibility tool for native Apple Messages poll creation, voting, "
        "and selective unvoting. Uses the same resolver as imsg_action."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["send", "vote", "unvote"]},
            "chat_id": {"type": "string"},
            "chat_guid": {"type": "string"},
            "chat_identifier": {"type": "string"},
            "to": {"type": "string"},
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 12,
            },
            "comment": {"type": "string"},
            "reply_to": {"type": "string"},
            "poll_guid": {"type": "string"},
            "option_id": {"type": "string"},
            "option_index": {"type": "integer", "minimum": 1},
            "option": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class ResolvedTarget:
    chat_id: Optional[str]
    chat_guid: str
    chat_identifier: Optional[str] = None
    to: Optional[str] = None
    is_group: Optional[bool] = None


def normalize_effect_id(value: Any) -> str:
    """Return a supported canonical Apple effect identifier."""
    effect = str(value or "").strip()
    if not effect:
        raise ValueError("effect must not be empty")
    normalized = effect.lower()
    if normalized in _EFFECT_ALIASES:
        return _EFFECT_ALIASES[normalized]
    if normalized in _CANONICAL_EFFECT_IDS:
        return _CANONICAL_EFFECT_IDS[normalized]
    allowed = ", ".join(sorted(_EFFECT_ALIASES))
    raise ValueError(f"unsupported effect; use one of: {allowed}")


def _required(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    text = str(value if value is not None else "")
    if not text.strip():
        raise ValueError(f"{name} is required")
    if "\x00" in text:
        raise ValueError(f"{name} contains an invalid null character")
    return text


def _optional_text(args: dict[str, Any], name: str) -> Optional[str]:
    if args.get(name) is None:
        return None
    value = str(args[name])
    if not value.strip():
        raise ValueError(f"{name} must not be empty when provided")
    if "\x00" in value:
        raise ValueError(f"{name} contains an invalid null character")
    return value


def _boolean(args: dict[str, Any], name: str, default: bool = False) -> bool:
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _integer(args: dict[str, Any], name: str, default: int, minimum: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _validate_payload(action: str, args: dict[str, Any]) -> dict[str, Any]:
    allowed = frozenset(_TARGET_KEYS) | _ACTION_FIELDS[action] | {"action"}
    extras = sorted(str(key) for key in args if key not in allowed)
    if extras:
        raise ValueError(
            f"{action} does not accept parameter(s): {', '.join(extras)}"
        )
    values: dict[str, Any] = {}
    if action == "react":
        remove = _boolean(args, "remove")
        reaction = _optional_text(args, "reaction")
        if not remove and reaction is None:
            raise ValueError("reaction is required when adding a tapback")
        values.update(
            message_id=_required(args, "message_id"),
            reaction=reaction,
            remove=remove,
            part_index=_integer(args, "part_index", 0, 0),
        )
    elif action == "edit":
        values.update(
            message_id=_required(args, "message_id"),
            text=_required(args, "text"),
            backwards_compatibility_message=_optional_text(
                args, "backwards_compatibility_message"
            ),
            part_index=_integer(args, "part_index", 0, 0),
        )
    elif action == "unsend":
        values.update(
            message_id=_required(args, "message_id"),
            part_index=_integer(args, "part_index", 0, 0),
        )
    elif action == "reply":
        text = str(args.get("text") or "")
        file_path = _optional_text(args, "file_path")
        if not text and not file_path:
            raise ValueError("reply requires text or file_path")
        values.update(
            message_id=_required(args, "message_id"),
            text=text,
            file_path=file_path,
            part_index=_integer(args, "part_index", 0, 0),
        )
    elif action == "sendWithEffect":
        values.update(
            text=_required(args, "text"),
            effect=normalize_effect_id(args.get("effect")),
            part_index=_integer(args, "part_index", 0, 0),
        )
    elif action == "upload-file":
        values.update(
            file_path=_required(args, "file_path"),
            as_voice=_boolean(args, "as_voice"),
        )
    elif action == "renameGroup":
        values["display_name"] = _required(args, "display_name")
    elif action == "setGroupIcon":
        values["file_path"] = _required(args, "file_path")
    elif action in {"addParticipant", "removeParticipant"}:
        values["address"] = _required(args, "address")
    elif action == "poll":
        options = args.get("options")
        if not isinstance(options, list):
            raise ValueError("options must be an array")
        if not 2 <= len(options) <= 12:
            raise ValueError("options must contain between 2 and 12 entries")
        values.update(
            question=_required(args, "question"),
            options=options,
            comment=_optional_text(args, "comment"),
            reply_to=_optional_text(args, "reply_to"),
        )
    elif action in {"poll-vote", "poll-unvote"}:
        supplied = [
            args.get("option_id") is not None,
            args.get("option_index") is not None,
            args.get("option") is not None,
        ]
        if sum(supplied) != 1:
            raise ValueError(
                "exactly one of option_id, option_index, or option is required"
            )
        values.update(
            poll_guid=_required(args, "poll_guid"),
            option_id=_optional_text(args, "option_id"),
            option_index=(
                _integer(args, "option_index", 1, 1)
                if args.get("option_index") is not None
                else None
            ),
            option=_optional_text(args, "option"),
        )
    return values


def _record_value(record: dict[str, Any], *names: str) -> Optional[str]:
    for name in names:
        value = record.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _record_is_group(record: dict[str, Any]) -> Optional[bool]:
    for name in ("is_group", "isGroup", "group"):
        value = record.get(name)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, str)) and str(value).strip().lower() in {
            "0",
            "1",
            "false",
            "true",
        }:
            return str(value).strip().lower() in {"1", "true"}
    participants = record.get("participants")
    if isinstance(participants, list):
        return len(participants) > 1
    return None


def _target_from_record(
    record: dict[str, Any], *, fallback_chat_id: Optional[str] = None
) -> ResolvedTarget:
    chat_id = _record_value(record, "id", "chat_id", "chatId", "rowid")
    chat_guid = _record_value(record, "guid", "chat_guid", "chatGuid")
    if not chat_guid:
        raise ValueError("target conversation does not expose a chat GUID")
    return ResolvedTarget(
        chat_id=chat_id or fallback_chat_id,
        chat_guid=chat_guid,
        chat_identifier=_record_value(
            record, "chat_identifier", "chatIdentifier", "identifier"
        ),
        is_group=_record_is_group(record),
    )


def _record_handles(record: dict[str, Any]) -> set[str]:
    handles: set[str] = set()
    for name in ("handle", "address", "recipient"):
        value = record.get(name)
        if value is not None and str(value).strip():
            handles.add(str(value).strip().casefold())
    participants = record.get("participants")
    if isinstance(participants, list):
        for participant in participants:
            if isinstance(participant, dict):
                value = _record_value(
                    participant, "handle", "address", "id", "identifier"
                )
            else:
                value = str(participant).strip() if participant is not None else None
            if value:
                handles.add(value.casefold())
    return handles


def _record_matches_identifier(record: dict[str, Any], identifier: str) -> bool:
    candidate = _record_value(
        record, "chat_identifier", "chatIdentifier", "identifier"
    )
    return candidate == identifier


async def resolve_target(
    client: ImsgClient, args: dict[str, Any], action: str
) -> ResolvedTarget:
    supplied = [key for key in _TARGET_KEYS if args.get(key) is not None]
    if len(supplied) != 1:
        raise ValueError(
            "exactly one of chat_id, chat_guid, chat_identifier, or to is required"
        )
    key = supplied[0]
    value = _required(args, key).strip()

    if key == "chat_id":
        _, chat_id, _ = cli.parse_send_target(f"chat_id:{value}")
        record = await client.group(chat_id)
        record_id = _record_value(record, "id", "chat_id", "chatId", "rowid")
        if record_id and record_id != chat_id:
            raise ValueError("chat lookup returned a different conversation")
        try:
            return _target_from_record(record, fallback_chat_id=chat_id)
        except ValueError:
            records = await client.chats(limit=1000)
            for candidate in records:
                candidate_id = _record_value(
                    candidate, "id", "chat_id", "chatId", "rowid"
                )
                if candidate_id == chat_id:
                    return _target_from_record(candidate, fallback_chat_id=chat_id)
            raise ValueError("target chat rowid does not resolve to an existing conversation")

    records = await client.chats(limit=1000)
    if key == "chat_guid":
        for record in records:
            if _record_value(record, "guid", "chat_guid", "chatGuid") == value:
                return _target_from_record(record)
        if action in _DIRECT_SYNTHESIS_ACTIONS:
            return ResolvedTarget(chat_id=None, chat_guid=value)
        raise ValueError("chat_guid does not resolve to an existing conversation")

    if key == "chat_identifier":
        for record in records:
            if _record_matches_identifier(record, value):
                return _target_from_record(record)
        raise ValueError("chat_identifier does not resolve to an existing conversation")

    target_flag, handle, service = cli.parse_send_target(value, is_phone=True)
    if target_flag != "--to":
        raise ValueError("to must be a direct phone or email target")
    normalized_handle = handle.casefold()
    for record in records:
        if normalized_handle in _record_handles(record):
            target = _target_from_record(record)
            return ResolvedTarget(
                chat_id=target.chat_id,
                chat_guid=target.chat_guid,
                chat_identifier=target.chat_identifier,
                to=handle,
                is_group=target.is_group,
            )
    if action not in _DIRECT_SYNTHESIS_ACTIONS:
        raise ValueError("to does not resolve to an existing conversation")
    service_name = "SMS" if service == "sms" else "iMessage"
    return ResolvedTarget(
        chat_id=None,
        chat_guid=f"{service_name};-;{handle}",
        to=handle,
        is_group=False,
    )


async def resolve_message_record(
    client: ImsgClient,
    target: ResolvedTarget,
    message_id: str,
    *,
    require_from_me: bool = False,
) -> dict[str, Any]:
    if not target.chat_id:
        raise ValueError("message action requires an existing conversation rowid")
    messages = await client.history(
        target.chat_id, limit=1000, attachments=False
    )
    for message in messages:
        guid = _record_value(message, "guid", "message_guid", "messageGuid")
        if guid != message_id:
            continue
        if require_from_me:
            from_me = message.get("is_from_me")
            if from_me is None:
                from_me = message.get("isFromMe")
            if from_me is not True:
                raise ValueError("message action requires a bot-authored message")
        return message
    raise ValueError(
        "message identifier does not belong to the current conversation"
    )


async def resolve_message_reference(
    client: ImsgClient,
    target: ResolvedTarget,
    message_id: str,
    *,
    require_from_me: bool = False,
) -> str:
    message = await resolve_message_record(
        client,
        target,
        message_id,
        require_from_me=require_from_me,
    )
    return _record_value(message, "guid", "message_guid", "messageGuid") or message_id


def _current_bot_tapback(message: dict[str, Any]) -> str:
    reactions = message.get("reactions")
    if not isinstance(reactions, list):
        reactions = []
    current: list[str] = []
    for reaction in reactions:
        if not isinstance(reaction, dict):
            continue
        from_me = reaction.get("is_from_me")
        if from_me is None:
            from_me = reaction.get("isFromMe")
        if from_me is not True:
            continue
        kind = _record_value(reaction, "type", "reaction_type", "reactionType")
        if kind:
            current.append(kind)
    if len(current) != 1:
        raise ValueError(
            "reaction removal without a name requires exactly one current "
            "bot-authored tapback"
        )
    return current[0]


def _action_tool_client() -> ImsgClient:
    binary = resolve_binary({"binary": os.getenv(ENV_BINARY)})
    try:
        timeout = max(0.1, float(os.getenv(ENV_COMMAND_TIMEOUT) or 30.0))
    except (TypeError, ValueError):
        timeout = 30.0
    return ImsgClient(binary, timeout=timeout)


def _tool_caller_context() -> tuple[str, str, str]:
    """Return the task-local platform, source, and authenticated user ID."""
    try:
        from gateway.session_context import get_session_env

        return (
            str(get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip(),
            str(get_session_env("HERMES_SESSION_SOURCE", "") or "").strip(),
            str(get_session_env("HERMES_SESSION_USER_ID", "") or "").strip(),
        )
    except Exception:
        platform = str(os.getenv("HERMES_SESSION_PLATFORM") or "").strip()
        source = str(os.getenv("HERMES_SESSION_SOURCE") or "").strip()
        user_id = str(os.getenv("HERMES_SESSION_USER_ID") or "").strip()
        return platform, source, user_id


def _authorize_group_action(action: str) -> None:
    """Require an explicit owner identity for group mutations from messaging."""
    if action not in _GROUP_ACTIONS:
        return
    platform, source, user_id = _tool_caller_context()
    surface = (platform or source).casefold()
    if surface in _LOCAL_OPERATOR_SURFACES:
        return
    configured = os.getenv(ENV_GROUP_ACTION_ADMINS)
    if not configured:
        configured = os.getenv(ENV_ALLOWED_USERS)
    admins = {value.casefold() for value in _csv_set(configured)}
    if not user_id or user_id.casefold() not in admins:
        raise ValueError(
            "group mutations require a local operator or a configured "
            "group action admin"
        )


async def _dispatch_action(
    client: ImsgClient,
    action: str,
    target: ResolvedTarget,
    values: dict[str, Any],
) -> dict[str, Any]:
    if action in _GROUP_ACTIONS and target.is_group is not True:
        raise ValueError(f"{action} requires an exact group conversation target")

    if action in _MESSAGE_ACTIONS:
        message = await resolve_message_record(
            client,
            target,
            values["message_id"],
            require_from_me=action in {"edit", "unsend"},
        )
        message_id = (
            _record_value(message, "guid", "message_guid", "messageGuid")
            or values["message_id"]
        )
    else:
        message = {}
        message_id = ""

    if action == "react":
        reaction = values["reaction"] or _current_bot_tapback(message)
        return await client.tapback(
            target.chat_guid,
            message_id,
            reaction,
            remove=values["remove"],
            part_index=values["part_index"],
        )
    if action == "edit":
        return await client.edit(
            target.chat_guid,
            message_id,
            values["text"],
            backwards_compatibility_message=values[
                "backwards_compatibility_message"
            ],
            part_index=values["part_index"],
        )
    if action == "unsend":
        return await client.unsend(
            target.chat_guid, message_id, part_index=values["part_index"]
        )
    if action == "reply":
        return await client.send_rich(
            target.chat_guid,
            values["text"],
            reply_to=message_id,
            file=values["file_path"],
            part_index=values["part_index"],
        )
    if action == "sendWithEffect":
        return await client.send_rich(
            target.chat_guid,
            values["text"],
            effect=values["effect"],
            part_index=values["part_index"],
        )
    if action == "upload-file":
        return await client.send_attachment(
            target.chat_guid,
            values["file_path"],
            as_voice=values["as_voice"],
        )
    if action == "renameGroup":
        return await client.rename_group(target.chat_guid, values["display_name"])
    if action == "setGroupIcon":
        return await client.set_group_icon(target.chat_guid, values["file_path"])
    if action == "addParticipant":
        return await client.add_participant(target.chat_guid, values["address"])
    if action == "removeParticipant":
        return await client.remove_participant(target.chat_guid, values["address"])
    if action == "leaveGroup":
        return await client.leave_group(target.chat_guid)
    if action == "poll":
        if values["reply_to"]:
            values["reply_to"] = await resolve_message_reference(
                client, target, values["reply_to"]
            )
        return await client.send_poll(
            target.chat_id or "",
            values["question"],
            values["options"],
            comment=values["comment"],
            reply_to=values["reply_to"],
            chat_guid=target.chat_guid,
        )
    if action in {"poll-vote", "poll-unvote"}:
        poll_guid = await resolve_message_reference(
            client, target, values["poll_guid"]
        )
        method = client.vote_poll if action == "poll-vote" else client.unvote_poll
        return await method(
            target.chat_id or "",
            poll_guid,
            option_id=values["option_id"],
            option_index=values["option_index"],
            option=values["option"],
            chat_guid=target.chat_guid,
        )
    raise ValueError(f"unsupported native action: {action}")


async def handle_imsg_action_tool(
    args: dict[str, Any], **_kwargs: Any
) -> dict[str, Any]:
    """Resolve and dispatch one native action, returning a stable result shape."""
    payload = args if isinstance(args, dict) else {}
    action = str(payload.get("action") or "").strip()
    if action not in ACTION_NAMES:
        return {
            "success": False,
            "action": action or None,
            "error": "action must be one of: " + ", ".join(ACTION_NAMES),
        }
    try:
        values = _validate_payload(action, payload)
        _authorize_group_action(action)
        client = _action_tool_client()
        target = await resolve_target(client, payload, action)
        result = await _dispatch_action(client, action, target, values)
        return {
            "success": True,
            "action": action,
            "chat_id": target.chat_id,
            "chat_guid": target.chat_guid,
            "chat_identifier": target.chat_identifier,
            "to": target.to,
            "result": result,
        }
    except asyncio.CancelledError:
        raise
    except ImsgCommandError as exc:
        return {
            "success": False,
            "action": action,
            "error": _redact(exc),
            "exit_code": exc.exit_code,
        }
    except Exception as exc:
        return {"success": False, "action": action, "error": _redact(exc)}


async def handle_imsg_poll_tool(
    args: dict[str, Any], **kwargs: Any
) -> dict[str, Any]:
    """Map the compatibility poll vocabulary through unified dispatch."""
    payload = dict(args) if isinstance(args, dict) else {}
    legacy_action = str(payload.get("action") or "").strip().lower()
    mapped_action = {
        "send": "poll",
        "vote": "poll-vote",
        "unvote": "poll-unvote",
    }.get(legacy_action)
    if mapped_action is None:
        return {
            "success": False,
            "action": legacy_action or None,
            "error": "action must be send, vote, or unvote",
        }
    payload["action"] = mapped_action
    result = await handle_imsg_action_tool(payload, **kwargs)
    result["native_action"] = mapped_action
    result["action"] = legacy_action
    return result
