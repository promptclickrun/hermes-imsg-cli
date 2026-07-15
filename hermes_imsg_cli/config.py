"""Configuration + environment helpers for the imsg platform plugin.

Kept import-light: the only Hermes symbol used is ``get_hermes_home`` and it is
imported lazily inside :func:`get_state_path` so this module can be imported for
unit tests without a full Hermes checkout on ``PYTHONPATH``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from .rules import _as_bool

logger = logging.getLogger(__name__)

PLATFORM_NAME = "imsg"
PLATFORM_LABEL = "iMessage"

MAX_TEXT_LENGTH = 4000
DEFAULT_DEBOUNCE = "250ms"
DEFAULT_STATE_FILE = "imsg_state.json"
DEFAULT_BINARY_NAME = "imsg"

# US-centric default; overridable via IMSG_DEFAULT_COUNTRY_CODE.  Only used to
# re-hydrate the leading '+' that core's send_message target parser strips for
# plugin platform names (see normalize_imsg_target / README "Known core gap").
DEFAULT_COUNTRY_CODE = "1"
# All-digit targets with at least this many digits are treated as phone numbers
# (E.164) rather than Messages chat rowids.  Chat rowids are small autoincrement
# integers, so a threshold of 10 cleanly separates the two.
DEFAULT_PHONE_MIN_DIGITS = 10

# ── Environment variable names (public contract; documented in plugin.yaml) ──
ENV_ENABLED = "IMSG_ENABLED"
ENV_BINARY = "IMSG_BINARY"
ENV_HOME_CHANNEL = "IMSG_HOME_CHANNEL"
ENV_HOME_CHANNEL_NAME = "IMSG_HOME_CHANNEL_NAME"
ENV_HOME_CHANNEL_THREAD_ID = "IMSG_HOME_CHANNEL_THREAD_ID"
ENV_ALLOWED_USERS = "IMSG_ALLOWED_USERS"
ENV_GROUP_ALLOWED_USERS = "IMSG_GROUP_ALLOWED_USERS"
ENV_ALLOWED_CHATS = "IMSG_ALLOWED_CHATS"
ENV_ALLOW_ALL_USERS = "IMSG_ALLOW_ALL_USERS"
ENV_REQUIRE_MENTION = "IMSG_REQUIRE_MENTION"
ENV_AUTO_LAUNCH = "IMSG_AUTO_LAUNCH"
ENV_SEND_READ_RECEIPTS = "IMSG_SEND_READ_RECEIPTS"
ENV_FALLBACK_ACTION = "IMSG_FALLBACK_ACTION"
ENV_PARTICIPANT_VALIDATION = "IMSG_PARTICIPANT_VALIDATION"
ENV_PARTICIPANT_CACHE_TTL = "IMSG_PARTICIPANT_CACHE_TTL"
ENV_DEBOUNCE = "IMSG_DEBOUNCE"
ENV_STATE_FILE = "IMSG_STATE_FILE"
ENV_MENTION_PATTERNS = "IMSG_MENTION_PATTERNS"
ENV_PHONE_MIN_DIGITS = "IMSG_PHONE_MIN_DIGITS"
ENV_DEFAULT_COUNTRY_CODE = "IMSG_DEFAULT_COUNTRY_CODE"
ENV_COMMAND_TIMEOUT = "IMSG_COMMAND_TIMEOUT"
ENV_POLL_VOTES_DM = "IMSG_POLL_VOTES_DM"
ENV_POLL_VOTES_GROUP = "IMSG_POLL_VOTES_GROUP"
ENV_POLL_UNVOTES = "IMSG_POLL_UNVOTES"

INSTALL_HINT = (
    "Install the `imsg` CLI (`brew install steipete/tap/imsg`) on macOS and grant your "
    "terminal/agent Full Disk Access + Automation (Messages) permission. "
    "The plugin never grants permissions automatically."
)

_ALL_DIGITS_RE = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Binary resolution + requirements
# ---------------------------------------------------------------------------

def resolve_binary(extra: Optional[dict] = None) -> str:
    """Resolve the imsg binary path from extra → env → PATH → bare name."""
    extra = extra or {}
    return str(
        extra.get("binary")
        or os.getenv(ENV_BINARY)
        or shutil.which(DEFAULT_BINARY_NAME)
        or DEFAULT_BINARY_NAME
    )


def check_imsg_requirements(binary: str | None = None, *, probe: bool = True) -> bool:
    """Return True when the local ``imsg`` CLI is available enough to run.

    ``probe=False`` performs only the cheap checks (macOS + binary resolvable);
    ``probe=True`` additionally runs ``imsg status --json`` (5s timeout) to
    confirm the CLI is functional.  Never attempts to grant permissions.
    """
    if sys.platform != "darwin":
        return False
    resolved = binary or shutil.which(DEFAULT_BINARY_NAME)
    if not resolved:
        return False
    if not probe:
        return True
    try:
        result = subprocess.run(
            [resolved, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout or "{}")
    except Exception:
        # Non-JSON but zero exit — treat as available.
        return True
    return bool(
        data.get("basic_features")
        or data.get("basicFeatures")
        or data.get("message")
        or data.get("version")
    )


def requirements_report(binary: str | None = None) -> dict:
    """Structured requirements check for CLI / status surfaces.

    Returns ``{"ok": bool, "binary": str|None, "reason": str, "install_hint": str}``
    without ever attempting to grant permissions.
    """
    if sys.platform != "darwin":
        return {
            "ok": False,
            "binary": None,
            "reason": "imsg requires macOS (Messages.app + local imsg CLI)",
            "install_hint": INSTALL_HINT,
        }
    resolved = binary or os.getenv(ENV_BINARY) or shutil.which(DEFAULT_BINARY_NAME)
    if not resolved:
        return {
            "ok": False,
            "binary": None,
            "reason": "imsg CLI not found on PATH and IMSG_BINARY unset",
            "install_hint": INSTALL_HINT,
        }
    ok = check_imsg_requirements(resolved, probe=True)
    return {
        "ok": bool(ok),
        "binary": resolved,
        "reason": "ok" if ok else "imsg CLI present but `imsg status` failed (permissions?)",
        "install_hint": "" if ok else INSTALL_HINT,
    }


# ---------------------------------------------------------------------------
# State file (profile-aware via get_hermes_home)
# ---------------------------------------------------------------------------

def get_state_path(extra: Optional[dict] = None) -> Path:
    """Resolve the rowid-state file path, profile-aware under the Hermes home.

    Precedence: ``extra['state_file']`` → ``IMSG_STATE_FILE`` → ``<hermes_home>/
    imsg_state.json``.  ``get_hermes_home`` is imported lazily so this module
    stays importable without Hermes; a hard fallback to ``~/.hermes`` is used
    only if that import fails entirely.
    """
    extra = extra or {}
    override = extra.get("state_file") or os.getenv(ENV_STATE_FILE)
    if override:
        return Path(override).expanduser()
    try:
        from hermes_cli.config import get_hermes_home  # lazy: Hermes-only
        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.getenv("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / DEFAULT_STATE_FILE


# ---------------------------------------------------------------------------
# E.164 target normalization (works around core's '+'-stripping gap)
# ---------------------------------------------------------------------------

def normalize_imsg_target(
    raw: Any,
    *,
    phone_min_digits: Optional[int] = None,
    default_country_code: Optional[str] = None,
) -> tuple[str, bool]:
    """Normalise a send target and classify it as phone vs Messages chat rowid.

    Returns ``(target, is_phone)`` where ``is_phone`` selects ``imsg send --to``
    (E.164 / email handle) vs ``imsg send --chat-id`` (Messages chat rowid).

    Rationale for the ``+`` re-hydration: stock Hermes core's
    ``send_message`` target parser (``tools/send_message_tool._parse_target_ref``)
    only preserves a leading ``+`` for platform names in ``_PHONE_PLATFORMS``,
    which upstream does NOT include ``imsg``.  So a cron/send_message target of
    ``imsg:+12025550105`` never reaches the plugin, while ``imsg:12025550105``
    arrives here as the bare digit string.  We re-add the ``+`` for all-digit
    targets that are long enough to be phone numbers, leaving small chat rowids
    (the reply-routing key) untouched.  Explicit ``+E.164`` and ``handle@...``
    email targets pass through as phone targets directly.
    """
    if phone_min_digits is None:
        try:
            phone_min_digits = int(os.getenv(ENV_PHONE_MIN_DIGITS) or DEFAULT_PHONE_MIN_DIGITS)
        except ValueError:
            phone_min_digits = DEFAULT_PHONE_MIN_DIGITS
    if default_country_code is None:
        default_country_code = (
            os.getenv(ENV_DEFAULT_COUNTRY_CODE) or DEFAULT_COUNTRY_CODE
        ).strip().lstrip("+")

    target = str(raw or "").strip()
    if not target:
        return target, False

    # Explicit E.164 or email handle → phone/handle target.
    if target.startswith("+"):
        return target, True
    if "@" in target:
        return target, True

    # All-digit → phone (long) or chat rowid (short).
    if _ALL_DIGITS_RE.match(target):
        if len(target) >= phone_min_digits:
            if len(target) == 10 and default_country_code:
                return f"+{default_country_code}{target}", True
            return f"+{target}", True
        # Short numeric → Messages chat rowid (reply-routing key).
        return target, False

    # Anything else (unexpected) → pass through as chat-id, no phone routing.
    return target, False


# ---------------------------------------------------------------------------
# Plugin config hooks: env_enablement_fn + apply_yaml_config_fn
# ---------------------------------------------------------------------------

def env_enablement_fn() -> Optional[dict]:
    """Seed ``PlatformConfig.extra`` from ``IMSG_*`` env vars.

    Called by core BEFORE adapter construction so ``gateway status`` and
    ``get_connected_platforms()`` reflect env-only configuration.  Returns
    ``None`` (skip auto-enable) unless ``IMSG_ENABLED`` is truthy.  The special
    ``home_channel`` key is turned into a proper ``HomeChannel`` by core.
    """
    if not _as_bool(os.getenv(ENV_ENABLED)):
        return None

    seed: dict[str, Any] = {}

    binary = os.getenv(ENV_BINARY, "").strip()
    if binary:
        seed["binary"] = binary

    # Allowlists are stored as raw CSV strings in extra; the adapter parses
    # them via _csv_set at construction time (matches dirty-core behaviour).
    for env_name, extra_key in (
        (ENV_ALLOWED_USERS, "allowed_handles"),
        (ENV_GROUP_ALLOWED_USERS, "group_allowed_handles"),
        (ENV_ALLOWED_CHATS, "allowed_chats"),
    ):
        value = os.getenv(env_name, "").strip()
        if value:
            seed[extra_key] = value

    if os.getenv(ENV_ALLOW_ALL_USERS, "").strip():
        seed["allow_all"] = _as_bool(os.getenv(ENV_ALLOW_ALL_USERS))
    if os.getenv(ENV_REQUIRE_MENTION, "").strip():
        seed["require_mention"] = _as_bool(os.getenv(ENV_REQUIRE_MENTION))
    if os.getenv(ENV_AUTO_LAUNCH, "").strip():
        seed["auto_launch"] = _as_bool(os.getenv(ENV_AUTO_LAUNCH))
    if os.getenv(ENV_SEND_READ_RECEIPTS, "").strip():
        seed["send_read_receipts"] = _as_bool(os.getenv(ENV_SEND_READ_RECEIPTS))
    for env_name, extra_key in (
        (ENV_POLL_VOTES_DM, "poll_votes_dm"),
        (ENV_POLL_VOTES_GROUP, "poll_votes_group"),
        (ENV_POLL_UNVOTES, "poll_unvotes"),
    ):
        if os.getenv(env_name, "").strip():
            seed[extra_key] = _as_bool(os.getenv(env_name))

    fallback = os.getenv(ENV_FALLBACK_ACTION, "").strip().lower()
    if fallback:
        seed["fallback_action"] = fallback
    validation = os.getenv(ENV_PARTICIPANT_VALIDATION, "").strip().lower()
    if validation:
        seed["participant_validation"] = validation
    ttl = os.getenv(ENV_PARTICIPANT_CACHE_TTL, "").strip()
    if ttl:
        seed["participant_cache_ttl"] = ttl

    home = os.getenv(ENV_HOME_CHANNEL, "").strip()
    if home:
        seed["home_channel"] = {
            "chat_id": home,
            "name": os.getenv(ENV_HOME_CHANNEL_NAME, "Home"),
            "thread_id": os.getenv(ENV_HOME_CHANNEL_THREAD_ID) or None,
        }

    return seed


def _bridge_env(env_name: str, value: Any) -> None:
    """Set ``env_name`` from YAML only when unset (env > YAML precedence)."""
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    if not os.getenv(env_name):
        os.environ[env_name] = text


def apply_yaml_config_fn(yaml_cfg: dict, platform_cfg: dict) -> Optional[dict]:
    """Translate ``platforms.imsg`` YAML into ``extra`` + bridged env vars.

    Lets the plugin own its config schema so core never needs to know it.  The
    returned dict is merged into ``PlatformConfig.extra`` by core; env bridging
    ensures out-of-process callers (cron / standalone send / requirements check)
    see the same configuration.  Env vars always win over YAML.
    """
    if not isinstance(platform_cfg, dict):
        return None
    extra = platform_cfg.get("extra")
    extra = extra if isinstance(extra, dict) else {}

    seed: dict[str, Any] = {}

    binary = extra.get("binary")
    if binary:
        seed["binary"] = binary
        _bridge_env(ENV_BINARY, binary)

    def _csv(value: Any) -> str:
        if isinstance(value, (list, tuple, set)):
            return ",".join(str(v).strip() for v in value if str(v).strip())
        return str(value).strip()

    if extra.get("allowed_handles") is not None:
        seed["allowed_handles"] = extra["allowed_handles"]
        _bridge_env(ENV_ALLOWED_USERS, _csv(extra["allowed_handles"]))
    if extra.get("group_allowed_handles") is not None:
        seed["group_allowed_handles"] = extra["group_allowed_handles"]
        _bridge_env(ENV_GROUP_ALLOWED_USERS, _csv(extra["group_allowed_handles"]))
    if extra.get("allowed_chats") is not None:
        seed["allowed_chats"] = extra["allowed_chats"]
        _bridge_env(ENV_ALLOWED_CHATS, _csv(extra["allowed_chats"]))

    for key, env_name in (
        ("allow_all", ENV_ALLOW_ALL_USERS),
        ("require_mention", ENV_REQUIRE_MENTION),
        ("auto_launch", ENV_AUTO_LAUNCH),
        ("send_read_receipts", ENV_SEND_READ_RECEIPTS),
        ("fallback_action", ENV_FALLBACK_ACTION),
        ("participant_validation", ENV_PARTICIPANT_VALIDATION),
        ("participant_cache_ttl", ENV_PARTICIPANT_CACHE_TTL),
        ("debounce", ENV_DEBOUNCE),
        ("command_timeout", ENV_COMMAND_TIMEOUT),
    ):
        if extra.get(key) is not None:
            seed[key] = extra[key]
            _bridge_env(env_name, extra[key])

    poll_events = extra.get("poll_vote_events")
    if isinstance(poll_events, dict):
        seed["poll_vote_events"] = poll_events
        for event_key, env_name in (
            ("dm", ENV_POLL_VOTES_DM),
            ("group", ENV_POLL_VOTES_GROUP),
            ("include_unvotes", ENV_POLL_UNVOTES),
        ):
            if event_key in poll_events:
                _bridge_env(env_name, poll_events[event_key])

    # Rich structures pass straight through to extra (parsed by the adapter).
    for key in ("rules", "identity_aliases", "mention_patterns"):
        if extra.get(key) is not None:
            seed[key] = extra[key]

    # Bridge the home channel so cron deliver=imsg works out-of-process.
    home = platform_cfg.get("home_channel")
    if isinstance(home, dict) and home.get("chat_id"):
        _bridge_env(ENV_HOME_CHANNEL, home.get("chat_id"))
        if home.get("name"):
            _bridge_env(ENV_HOME_CHANNEL_NAME, home.get("name"))
        if home.get("thread_id"):
            _bridge_env(ENV_HOME_CHANNEL_THREAD_ID, home.get("thread_id"))

    return seed or None
