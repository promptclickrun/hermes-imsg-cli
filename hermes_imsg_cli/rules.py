"""Access-control rule engine for the iMessage (imsg) platform plugin.

This module is intentionally free of any ``gateway`` / ``hermes_cli`` imports so
that the rule logic can be imported and unit-tested without a full Hermes
checkout on ``PYTHONPATH``.

Rules are evaluated top-down; the first matching rule wins.  When no rule
matches the adapter's configured ``fallback_action`` is applied.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rule engine constants
# ---------------------------------------------------------------------------

VALID_ACTIONS = frozenset({"allow", "deny", "prompt", "silent_drop"})
VALID_CONTEXTS = frozenset({"dm", "group", "any"})
VALID_VALIDATIONS = frozenset({"fast", "strict"})
VALID_FALLBACKS = frozenset({"silent_drop", "deny", "prompt", "allow"})

# Backwards-friendly private aliases (mirror the dirty-core names).
_VALID_ACTIONS = VALID_ACTIONS
_VALID_CONTEXTS = VALID_CONTEXTS
_VALID_VALIDATIONS = VALID_VALIDATIONS
_VALID_FALLBACKS = VALID_FALLBACKS

_PHONE_RE = re.compile(r"\+?\d{7,15}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# Home directory paths (macOS ``/Users/<name>``, and ``/home/<name>`` for any
# copied-DB/Linux-preview code paths) can identify the operator; strip them
# from any string headed for a log line before it is emitted.
_HOME_PATH_RE = re.compile(r"/Users/[^/\s\"']+|/home/[^/\s\"']+")
# Standard UUIDs (optionally ``p:<n>/``-prefixed, as upstream uses for poll
# option threading) and imsg's ``<service>;<+/->;<identifier>`` chat-GUID
# shape are message/chat identifiers, not phone/email PII, but are still
# stable identifiers that should never appear in a log line.
_GUID_RE = re.compile(
    r"\b(?:p:\d+/)?[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"
)
_CHAT_GUID_RE = re.compile(r"\b[A-Za-z][\w.+-]*;[+-];\S+")


# ---------------------------------------------------------------------------
# Small coercion helpers (shared by the parser and the adapter)
# ---------------------------------------------------------------------------

def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _csv_set(value: Any) -> set[str]:
    """Normalise a comma/newline/list value into a set of trimmed strings."""
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = str(value).replace("\n", ",").split(",")
    return {str(item).strip() for item in raw if str(item).strip()}


def _redact(text: Any) -> str:
    """Redact phone numbers, emails, home paths, and GUID-like identifiers.

    Applied to every string that can reach a log line or an error surfaced to
    the agent: subprocess stderr/stdout, exception text, and watch-loop
    diagnostics.  Order matters — GUID/path patterns are replaced before the
    broader phone-number pattern so a numeric run inside a GUID is not
    partially matched first.
    """
    value = str(text or "")
    value = _HOME_PATH_RE.sub("[REDACTED_PATH]", value)
    value = _CHAT_GUID_RE.sub("[REDACTED_GUID]", value)
    value = _GUID_RE.sub("[REDACTED_GUID]", value)
    value = _PHONE_RE.sub("[REDACTED]", value)
    value = _EMAIL_RE.sub("[REDACTED]", value)
    return value


# ---------------------------------------------------------------------------
# ImsgRule dataclass + parser
# ---------------------------------------------------------------------------

@dataclass
class ImsgRule:
    """A single access-control rule for the iMessage adapter.

    Rules are evaluated top-down; the first match wins.  When no rule matches
    the configured ``fallback_action`` is applied.
    """

    name: str
    action: str                                   # allow | deny | prompt | silent_drop
    context: str = "any"                          # dm | group | any
    senders: Optional[set[str]] = None            # None means "any sender"
    chats: Optional[set[str]] = None              # None means "any chat"
    require_participants: Optional[dict] = None    # {"any_of": [...], "all_of": [...]}
    require_mention: bool = False


def parse_imsg_rules(raw: Any) -> list[ImsgRule]:
    """Parse the ``rules`` list from config into :class:`ImsgRule` objects.

    Accepts the raw value of ``platforms.imsg.extra.rules`` from ``config.yaml``.
    Returns an empty list for ``None`` or ``[]``.  Unknown / invalid field
    values fall back to safe defaults with a warning rather than raising, so a
    misconfigured rule degrades gracefully instead of killing the adapter.
    """
    if not raw or not isinstance(raw, list):
        return []
    rules: list[ImsgRule] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            logger.warning("[imsg] rules[%d] is not a dict, skipping", i)
            continue

        action = str(item.get("action", "silent_drop")).strip().lower()
        if action not in _VALID_ACTIONS:
            logger.warning(
                "[imsg] rules[%d] has unknown action %r, defaulting to silent_drop",
                i, action,
            )
            action = "silent_drop"

        context = str(item.get("context", "any")).strip().lower()
        if context not in _VALID_CONTEXTS:
            logger.warning(
                "[imsg] rules[%d] has unknown context %r, defaulting to any", i, context
            )
            context = "any"

        raw_senders = item.get("senders")
        senders: Optional[set[str]] = None
        if raw_senders is not None and raw_senders != "any":
            senders = {
                str(s).strip()
                for s in (raw_senders if isinstance(raw_senders, list) else [raw_senders])
                if str(s).strip()
            }

        raw_chats = item.get("chats")
        chats: Optional[set[str]] = None
        if raw_chats is not None and raw_chats != "any":
            chats = {
                str(c).strip()
                for c in (raw_chats if isinstance(raw_chats, list) else [raw_chats])
                if str(c).strip()
            }

        rp_raw = item.get("require_participants")
        require_participants: Optional[dict] = None
        if isinstance(rp_raw, dict):
            require_participants = {}
            if "any_of" in rp_raw:
                require_participants["any_of"] = [str(h).strip() for h in rp_raw["any_of"]]
            if "all_of" in rp_raw:
                require_participants["all_of"] = [str(h).strip() for h in rp_raw["all_of"]]

        rules.append(ImsgRule(
            name=str(item.get("name", f"rule_{i}")),
            action=action,
            context=context,
            senders=senders,
            chats=chats,
            require_participants=require_participants,
            require_mention=_as_bool(item.get("require_mention"), False),
        ))
    return rules
