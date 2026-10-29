"""Native iMessage/SMS platform adapter for Hermes, backed by the local
``imsg`` CLI, packaged as a standalone user plugin (platform name ``imsg``).

The safety property this adapter exists to preserve is simple: inbound replies
must route back to the exact Messages chat rowid that produced the inbound
message.  No GUID resolution for routing, no participant matching, no
cross-chat guessing.

This module registers the platform via the stock plugin API and therefore does
**not** require ``Platform.IMSG`` to exist in Hermes core; the platform name is
resolved through ``Platform("imsg")`` which upstream's ``Platform._missing_``
turns into a dynamic member once the plugin has registered itself in the
platform registry.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from collections import OrderedDict, deque
from typing import Any, Optional

from gateway.config import PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

from . import cli
from .actions import (
    IMSG_ACTION_TOOL_SCHEMA,
    IMSG_POLL_TOOL_SCHEMA,
    handle_imsg_action_tool,
    handle_imsg_poll_tool,
)
from .attachments import validate_outbound_attachment_path
from .client import ImsgClient, ImsgCommandError
from .config import (
    DEFAULT_DEBOUNCE,
    ENV_ALLOW_ALL_USERS,
    ENV_ALLOWED_CHATS,
    ENV_ALLOWED_USERS,
    ENV_AUTO_LAUNCH,
    ENV_BINARY,
    ENV_COMMAND_TIMEOUT,
    ENV_DEBOUNCE,
    ENV_FALLBACK_ACTION,
    ENV_GROUP_ALLOWED_USERS,
    ENV_GROUP_SILENCE,
    ENV_HOME_CHANNEL,
    ENV_MENTION_PATTERNS,
    ENV_PARTICIPANT_CACHE_TTL,
    ENV_PARTICIPANT_VALIDATION,
    ENV_POLL_UNVOTES,
    ENV_POLL_VOTES_DM,
    ENV_POLL_VOTES_GROUP,
    ENV_REACTION_NOTIFICATIONS,
    ENV_REQUIRE_MENTION,
    ENV_SEND_READ_RECEIPTS,
    INSTALL_HINT,
    MAX_TEXT_LENGTH,
    PLATFORM_LABEL,
    PLATFORM_NAME,
    apply_yaml_config_fn,
    check_imsg_requirements,
    env_enablement_fn,
    get_state_path,
    normalize_imsg_target,
    resolve_binary,
)
from .rules import (
    _VALID_FALLBACKS,
    _VALID_VALIDATIONS,
    ImsgRule,
    _as_bool,
    _csv_set,
    _redact,
    parse_imsg_rules,
)

logger = logging.getLogger(__name__)

# Hermes added ``MessageEvent.reply_expected`` in late September 2026; older
# cores reject the keyword, so it is passed only when the field exists.
_EVENT_TAKES_REPLY_EXPECTED = dataclasses.is_dataclass(MessageEvent) and any(
    f.name == "reply_expected" for f in dataclasses.fields(MessageEvent)
)

PLATFORM_HINT = (
    "You are chatting via iMessage/SMS. Supported emphasis markup is converted "
    "to native Messages formatting; avoid complex Markdown layouts. Keep "
    "responses concise and conversational. In "
    "group chats, only reply when addressed. Reply routing is bound to the "
    "originating chat; never try to redirect a reply to a different chat."
)


# ---------------------------------------------------------------------------
# Event field accessors (imsg watch JSON is intentionally permissive)
# ---------------------------------------------------------------------------

def _event_get(event: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in event and event.get(name) is not None:
            return event.get(name)
    return default


def _event_rowid(event: dict[str, Any]) -> Optional[int]:
    value = _event_get(event, "id", "rowid", "message_id")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_chat_id(event: dict[str, Any]) -> Optional[str]:
    value = _event_get(event, "chat_id", "chat_rowid", "chatRowid")
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    try:
        if int(text) <= 0:
            return None
    except ValueError:
        # The upstream schema promises an integer rowid.  Non-numeric values
        # are retained only for backward compatibility with older fixtures.
        pass
    return text


def _event_sender(event: dict[str, Any]) -> str:
    value = _event_get(event, "sender", "from", "handle", "handle_id", "sender_handle", default="")
    return str(value or "").strip()


def _event_text(event: dict[str, Any]) -> str:
    value = _event_get(event, "text", "body", "message", "message_text", default="")
    return str(value or "")


def _event_guid(event: dict[str, Any]) -> Optional[str]:
    value = _event_get(event, "guid", "message_guid", "messageGuid")
    return str(value) if value else None


def _event_chat_guid(event: dict[str, Any]) -> Optional[str]:
    value = _event_get(event, "chat_guid", "chatGuid", "guid_chat")
    return str(value) if value else None


def _event_is_group(event: dict[str, Any]) -> bool:
    value = _event_get(event, "is_group", "group", "isGroup")
    if value is not None:
        return _as_bool(value)
    participants = event.get("participants")
    return isinstance(participants, list) and len(participants) > 1


def _attachment_mime(att: Any) -> str:
    if not isinstance(att, dict):
        return ""
    return str(
        att.get("mime_type")
        or att.get("mimeType")
        or att.get("uti")
        or att.get("type")
        or ""
    ).lower()


def _attachment_path(att: Any) -> Optional[str]:
    """Return a resolvable filesystem path for an inbound attachment, or ``None``.

    Only keys that upstream documents as resolvable filesystem locations are
    used: the converted-cache path (preferred, matching Hermes' converted-
    attachment consumption) and the original ``path``/``cached_path``.
    ``filename``/``file`` are display names only; upstream does not promise
    they resolve to anything on disk relative to any particular directory,
    so they are intentionally never expanded into a path.  Treating a bare
    filename as if it were a path risked handing Hermes a nonexistent or,
    worse, an unintentionally relative path.  Missing/unresolvable
    attachments are omitted from ``media_urls`` the same way a truly
    ``missing=true`` attachment is; the raw attachment object is preserved in
    ``raw_message`` regardless.
    """
    if not isinstance(att, dict):
        return None
    if _as_bool(att.get("missing"), False):
        return None
    value = (
        att.get("converted_path")
        or att.get("convertedPath")
        or att.get("path")
        or att.get("cached_path")
    )
    if value:
        return str(value)
    return None


def _poll_event_kind(event: dict[str, Any]) -> Optional[str]:
    payload = event.get("poll")
    if not isinstance(payload, dict):
        return None
    event_name = str(payload.get("event") or "").lower()
    kind = str(payload.get("kind") or "").lower()
    vote = payload.get("vote")
    vote_type = str(vote.get("event_type") or "").lower() if isinstance(vote, dict) else ""
    if kind == "unvote" or event_name.endswith(".unvoted") or vote_type in {"removed", "deselected"}:
        return "unvote"
    if kind == "vote" or event_name.endswith(".voted") or vote_type == "selected":
        return "vote"
    if kind == "created" or event_name.endswith(".created"):
        return "created"
    return None


def _format_poll_event(event: dict[str, Any], kind: str) -> str:
    payload_raw = event.get("poll")
    payload = payload_raw if isinstance(payload_raw, dict) else {}
    vote_raw = payload.get("vote")
    vote = vote_raw if isinstance(vote_raw, dict) else {}
    if not vote and isinstance(payload.get("votes"), list) and payload["votes"]:
        first_vote = payload["votes"][0]
        vote = first_vote if isinstance(first_vote, dict) else {}
    option = str(vote.get("option_text") or vote.get("optionText") or "").strip()
    question = str(payload.get("question") or payload.get("poll_question") or "").strip()
    poll_guid = str(payload.get("poll_guid") or payload.get("original_guid") or "").strip()
    target = f' on "{question}"' if question else (f" on poll {poll_guid}" if poll_guid else "")
    if kind == "unvote":
        return f"Poll vote removed: {option or 'an option'}{target}"
    return f"Poll vote: {option or 'an option'}{target}"


def _format_created_poll(event: dict[str, Any]) -> Optional[str]:
    payload = event.get("poll")
    if not isinstance(payload, dict):
        return None
    options = payload.get("options")
    if not isinstance(options, list) or not options:
        return None
    votes = payload.get("votes")
    tallies: dict[str, int] = {}
    if isinstance(votes, list):
        for vote in votes:
            if not isinstance(vote, dict):
                continue
            if str(vote.get("event_type") or "").lower() == "removed":
                continue
            option_id = str(vote.get("option_id") or "").strip()
            if option_id:
                tallies[option_id] = tallies.get(option_id, 0) + 1
    lines: list[str] = []
    question = str(payload.get("question") or "").strip()
    lines.append(f"Poll: {question}" if question else "Poll")
    for index, option in enumerate(options, start=1):
        if not isinstance(option, dict):
            continue
        text = str(option.get("text") or option.get("label") or "").strip()
        if not text:
            continue
        option_id = str(option.get("id") or option.get("option_id") or "").strip()
        raw_count = option.get("vote_count", option.get("count"))
        try:
            count = int(raw_count) if raw_count is not None else tallies.get(option_id, 0)
        except (TypeError, ValueError):
            count = tallies.get(option_id, 0)
        suffix = f" ({count} {'vote' if count == 1 else 'votes'})"
        lines.append(f"{index}. {text}{suffix}")
    poll_guid = str(
        payload.get("poll_guid")
        or payload.get("original_guid")
        or _event_guid(event)
        or ""
    ).strip()
    if poll_guid:
        lines.append(f"Poll ID: {poll_guid}")
    return "\n".join(lines) if len(lines) > 1 else None


_REACTION_LABELS = {
    "love": "Love",
    "heart": "Love",
    "❤️": "Love",
    "like": "Like",
    "👍": "Like",
    "dislike": "Dislike",
    "👎": "Dislike",
    "laugh": "Laugh",
    "laughed": "Laugh",
    "😂": "Laugh",
    "emphasis": "Emphasis",
    "emphasize": "Emphasis",
    "‼️": "Emphasis",
    "question": "Question",
    "❓": "Question",
}


def _is_reaction_event(event: dict[str, Any]) -> bool:
    if _as_bool(_event_get(event, "is_reaction", "isReaction", "is_tapback"), False):
        return True
    value = _event_get(event, "associated_message_type", "associatedMessageType")
    try:
        associated_type = int(value)
    except (TypeError, ValueError):
        return False
    return 2000 <= associated_type < 4000


def _reaction_context(event: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not _is_reaction_event(event):
        return None
    raw_guid = str(
        _event_get(
            event,
            "reacted_to_guid",
            "reactedToGuid",
            "associated_message_guid",
            "associatedMessageGuid",
            default="",
        )
        or ""
    ).strip()
    target_guid = re.sub(r"^p(?::\d+)?/", "", raw_guid, flags=re.IGNORECASE)
    reaction_type = str(
        _event_get(event, "reaction_type", "reactionType", default="") or ""
    ).strip()
    emoji = str(
        _event_get(event, "reaction_emoji", "reactionEmoji", default="") or ""
    ).strip()
    label = (
        _REACTION_LABELS.get(reaction_type.lower())
        or _REACTION_LABELS.get(emoji)
        or reaction_type
        or emoji
        or "Reaction"
    )
    return {
        "action": (
            "removed"
            if _event_get(event, "is_reaction_add", "isReactionAdd") is False
            else "added"
        ),
        "label": label,
        "emoji": emoji or None,
        "target_guid": target_guid or None,
        "target_is_from_me": _as_bool(
            _event_get(
                event,
                "reacted_to_is_from_me",
                "reactedToIsFromMe",
                "target_is_from_me",
            ),
            False,
        ),
    }


def _resolve_platform():
    """Return ``Platform('imsg')``, registering the entry first if needed.

    On stock/official Hermes the enum has no IMSG member; ``Platform('imsg')``
    resolves only once the plugin is present in the platform registry.  We
    self-register a full entry if construction happens before ``register(ctx)``
    (e.g. direct adapter use in tests).
    """
    from gateway.config import Platform
    try:
        return Platform(PLATFORM_NAME)
    except ValueError:
        _ensure_registered()
        return Platform(PLATFORM_NAME)


class ImsgAdapter(BasePlatformAdapter):
    """Native iMessage/SMS adapter via ``imsg watch`` and ``imsg send``."""

    SUPPORTS_MESSAGE_EDITING = False
    MAX_MESSAGE_LENGTH = MAX_TEXT_LENGTH
    splits_long_messages = True
    supports_code_blocks = False

    def __init__(self, config: PlatformConfig):
        super().__init__(config, _resolve_platform())
        extra = config.extra or {}
        self.binary = resolve_binary(extra)
        self.debounce = str(extra.get("debounce") or os.getenv(ENV_DEBOUNCE) or DEFAULT_DEBOUNCE)
        try:
            self.command_timeout = max(
                0.1,
                float(extra.get("command_timeout") or os.getenv(ENV_COMMAND_TIMEOUT) or 30.0),
            )
        except (TypeError, ValueError):
            self.command_timeout = 30.0
        self.state_file = get_state_path(extra)
        self.allow_all = _as_bool(extra.get("allow_all", os.getenv(ENV_ALLOW_ALL_USERS)), False)
        self.allowed_handles = _csv_set(
            extra.get("allowed_handles")
            or extra.get("allow_from")
            or os.getenv(ENV_ALLOWED_USERS)
        )
        self.allowed_chats = _csv_set(
            extra.get("allowed_chats")
            or os.getenv(ENV_ALLOWED_CHATS)
        )
        self.group_allowed_handles = _csv_set(
            extra.get("group_allowed_handles")
            or extra.get("group_allow_from")
            or os.getenv(ENV_GROUP_ALLOWED_USERS)
        )
        self.require_mention = _as_bool(extra.get("require_mention", os.getenv(ENV_REQUIRE_MENTION)), False)
        self.mention_patterns = self._compile_mention_patterns(
            extra.get("mention_patterns") or os.getenv(ENV_MENTION_PATTERNS)
        )
        # A group message that neither names the bot nor replies to it may end
        # with the agent staying silent; off keeps Hermes' visible fallback.
        self.group_silence = _as_bool(
            extra.get("group_silence", os.getenv(ENV_GROUP_SILENCE)), True
        )
        # IMCore advanced features (typing indicators, read receipts) require
        # Messages.app to be launched with imsg's injected bridge dylib.  Re-arm
        # the bridge on connect by default.
        self.auto_launch = _as_bool(extra.get("auto_launch", os.getenv(ENV_AUTO_LAUNCH)), True)
        self.send_read_receipts = _as_bool(
            extra.get("send_read_receipts", os.getenv(ENV_SEND_READ_RECEIPTS)), True
        )
        reaction_notifications = str(
            extra.get("reaction_notifications")
            or os.getenv(ENV_REACTION_NOTIFICATIONS)
            or "off"
        ).strip().lower()
        self.reaction_notifications = (
            reaction_notifications
            if reaction_notifications in {"own", "all", "off"}
            else "off"
        )
        poll_events = extra.get("poll_vote_events")
        poll_events = poll_events if isinstance(poll_events, dict) else {}
        self.poll_votes_dm = _as_bool(
            poll_events.get("dm", extra.get("poll_votes_dm", os.getenv(ENV_POLL_VOTES_DM))),
            False,
        )
        self.poll_votes_group = _as_bool(
            poll_events.get("group", extra.get("poll_votes_group", os.getenv(ENV_POLL_VOTES_GROUP))),
            False,
        )
        self.poll_unvotes = _as_bool(
            poll_events.get(
                "include_unvotes",
                extra.get("poll_unvotes", os.getenv(ENV_POLL_UNVOTES)),
            ),
            False,
        )
        self.poll_vote_chats = _csv_set(poll_events.get("chats"))
        # Rules still gate enabled poll events; these switches only decide
        # whether vote-only rows become agent-visible messages at all.
        self.poll_vote_events = {
            "dm": self.poll_votes_dm,
            "group": self.poll_votes_group,
            "include_unvotes": self.poll_unvotes,
            "chats": sorted(self.poll_vote_chats),
        }
        # Rules engine: declarative access-control layer.
        self.rules: list[ImsgRule] = parse_imsg_rules(extra.get("rules"))
        _fa = str(
            extra.get("fallback_action") or os.getenv(ENV_FALLBACK_ACTION) or "silent_drop"
        ).strip().lower()
        self.fallback_action: str = _fa if _fa in _VALID_FALLBACKS else "silent_drop"
        _pv = str(
            extra.get("participant_validation") or os.getenv(ENV_PARTICIPANT_VALIDATION) or "fast"
        ).strip().lower()
        self.participant_validation: str = _pv if _pv in _VALID_VALIDATIONS else "fast"
        self.participant_cache_ttl: int = int(
            extra.get("participant_cache_ttl") or os.getenv(ENV_PARTICIPANT_CACHE_TTL) or 300
        )
        raw_aliases = extra.get("identity_aliases") or {}
        self.identity_aliases: dict[str, str] = (
            {
                str(handle).strip(): str(name).strip()
                for handle, name in raw_aliases.items()
                if str(handle).strip() and str(name).strip()
            }
            if isinstance(raw_aliases, dict)
            else {}
        )
        # Cache for strict-mode participant lookups: {chat_id: (handles, expires_at)}
        self._participant_cache: dict[str, tuple[set[str], float]] = {}
        self._running = False
        self._watch_task: asyncio.Task | None = None
        self._watch_proc: asyncio.subprocess.Process | None = None
        # Bounded tail of the watch process's stderr (redacted) for reconnect
        # diagnostics.  The stderr pipe is drained concurrently so it can never
        # fill and deadlock the child (which would silently stall stdout).
        self._watch_stderr_tail: deque[str] = deque(maxlen=20)
        # Strong references to best-effort fire-and-forget tasks (deny notices,
        # read receipts) so the event loop cannot GC them mid-flight.
        self._background_tasks: set[asyncio.Task] = set()
        self._last_rowid = self._load_last_rowid()
        self._recent_message_guids: dict[str, str] = {}
        self._recent_chat_guids: dict[str, str] = {}
        self._sent_message_guids: OrderedDict[str, str] = OrderedDict()

    # ------------------------------------------------------------------
    # Best-effort background tasks
    # ------------------------------------------------------------------

    def _spawn_bg(self, coro) -> asyncio.Task:
        """Schedule a best-effort background task, holding a strong reference.

        ``asyncio`` keeps only a weak reference to bare ``create_task`` results,
        so an unreferenced fire-and-forget task (a deny notice, a read receipt)
        can be garbage-collected before it completes.  We retain it in a set and
        drop it, observing any exception, once it finishes.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.debug("[imsg] background task failed: %s", _redact(exc))

        task.add_done_callback(_done)
        return task

    # ------------------------------------------------------------------
    # Mention handling
    # ------------------------------------------------------------------

    @staticmethod
    def _compile_mention_patterns(raw: Any) -> list[re.Pattern]:
        if raw is None:
            patterns: list[Any] = [r"(?<![\w@])@?gordie\b[,:\-]?"]
        elif isinstance(raw, str):
            text = raw.strip()
            try:
                parsed = json.loads(text) if text else []
            except Exception:
                parsed = None
            patterns = parsed if isinstance(parsed, list) else [
                part.strip()
                for line in text.splitlines()
                for part in line.split(",")
            ]
        elif isinstance(raw, list):
            patterns = raw
        else:
            patterns = [raw]
        compiled: list[re.Pattern] = []
        for pattern_index, pattern in enumerate(patterns):
            value = str(pattern).strip()
            if not value:
                continue
            try:
                compiled.append(re.compile(value, re.IGNORECASE))
            except re.error:
                logger.warning(
                    "[imsg] Invalid mention pattern at index %d; ignored",
                    pattern_index,
                )
        return compiled

    def _message_matches_mention(self, text: str) -> bool:
        if not text:
            return False
        return any(pattern.search(text) for pattern in self.mention_patterns)

    def _clean_mention_text(self, text: str) -> str:
        if not text:
            return text
        stripped = text.lstrip()
        for pattern in self.mention_patterns:
            match = pattern.match(stripped)
            if match:
                return stripped[match.end():].lstrip(" ,:-") or text
        return text

    def _reply_expected(
        self,
        event: dict[str, Any],
        *,
        conversational: bool,
        thread_originator: Optional[str],
    ) -> Optional[bool]:
        """``MessageEvent.reply_expected`` for an admitted message.

        Hermes replaces a bare silence marker on a human turn with a visible
        warning unless the adapter says the message wasn't meant for the bot.
        In a group, people talk to each other, so a message is addressed to the
        bot only when it names the bot (``mention_patterns``) or replies to one
        of the bot's own messages. Any other group message, and any Tapback or
        poll vote there, is False so the agent can stay silent. DMs are always
        meant for the bot and stay None, keeping the fallback.
        """
        if not _event_is_group(event):
            return None
        if conversational:
            if self._message_matches_mention(_event_text(event)):
                return True
            if thread_originator and thread_originator in self._sent_message_guids:
                return True
        return False if self.group_silence else None

    def _reply_expected_kwargs(self, event: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if not _EVENT_TAKES_REPLY_EXPECTED:
            return {}
        return {"reply_expected": self._reply_expected(event, **kwargs)}

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_last_rowid(self) -> Optional[int]:
        try:
            if not self.state_file.exists():
                return None
            data = json.loads(self.state_file.read_text())
            value = data.get("last_rowid")
            return int(value) if value is not None else None
        except Exception as exc:
            logger.debug(
                "[imsg] Failed to load state file %s: %s",
                _redact(str(self.state_file)),
                _redact(exc),
            )
            return None

    def _persist_last_rowid(self) -> None:
        """Persist ``last_rowid`` atomically with owner-only (0600) permissions.

        The cursor file lives under the Hermes home and its path/contents are
        not secret, but it is still local state written by an automation
        process; it is written to a same-directory temp file and
        ``os.replace()``d into place (atomic on the same filesystem, with no
        reader ever observes a partially written file) with restrictive
        permissions set before the rename so the final file is never briefly
        group/world readable.
        """
        if self._last_rowid is None:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"last_rowid": self._last_rowid, "updated_at": time.time()})
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self.state_file.parent),
                prefix=f".{self.state_file.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(tmp_name, 0o600)
                os.replace(tmp_name, self.state_file)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception as exc:
            logger.debug(
                "[imsg] Failed to persist state file %s: %s",
                _redact(str(self.state_file)),
                _redact(exc),
            )

    def _remember_event_ids(self, event: dict[str, Any]) -> None:
        chat_id = _event_chat_id(event)
        if not chat_id:
            return
        guid = None if _is_reaction_event(event) else _event_guid(event)
        chat_guid = _event_chat_guid(event)
        if guid:
            self._recent_message_guids[chat_id] = guid
        if chat_guid:
            self._recent_chat_guids[chat_id] = chat_guid

    def _remember_sent_message(self, chat_id: str, message_id: Optional[str]) -> None:
        if not message_id:
            return
        key = str(message_id)
        self._sent_message_guids[key] = str(chat_id)
        self._sent_message_guids.move_to_end(key)
        while len(self._sent_message_guids) > 512:
            self._sent_message_guids.popitem(last=False)

    async def _remember_reaction_target_ownership(
        self, event: dict[str, Any]
    ) -> None:
        if self.reaction_notifications != "own":
            return
        reaction = _reaction_context(event)
        if not reaction:
            return
        target_guid = str(reaction.get("target_guid") or "")
        chat_id = _event_chat_id(event)
        if not target_guid or not chat_id:
            return
        if reaction.get("target_is_from_me"):
            self._remember_sent_message(chat_id, target_guid)
            return
        if target_guid in self._sent_message_guids:
            return
        try:
            messages = await self.get_history(
                chat_id,
                limit=1000,
                attachments=False,
                convert_attachments=False,
            )
        except Exception as exc:
            logger.debug(
                "[imsg] reaction target ownership lookup failed: %s",
                _redact(exc),
            )
            return
        for message in messages:
            message_guid = _event_guid(message)
            if message_guid != target_guid:
                continue
            if _as_bool(
                _event_get(message, "is_from_me", "from_me", "isFromMe"),
                False,
            ):
                self._remember_sent_message(chat_id, target_guid)
            return

    # ------------------------------------------------------------------
    # Access control: legacy flat check and async rules engine
    # ------------------------------------------------------------------

    def _should_process(self, event: dict[str, Any]) -> bool:
        """Sync shim kept for backward-compat / simple tests.

        Production uses ``_process_watch_event`` → ``_should_process_async``.
        """
        if _as_bool(_event_get(event, "is_from_me", "from_me", "isFromMe"), False):
            return False
        chat_id = _event_chat_id(event)
        if not chat_id:
            return False
        sender = _event_sender(event)
        if self.allow_all:
            return True
        is_group = _event_is_group(event)
        if is_group:
            if chat_id in self.allowed_chats:
                return True
            if sender and sender in self.group_allowed_handles:
                return True
        elif sender and sender in self.allowed_handles:
            return True
        return False

    async def _should_process_async(self, event: dict[str, Any]) -> bool:
        """Top-down async rule evaluator.  Called from ``_process_watch_event``.

        Evaluates ``self.rules`` in order; the first matching rule's action is
        executed.  When no rule matches, ``fallback_action`` is applied.  If no
        rules are configured the legacy flat allowlist is auto-synthesised so
        existing configs keep working.
        """
        if _as_bool(_event_get(event, "is_from_me", "from_me", "isFromMe"), False):
            return False
        chat_id = _event_chat_id(event)
        if not chat_id:
            return False

        rules = self.rules if self.rules else self._synthesise_legacy_rules()
        participants = await self._get_participants(chat_id, event)

        for rule in rules:
            if self._evaluate_rule(rule, event, participants):
                return await self._execute_rule_action(rule, event, chat_id)

        return await self._execute_fallback_action(event, chat_id)

    def _synthesise_legacy_rules(self) -> list[ImsgRule]:
        """Convert flat allow_all / allowed_handles / allowed_chats into rules."""
        if self.allow_all:
            return [ImsgRule(name="_legacy_allow_all", action="allow")]

        rules: list[ImsgRule] = []
        if self.allowed_handles:
            rules.append(ImsgRule(
                name="_legacy_dm_handles",
                context="dm",
                senders=set(self.allowed_handles),
                action="allow",
            ))
        if self.group_allowed_handles:
            rules.append(ImsgRule(
                name="_legacy_group_handles",
                context="group",
                senders=set(self.group_allowed_handles),
                action="allow",
            ))
        if self.allowed_chats:
            rules.append(ImsgRule(
                name="_legacy_group_chats",
                context="group",
                chats=set(self.allowed_chats),
                require_mention=self.require_mention,
                action="allow",
            ))
        return rules

    async def _execute_rule_action(
        self, rule: ImsgRule, event: dict[str, Any], chat_id: str
    ) -> bool:
        if rule.action == "allow":
            return True
        if rule.action == "silent_drop":
            logger.debug("[imsg] rule action: silent_drop")
            return False
        if rule.action == "deny":
            logger.debug("[imsg] rule action: deny")
            self._spawn_bg(
                self.send(chat_id, "Sorry, you're not authorized to use this bot.")
            )
            return False
        if rule.action == "prompt":
            # Return True so the gateway pairing middleware handles it.
            return True
        return False

    async def _execute_fallback_action(self, event: dict[str, Any], chat_id: str) -> bool:
        logger.debug(
            "[imsg] no rule matched; fallback=%s",
            self.fallback_action,
        )
        if self.fallback_action == "allow":
            return True
        if self.fallback_action == "deny":
            self._spawn_bg(
                self.send(chat_id, "Sorry, you're not authorized to use this bot.")
            )
            return False
        if self.fallback_action == "prompt":
            return True
        return False  # silent_drop (default)

    # ------------------------------------------------------------------
    # Participant lookup
    # ------------------------------------------------------------------

    async def _get_participants(self, chat_id: str, event: dict[str, Any]) -> set[str]:
        """Return the participant handles for a chat.

        ``fast`` mode reads from the event's ``participants`` field (zero
        latency).  ``strict`` mode issues a live ``imsg group --chat-id`` call
        cached for ``participant_cache_ttl`` seconds.  Current upstream ``chats``
        has no ``--chat-id`` option.
        """
        if self.participant_validation == "fast":
            raw = event.get("participants") or []
            if not isinstance(raw, list):
                raw = [raw]
            # Only accept string handles (matches strict-mode's discipline
            # below).  A dict/list/bool/number entry cannot be a genuine
            # participant handle; silently coercing it via str() risked
            # synthesizing a spoofed-looking handle into rule evaluation.
            return {h.strip() for h in raw if isinstance(h, str) and h.strip()}

        cached = self._participant_cache.get(chat_id)
        if cached is not None:
            participants_set, expires_at = cached
            if time.time() < expires_at:
                return participants_set

        try:
            code, stdout, stderr = await self._run_imsg(cli.build_group_args(chat_id))
        except Exception as exc:
            logger.debug("[imsg] participant lookup failed: %s", _redact(exc))
            return set()

        if code != 0:
            logger.debug(
                "[imsg] participant lookup returned non-zero: %s",
                _redact(stderr or stdout),
            )
            return set()

        try:
            chats_list = cli.parse_json_output(stdout)
            participants: set[str] = set()
            for chat_obj in chats_list:
                for h in (chat_obj.get("participants") or []):
                    if isinstance(h, str) and h.strip():
                        participants.add(h.strip())
        except Exception as exc:
            logger.debug("[imsg] participant lookup JSON parse failed: %s", _redact(exc))
            return set()

        self._participant_cache[chat_id] = (participants, time.time() + self.participant_cache_ttl)
        return participants

    # ------------------------------------------------------------------
    # Rule evaluation (pure sync)
    # ------------------------------------------------------------------

    def _evaluate_rule(
        self,
        rule: ImsgRule,
        event: dict[str, Any],
        participants: set[str],
    ) -> bool:
        """Return True if *event* satisfies every condition in *rule*.

        AND-across-all-set-fields: every configured field must match; ``None``
        fields ("any") always pass.  Participant + mention checks apply only to
        group messages.
        """
        is_group = _event_is_group(event)
        chat_id = str(_event_chat_id(event) or "")
        sender = _event_sender(event)
        text = _event_text(event)

        if rule.context == "dm" and is_group:
            return False
        if rule.context == "group" and not is_group:
            return False

        if rule.senders is not None and sender not in rule.senders:
            return False

        if rule.chats is not None and chat_id not in rule.chats:
            return False

        if rule.require_participants and is_group:
            any_of = rule.require_participants.get("any_of", [])
            all_of = rule.require_participants.get("all_of", [])
            if any_of and not any(h in participants for h in any_of):
                return False
            if all_of and not all(h in participants for h in all_of):
                return False

        if rule.require_mention and is_group:
            if not self._message_matches_mention(text):
                return False

        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _launch_bridge(self) -> None:
        """Inject imsg's bridge dylib into Messages.app so IMCore features work.

        Best-effort: a launch failure never blocks the adapter from connecting.
        """
        if not self.auto_launch:
            return
        try:
            code, stdout, stderr = await self._run_imsg(cli.build_launch_args())
            if code == 0:
                logger.info("[imsg] Messages.app bridge armed via `imsg launch`")
                await asyncio.sleep(2)
            else:
                logger.warning(
                    "[imsg] `imsg launch` failed; typing/read receipts may be unavailable: %s",
                    _redact(stderr or stdout or f"imsg exited {code}"),
                )
        except Exception as exc:
            logger.warning("[imsg] `imsg launch` raised (non-fatal): %s", _redact(exc))

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        if not check_imsg_requirements(self.binary):
            logger.error(
                "[imsg] imsg CLI is unavailable or not functional at %s", _redact(self.binary)
            )
            return False
        await self._launch_bridge()
        self._running = True
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("[imsg] connected using %s", _redact(self.binary))
        return True

    async def disconnect(self) -> None:
        self._running = False
        task = self._watch_task
        if task:
            task.cancel()
        proc = self._watch_proc
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        # Cancel any in-flight best-effort tasks (deny notices, read receipts).
        for bg in list(self._background_tasks):
            bg.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        self._persist_last_rowid()
        logger.info("[imsg] disconnected")

    async def _handle_watch_line(self, line: str) -> None:
        """Parse and dispatch one decoded ``imsg watch`` NDJSON line.

        Never logs the raw line: it is unparsed message data straight from
        Messages and may contain full message text, handles, or other PII
        that no redaction pattern can be trusted to fully strip. Only the
        length is safe to log on a parse failure.
        """
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("[imsg] non-JSON watch line dropped (%d chars)", len(line))
            return
        if not isinstance(event, dict):
            return
        await self._process_watch_event(event)

    def _build_watch_args(self) -> list[str]:
        """Build the supervised watch command for the configured event policy."""
        return cli.build_watch_args(
            self.debounce,
            self._last_rowid,
            reactions=self.reaction_notifications != "off",
        )

    async def _watch_loop(self) -> None:
        backoff = 1.0
        while self._running:
            cmd = [self.binary, *self._build_watch_args()]
            stderr_task: asyncio.Task | None = None
            started = time.monotonic()
            healthy = False
            try:
                self._watch_stderr_tail.clear()
                self._watch_proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                assert self._watch_proc.stdout is not None
                started = time.monotonic()
                # Drain stderr concurrently: a long-lived `imsg watch` spawned
                # with stderr=PIPE will block the child (and thus stall stdout /
                # all inbound delivery) if that pipe is never read.
                stderr_task = asyncio.create_task(
                    self._drain_watch_stderr(self._watch_proc)
                )
                async for raw in self._watch_proc.stdout:
                    if not self._running:
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    # Reset the reconnect backoff only once the stream has
                    # proven healthy by delivering a real line, not merely on
                    # spawn success, so a start-then-exit crash loop still backs
                    # off exponentially instead of respawning every second.
                    if not healthy:
                        healthy = True
                        backoff = 1.0
                    await self._handle_watch_line(line)
                if self._running:
                    logger.warning(
                        "[imsg] watch stream ended after %.1fs, reconnecting%s",
                        time.monotonic() - started,
                        self._stderr_tail_suffix(),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._running:
                    logger.warning(
                        "[imsg] watch loop error, reconnecting: %s%s",
                        _redact(exc),
                        self._stderr_tail_suffix(),
                    )
            finally:
                proc = self._watch_proc
                self._watch_proc = None
                if proc and proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                if stderr_task:
                    stderr_task.cancel()
                    try:
                        await stderr_task
                    except (asyncio.CancelledError, Exception):
                        pass
            if self._running:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _stderr_tail_suffix(self) -> str:
        """Return a redacted stderr tail suffix for reconnect logs (or '')."""
        if not self._watch_stderr_tail:
            return ""
        return " | stderr: " + " ⏎ ".join(list(self._watch_stderr_tail)[-3:])

    async def _drain_watch_stderr(self, proc: asyncio.subprocess.Process) -> None:
        """Continuously consume the watch process's stderr into a bounded tail.

        Keeping the pipe drained is the point: an undrained stderr pipe can fill
        the OS buffer, block ``imsg watch``, and silently stall stdout (and thus
        all inbound message delivery).  We retain only a small redacted tail for
        reconnect diagnostics.
        """
        stderr = proc.stderr
        if stderr is None:
            return
        try:
            async for raw in stderr:
                text = raw.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._watch_stderr_tail.append(_redact(text))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[imsg] watch stderr drain ended: %s", _redact(exc))

    async def _process_watch_event(self, event: dict[str, Any]) -> None:
        rowid = _event_rowid(event)
        if rowid is not None and (self._last_rowid is None or rowid > self._last_rowid):
            self._last_rowid = rowid
            self._persist_last_rowid()
        if self.reaction_notifications == "off" and _is_reaction_event(event):
            return
        self._remember_event_ids(event)
        if not await self._should_process_async(event):
            return
        await self._remember_reaction_target_ownership(event)
        # Mark the chat read (fire-and-forget) so a slow IMCore read never
        # delays message handling.
        chat_id = _event_chat_id(event)
        if self.send_read_receipts and chat_id:
            self._spawn_bg(self.mark_read(chat_id))
        message_event = self._to_message_event(event)
        if message_event is None:
            return
        await self.handle_message(message_event)

    def _poll_event_enabled(self, event: dict[str, Any], kind: str) -> bool:
        chat_id = _event_chat_id(event)
        if self.poll_vote_chats and chat_id not in self.poll_vote_chats:
            return False
        if kind == "unvote" and not self.poll_unvotes:
            return False
        return self.poll_votes_group if _event_is_group(event) else self.poll_votes_dm

    def _to_message_event(self, event: dict[str, Any]) -> MessageEvent | None:
        chat_id = _event_chat_id(event)
        if not chat_id:
            return None
        text = _event_text(event)
        metadata: dict[str, Any] = {}
        reaction = _reaction_context(event)
        poll_kind = _poll_event_kind(event)
        if reaction:
            if self.reaction_notifications == "off":
                return None
            target_guid = reaction.get("target_guid")
            target_is_own = bool(
                reaction.get("target_is_from_me")
                or (
                    target_guid
                    and str(target_guid) in self._sent_message_guids
                )
            )
            if self.reaction_notifications == "own" and not target_is_own:
                return None
            target = "your message" if target_is_own else "a message"
            preposition = "from" if reaction["action"] == "removed" else "to"
            text = (
                f"Reaction: {reaction['label']} {reaction['action']} "
                f"{preposition} {target}"
            )
            metadata = {
                "imsg_event_type": "reaction",
                "reaction": reaction,
            }
        elif poll_kind:
            if poll_kind in {"vote", "unvote"} and not self._poll_event_enabled(
                event, poll_kind
            ):
                return None
            if poll_kind == "created":
                text = _format_created_poll(event) or text
            else:
                text = _format_poll_event(event, poll_kind)
            metadata = {
                "imsg_event_type": "poll",
                "poll": event.get("poll"),
            }
        elif self.require_mention and _event_is_group(event):
            text = self._clean_mention_text(text)
        sender = _event_sender(event)
        chat_name = str(_event_get(event, "chat_name", "display_name", "name", default="") or "") or None
        attachments = event.get("attachments") or []
        media_urls: list[str] = []
        media_types: list[str] = []
        msg_type = MessageType.TEXT
        if isinstance(attachments, list):
            for att in attachments:
                path = _attachment_path(att)
                mime = _attachment_mime(att)
                if path:
                    media_urls.append(path)
                    media_types.append(mime or "application/octet-stream")
        if media_types:
            if any(m.startswith("image/") for m in media_types):
                msg_type = MessageType.PHOTO
            elif any(m.startswith("audio/") for m in media_types):
                msg_type = MessageType.VOICE
            elif any(m.startswith("video/") for m in media_types):
                msg_type = MessageType.VIDEO
            else:
                msg_type = MessageType.DOCUMENT
        if not text.strip() and not media_urls:
            return None
        user_name = (
            self.identity_aliases.get(sender)
            or str(_event_get(event, "sender_name", "from_name", default="") or "").strip()
            or sender
            or "iMessage user"
        )
        message_id = _event_guid(event) or (
            str(_event_rowid(event)) if _event_rowid(event) is not None else None
        )
        thread_originator = _event_get(
            event, "thread_originator_guid", "threadOriginatorGuid"
        )
        thread_originator = (
            thread_originator.strip()
            if isinstance(thread_originator, str) and thread_originator.strip()
            else None
        )
        has_reply_context = (
            thread_originator is not None
            and reaction is None
            and poll_kind is None
        )
        reply_to_text = _event_get(event, "reply_to_text", "replyToText")
        reply_to_text = (
            reply_to_text
            if has_reply_context
            and isinstance(reply_to_text, str)
            and reply_to_text.strip()
            else None
        )
        reply_to_author = _event_get(event, "reply_to_sender", "replyToSender")
        reply_to_author = (
            reply_to_author.strip()
            if has_reply_context
            and isinstance(reply_to_author, str)
            and reply_to_author.strip()
            else None
        )
        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type="group" if _event_is_group(event) else "dm",
            user_id=sender or None,
            user_name=user_name,
            chat_id_alt=_event_chat_guid(event),
            message_id=message_id,
        )
        return MessageEvent(
            text=text,
            message_type=msg_type,
            source=source,
            raw_message=event,
            message_id=message_id,
            media_urls=media_urls,
            media_types=media_types,
            reply_to_message_id=thread_originator if has_reply_context else None,
            reply_to_text=reply_to_text,
            reply_to_author_id=reply_to_author,
            metadata=metadata,
            **self._reply_expected_kwargs(
                event,
                conversational=reaction is None and poll_kind in (None, "created"),
                thread_originator=thread_originator,
            ),
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def _run_imsg(self, args: list[str]) -> tuple[int, str, str]:
        try:
            return await asyncio.wait_for(
                cli.run_imsg(self.binary, args), timeout=self.command_timeout
            )
        except asyncio.TimeoutError as exc:
            command = args[0] if args else "command"
            raise TimeoutError(
                f"imsg {command} timed out after {self.command_timeout:g}s"
            ) from exc

    async def _run_json_objects(self, args: list[str]) -> list[dict[str, Any]]:
        """Run a bounded read command and parse its NDJSON object stream."""
        code, stdout, stderr = await self._run_imsg(args)
        if code != 0:
            raise RuntimeError(_redact(stderr or stdout or f"imsg exited {code}"))
        values = cli.parse_json_output(stdout)
        if not all(isinstance(value, dict) for value in values):
            raise cli.ImsgJSONError("imsg returned a non-object JSON value")
        return values

    async def list_chats(self, *, limit: int = 20, unread_only: bool = False) -> list[dict[str, Any]]:
        """List recent chats through the public local-database command."""
        return await self._run_json_objects(cli.build_chats_args(limit, unread_only))

    async def get_history(
        self,
        chat_id: str,
        *,
        limit: int = 50,
        attachments: bool = True,
        convert_attachments: bool = True,
        participants: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return history while preserving upstream message/attachment fields."""
        return await self._run_json_objects(cli.build_history_args(
            chat_id,
            limit=limit,
            attachments=attachments,
            convert_attachments=convert_attachments,
            participants=participants,
            start=start,
            end=end,
        ))

    async def search_messages(
        self, query: str, *, match: str = "contains", limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search the local Messages database through ``imsg search``."""
        return await self._run_json_objects(cli.build_search_args(query, match=match, limit=limit))

    def _client(self) -> ImsgClient:
        async def runner(_binary: str, args: list[str]) -> tuple[int, str, str]:
            return await self._run_imsg(args)

        async def rpc_runner(_binary: str, request: dict[str, Any]) -> tuple[int, str, str]:
            return await self._run_imsg_rpc(request)

        return ImsgClient(
            self.binary,
            runner=runner,
            rpc_runner=rpc_runner,
            timeout=self.command_timeout,
        )

    def _poll_client(self) -> ImsgClient:
        """Compatibility alias for callers that exercise adapter poll methods."""
        return self._client()

    async def _run_imsg_rpc(self, request: dict[str, Any]) -> tuple[int, str, str]:
        return await cli.run_imsg_rpc(self.binary, request)

    async def _poll_result(self, action: str, operation) -> dict[str, Any]:
        """Convert client poll success/failure into an agent-safe result object."""
        try:
            result = await operation
            return {
                "success": True,
                "action": action,
                "chat_id": None,
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

    async def send_poll(
        self,
        chat_id: str,
        question: str,
        options: list[str],
        *,
        comment: Optional[str] = None,
        reply_to: Optional[str] = None,
        chat_guid: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a native poll, bound to a rowid unless a GUID is explicit."""
        result = await self._poll_result("send", self._poll_client().send_poll(
            str(chat_id), question, options, comment=comment, reply_to=reply_to,
            chat_guid=chat_guid,
        ))
        if result.get("success"):
            result["chat_id"] = None if chat_guid else str(chat_id)
            result["chat_guid"] = chat_guid
        return result

    async def vote_poll(
        self,
        chat_id: str,
        poll_guid: str,
        *,
        option_id: Optional[str] = None,
        option_index: Optional[int] = None,
        option: Optional[str] = None,
        chat_guid: Optional[str] = None,
    ) -> dict[str, Any]:
        result = await self._poll_result("vote", self._poll_client().vote_poll(
            str(chat_id), poll_guid, option_id=option_id, option_index=option_index,
            option=option, chat_guid=chat_guid,
        ))
        if result.get("success"):
            result["chat_id"] = None if chat_guid else str(chat_id)
            result["chat_guid"] = chat_guid
        return result

    async def unvote_poll(
        self,
        chat_id: str,
        poll_guid: str,
        *,
        option_id: Optional[str] = None,
        option_index: Optional[int] = None,
        option: Optional[str] = None,
        chat_guid: Optional[str] = None,
    ) -> dict[str, Any]:
        result = await self._poll_result("unvote", self._poll_client().unvote_poll(
            str(chat_id), poll_guid, option_id=option_id, option_index=option_index,
            option=option, chat_guid=chat_guid,
        ))
        if result.get("success"):
            result["chat_id"] = None if chat_guid else str(chat_id)
            result["chat_guid"] = chat_guid
        return result

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        """Send an ordinary response by exact rowid without native threading."""
        del reply_to, metadata
        chunks = self.truncate_message(self.format_message(content), self.MAX_MESSAGE_LENGTH)
        last_message_id = None
        continuations: list[str] = []
        for chunk in chunks:
            result = await self._send_plain(chat_id, chunk)
            if not result.success:
                return result
            if last_message_id:
                continuations.append(last_message_id)
            last_message_id = result.message_id
        return SendResult(
            success=True,
            message_id=last_message_id,
            continuation_message_ids=tuple(continuations),
        )

    def _send_result_from_native(
        self, chat_id: str, result: dict[str, Any]
    ) -> SendResult:
        raw_response = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        message_id = cli.extract_message_id(raw_response)
        self._remember_sent_message(chat_id, message_id)
        return SendResult(
            success=True,
            message_id=message_id,
            raw_response=raw_response,
        )

    async def _send_plain(
        self,
        chat_id: str,
        content: str,
        media_files: Optional[list[tuple[str, bool]]] = None,
    ) -> SendResult:
        files = list(media_files or [])
        if len(files) > 1:
            return SendResult(
                success=False,
                error="imsg send accepts one attachment per invocation",
            )
        file_path = files[0][0] if files else None
        try:
            result = await self._client().send(
                str(chat_id),
                content,
                file=file_path,
            )
        except asyncio.CancelledError:
            raise
        except ImsgCommandError as exc:
            if exc.exit_code == 0 and "returned no JSON object" in str(exc):
                return SendResult(
                    success=False,
                    error="imsg send exited 0 but returned no JSON confirmation object",
                )
            return SendResult(success=False, error=_redact(exc))
        except Exception as exc:
            return SendResult(success=False, error=_redact(exc))
        return self._send_result_from_native(str(chat_id), result)

    @staticmethod
    def _extract_sent_message_id(stdout: str) -> Optional[str]:
        return cli.extract_message_id(stdout)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        text = caption or ""
        del file_name, reply_to, metadata, kwargs
        return await self._send_plain(chat_id, text, media_files=[(file_path, False)])

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self.send_document(chat_id, image_path, caption, None, reply_to, metadata)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        return await self.send_document(chat_id, video_path, caption, None, reply_to, metadata)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        del reply_to, metadata, kwargs
        return await self._send_plain(
            chat_id, caption or "", media_files=[(audio_path, True)]
        )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        duration = metadata.get("duration") if isinstance(metadata, dict) else None
        args = cli.build_typing_args(str(chat_id), duration)
        try:
            code, stdout, stderr = await self._run_imsg(args)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[imsg] typing indicator raised: %s", _redact(exc))
            return
        if code != 0:
            logger.debug("[imsg] typing indicator failed: %s", _redact(stderr or stdout or f"imsg exited {code}"))

    async def mark_read(self, chat_id: str) -> bool:
        """Mark a chat's messages as read.  Best-effort; failures swallowed."""
        try:
            code, stdout, stderr = await self._run_imsg(cli.build_read_args(chat_id))
        except Exception as exc:
            logger.debug("[imsg] mark_read raised: %s", _redact(exc))
            return False
        if code != 0:
            logger.debug(
                "[imsg] mark_read failed: %s",
                _redact(stderr or stdout or f"imsg exited {code}"),
            )
            return False
        return True

    async def add_reaction(self, chat_id: str, emoji: str, message_id: Optional[str] = None) -> dict[str, Any]:
        reaction_map = {
            "❤️": "love", "👍": "like", "👎": "dislike", "😂": "laugh",
            "‼️": "emphasis", "❓": "question",
            "love": "love", "like": "like", "dislike": "dislike", "laugh": "laugh",
            "emphasis": "emphasis", "emphasize": "emphasis", "question": "question",
        }
        reaction = reaction_map.get((emoji or "").strip(), (emoji or "").strip())
        if reaction not in cli.STANDARD_REACTIONS:
            return {
                "error": "imsg public automation supports only standard reactions: "
                + ", ".join(sorted(cli.STANDARD_REACTIONS))
            }
        chat_guid = self._recent_chat_guids.get(str(chat_id))
        target_message = message_id or self._recent_message_guids.get(str(chat_id))
        if chat_guid and target_message:
            args = cli.build_tapback_args(chat_guid, target_message, reaction)
        else:
            args = cli.build_react_args(str(chat_id), reaction)
        code, stdout, stderr = await self._run_imsg(args)
        if code != 0:
            return {"error": _redact(stderr or stdout or f"imsg exited {code}")}
        return {"success": True, "platform": PLATFORM_NAME, "chat_id": str(chat_id)}

    async def remove_reaction(self, chat_id: str, message_id: Optional[str] = None) -> dict[str, Any]:
        return {"error": "imsg does not currently expose reliable tapback removal through this adapter"}

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        code, stdout, stderr = await self._run_imsg(cli.build_group_args(chat_id))
        if code != 0:
            return {"name": str(chat_id), "type": "dm", "error": _redact(stderr or stdout)}
        try:
            values = cli.parse_json_output(stdout)
            data = values[0] if values and isinstance(values[0], dict) else {}
        except Exception:
            data = {}
        is_group = _as_bool(data.get("is_group") or data.get("group"), False)
        return {
            "name": data.get("display_name") or data.get("name") or str(chat_id),
            "type": "group" if is_group else "dm",
            "raw": data,
        }


# ---------------------------------------------------------------------------
# Standalone (out-of-process) send
# ---------------------------------------------------------------------------

async def send_imsg_direct(
    extra: dict[str, Any],
    chat_id: str,
    message: str,
    media_files: Optional[list[tuple[str, bool]]] = None,
) -> dict[str, Any]:
    """Standalone send helper for cron / send_message out-of-gateway delivery.

    Multiple attachments are sent as separate ``imsg send`` invocations (see
    ``_send_plain``/upstream's one-file-per-command contract).  This is
    fail-fast: sending stops at the first failure.  To resolve the ambiguity
    of "some attachments already sent, then one failed", the failure result
    is deterministic and explicit about exactly what happened; it reports
    the failing index, how many attachments were attempted, and the
    already-confirmed message ids of every attachment sent before the
    failure, so a caller can never be left guessing whether a partial send
    occurred.
    """
    adapter = ImsgAdapter(PlatformConfig(enabled=True, extra=extra or {}))
    target, _is_phone = normalize_imsg_target(chat_id)
    if media_files:
        sent: list[dict[str, Any]] = []
        for index, (media_path, is_voice) in enumerate(media_files):
            # Upstream accepts one --file per send.  Avoid duplicating a caption
            # when Hermes supplies several attachments.
            send_result = await adapter._send_plain(
                target,
                message if index == 0 else "",
                media_files=[(media_path, is_voice)],
            )
            if not send_result.success:
                return {
                    "error": send_result.error or "imsg send failed",
                    "platform": PLATFORM_NAME,
                    "chat_id": str(target),
                    "failed_index": index,
                    "attempted_attachments": len(media_files),
                    "succeeded_message_ids": [s["message_id"] for s in sent],
                }
            sent.append({"index": index, "message_id": send_result.message_id})
        return {
            "success": True,
            "platform": PLATFORM_NAME,
            "chat_id": str(target),
            "message_id": sent[0]["message_id"] if sent else None,
            "message_ids": [s["message_id"] for s in sent],
        }
    result = await adapter.send(target, message)
    if not result.success:
        return {"error": result.error or "imsg send failed"}
    return {
        "success": True,
        "platform": PLATFORM_NAME,
        "chat_id": str(target),
        "message_id": result.message_id,
    }


async def _standalone_send(
    pconfig,
    chat_id,
    message,
    *,
    thread_id=None,
    media_files=None,
    force_document=False,
) -> dict[str, Any]:
    """``PlatformEntry.standalone_sender_fn``: deliver without a live adapter.

    Used by ``tools/send_message_tool._send_via_adapter`` when cron/send_message
    runs out-of-process from the gateway.  ``thread_id`` / ``force_document`` are
    not meaningful for iMessage and are ignored.
    """
    extra = dict(getattr(pconfig, "extra", {}) or {})
    binary = resolve_binary(extra)
    if not check_imsg_requirements(binary, probe=False):
        return {
            "error": (
                "imsg standalone send unavailable: requires macOS with a resolvable "
                f"imsg binary. {INSTALL_HINT}"
            )
        }
    # Attachments are supported via `imsg send --file`, but validate up front
    # (existence, regular-file-only, no symlinks) so we never report success
    # for a file that does not exist or is unsafe to send.  `_send_plain`
    # repeats this check as the single authoritative choke point; this
    # pre-check just fails fast before spinning up an adapter.
    if media_files:
        for media_path, _is_voice in media_files:
            attachment_error = validate_outbound_attachment_path(media_path)
            if attachment_error:
                return {"error": attachment_error}
    try:
        return await send_imsg_direct(extra, chat_id, message, media_files=media_files)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("[imsg] standalone send raised: %s", _redact(exc))
        return {"error": f"imsg standalone send failed: {_redact(exc)}"}


# ---------------------------------------------------------------------------
# Requirements / config predicates for the platform registry
# ---------------------------------------------------------------------------

def check_requirements() -> bool:
    """Lightweight no-arg ``check_fn`` for the registry (no CLI probe)."""
    return check_imsg_requirements(os.getenv(ENV_BINARY) or None, probe=False)


def validate_config(config) -> bool:
    """Return True when the platform has enough to attempt a connection."""
    extra = getattr(config, "extra", {}) or {}
    binary = resolve_binary(extra)
    return sys.platform == "darwin" and bool(binary)


def is_connected(config) -> bool:
    """Enablement gate: True only when opted-in AND the binary is available.

    Prevents auto-enabling imsg merely because the binary happens to be
    installed; the user must opt in via ``IMSG_ENABLED`` or by providing an
    explicit ``platforms.imsg`` config (which populates ``extra``).
    """
    if sys.platform != "darwin":
        return False
    extra = getattr(config, "extra", {}) or {}
    binary = extra.get("binary") or os.getenv(ENV_BINARY) or shutil.which("imsg")
    if not binary:
        return False
    if _as_bool(os.getenv("IMSG_ENABLED")):
        return True
    signal_keys = (
        "allow_all", "allowed_handles", "allowed_chats",
        "group_allowed_handles", "rules", "identity_aliases",
    )
    return any(extra.get(k) for k in signal_keys)


# ---------------------------------------------------------------------------
# Platform registry entry + registration
# ---------------------------------------------------------------------------

def build_entry_kwargs() -> dict:
    """Return the full kwargs for ``PlatformEntry`` / ``ctx.register_platform``."""
    return dict(
        name=PLATFORM_NAME,
        label=PLATFORM_LABEL,
        adapter_factory=lambda cfg: ImsgAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=[],
        install_hint=INSTALL_HINT,
        env_enablement_fn=env_enablement_fn,
        apply_yaml_config_fn=apply_yaml_config_fn,
        cron_deliver_env_var=ENV_HOME_CHANNEL,
        standalone_sender_fn=_standalone_send,
        allowed_users_env=ENV_ALLOWED_USERS,
        allow_all_env=ENV_ALLOW_ALL_USERS,
        max_message_length=MAX_TEXT_LENGTH,
        # `pii_safe` is deliberately NOT set (defaults to False): core only
        # hashes/redacts a platform's session chat_id + user_id in the LLM
        # prompt when pii_safe is True, and this plugin's native action tools
        # require the agent to pass exact rowids and message references. A
        # hashed chat_id would silently break native actions for no privacy
        # benefit, since routing
        # already uses the untouched value from SessionSource regardless of
        # what the prompt shows. The action contract depends on the raw id, so
        # opting into that redaction here would not be justified.
        emoji="💬",
        allow_update_command=True,
        platform_hint=PLATFORM_HINT,
    )


def _ensure_registered() -> None:
    """Register a full ``PlatformEntry`` directly if not already present."""
    from gateway.platform_registry import platform_registry, PlatformEntry
    if platform_registry.is_registered(PLATFORM_NAME):
        return
    platform_registry.register(PlatformEntry(source="plugin", **build_entry_kwargs()))


def register(ctx) -> None:
    """Plugin entry point: called by the Hermes plugin system."""
    ctx.register_platform(**build_entry_kwargs())
    ctx.register_tool(
        name="imsg_action",
        toolset="imsg",
        schema=IMSG_ACTION_TOOL_SCHEMA,
        handler=handle_imsg_action_tool,
        check_fn=check_requirements,
        is_async=True,
        description=IMSG_ACTION_TOOL_SCHEMA["description"],
        emoji="💬",
    )
    ctx.register_tool(
        name="imsg_poll",
        toolset="imsg",
        schema=IMSG_POLL_TOOL_SCHEMA,
        handler=handle_imsg_poll_tool,
        check_fn=check_requirements,
        is_async=True,
        description=IMSG_POLL_TOOL_SCHEMA["description"],
        emoji="📊",
    )
