"""Plugin registration + config-hook tests.  Requires Hermes core on PYTHONPATH."""

import os
import shutil
import sys

import pytest

pytest.importorskip("gateway", reason="Hermes core not on PYTHONPATH")

from gateway.config import Platform  # noqa: E402
from gateway.platform_registry import PlatformEntry, platform_registry  # noqa: E402

from hermes_imsg_cli import adapter as adapter_mod  # noqa: E402
from hermes_imsg_cli.adapter import (  # noqa: E402
    build_entry_kwargs,
    register,
)
from hermes_imsg_cli.config import (  # noqa: E402
    ENV_ALLOW_ALL_USERS,
    ENV_ALLOWED_USERS,
    ENV_GROUP_ACTION_ADMINS,
    ENV_HOME_CHANNEL,
    ENV_REACTION_NOTIFICATIONS,
    apply_yaml_config_fn,
    env_enablement_fn,
)


class FakeCtx:
    """Minimal stand-in for hermes_cli.plugins.PluginContext.register_platform."""

    def __init__(self):
        self.registered_kwargs = None
        self.registered_tools = []

    def register_platform(self, **kwargs):
        self.registered_kwargs = dict(kwargs)
        kwargs.setdefault("plugin_name", "imsg-platform")
        platform_registry.register(PlatformEntry(source="plugin", **kwargs))

    def register_tool(self, **kwargs):
        self.registered_tools.append(dict(kwargs))


# ---------------------------------------------------------------------------
# register() / entry shape
# ---------------------------------------------------------------------------

def test_build_entry_kwargs_shape():
    kw = build_entry_kwargs()
    assert kw["name"] == "imsg"
    assert kw["label"] == "iMessage"
    assert callable(kw["adapter_factory"])
    assert callable(kw["check_fn"])
    assert callable(kw["standalone_sender_fn"])
    assert callable(kw["env_enablement_fn"])
    assert callable(kw["apply_yaml_config_fn"])
    assert kw["cron_deliver_env_var"] == ENV_HOME_CHANNEL
    assert kw["allowed_users_env"] == ENV_ALLOWED_USERS
    assert kw["allow_all_env"] == ENV_ALLOW_ALL_USERS
    # Not justified: the agent-facing imsg_poll tool requires the *exact*
    # rowid the agent was given, so hashing chat_id/user_id for the LLM
    # prompt (what pii_safe=True would enable) would silently break poll
    # actions for no privacy benefit. See build_entry_kwargs() for the
    # full rationale.
    assert kw.get("pii_safe", False) is False
    # All keys must be valid PlatformEntry fields (constructor is the check).
    PlatformEntry(source="plugin", **kw)


def test_register_registers_imsg_platform():
    ctx = FakeCtx()
    register(ctx)
    assert ctx.registered_kwargs is not None
    assert ctx.registered_kwargs["name"] == "imsg"

    entry = platform_registry.get("imsg")
    assert entry is not None
    assert entry.source == "plugin"
    assert entry.name == "imsg"
    assert entry.cron_deliver_env_var == "IMSG_HOME_CHANNEL"
    assert entry.standalone_sender_fn is adapter_mod._standalone_send
    assert entry.env_enablement_fn is not None
    assert entry.apply_yaml_config_fn is not None
    assert [tool["name"] for tool in ctx.registered_tools] == [
        "imsg_action",
        "imsg_poll",
    ]
    action_tool, poll_tool = ctx.registered_tools
    assert action_tool["toolset"] == "imsg"
    assert action_tool["is_async"] is True
    assert action_tool["schema"]["parameters"]["properties"]["action"]["enum"] == [
        "react",
        "edit",
        "unsend",
        "reply",
        "sendWithEffect",
        "upload-file",
        "renameGroup",
        "setGroupIcon",
        "addParticipant",
        "removeParticipant",
        "leaveGroup",
        "poll",
        "poll-vote",
        "poll-unvote",
    ]
    assert poll_tool["toolset"] == "imsg"
    assert poll_tool["is_async"] is True
    assert poll_tool["schema"]["parameters"]["properties"]["action"]["enum"] == ["send", "vote", "unvote"]


def test_platform_missing_resolves_after_register():
    adapter_mod._ensure_registered()
    # Platform("imsg") must resolve to a stable dynamic member (no Platform.IMSG
    # in stock core).
    p = Platform("imsg")
    assert p.value == "imsg"
    assert Platform("imsg") is p


def test_adapter_factory_builds_adapter(tmp_path):
    from gateway.config import PlatformConfig
    entry = platform_registry.get("imsg") or _register_fresh()
    cfg = PlatformConfig(enabled=True, extra={"binary": "/usr/bin/false", "state_file": str(tmp_path / "s.json")})
    inst = entry.adapter_factory(cfg)
    assert inst.__class__.__name__ == "ImsgAdapter"


def _register_fresh():
    ctx = FakeCtx()
    register(ctx)
    return platform_registry.get("imsg")


# ---------------------------------------------------------------------------
# env_enablement_fn
# ---------------------------------------------------------------------------

def test_env_enablement_returns_none_without_enabled(monkeypatch):
    for var in ("IMSG_ENABLED", "IMSG_HOME_CHANNEL", "IMSG_ALLOWED_USERS"):
        monkeypatch.delenv(var, raising=False)
    assert env_enablement_fn() is None


def test_env_enablement_returns_seed_with_enabled(monkeypatch):
    monkeypatch.setenv("IMSG_ENABLED", "true")
    monkeypatch.setenv("IMSG_HOME_CHANNEL", "16")
    monkeypatch.setenv("IMSG_ALLOWED_USERS", "+12025550101")
    monkeypatch.setenv("IMSG_GROUP_ALLOWED_USERS", "+12025550101,+12025550102")
    monkeypatch.setenv(ENV_GROUP_ACTION_ADMINS, "+12025550101")
    seed = env_enablement_fn()
    assert seed is not None
    assert seed["allowed_handles"] == "+12025550101"
    assert seed["group_allowed_handles"] == "+12025550101,+12025550102"
    assert seed["group_action_admins"] == "+12025550101"
    assert seed["home_channel"]["chat_id"] == "16"


def test_env_enablement_preserves_reaction_notification_mode(monkeypatch):
    monkeypatch.setenv("IMSG_ENABLED", "true")
    monkeypatch.setenv(ENV_REACTION_NOTIFICATIONS, "all")
    seed = env_enablement_fn()
    assert seed is not None
    assert seed["reaction_notifications"] == "all"


# ---------------------------------------------------------------------------
# apply_yaml_config_fn
# ---------------------------------------------------------------------------

def test_apply_yaml_config_bridges_env_and_returns_extra(monkeypatch):
    for var in (
        "IMSG_BINARY",
        "IMSG_ALLOWED_USERS",
        "IMSG_GROUP_ACTION_ADMINS",
        "IMSG_HOME_CHANNEL",
    ):
        monkeypatch.delenv(var, raising=False)
    platform_cfg = {
        "extra": {
            "binary": "/opt/homebrew/bin/imsg",
            "allowed_handles": ["+12025550105", "+12025550106"],
            "group_action_admins": ["+12025550105"],
            "rules": [{"name": "owners_dm", "action": "allow"}],
            "identity_aliases": {"+12025550105": "Family Member"},
        },
        "home_channel": {"chat_id": "16", "name": "Family"},
    }
    seed = apply_yaml_config_fn({}, platform_cfg)
    assert seed is not None
    assert seed["binary"] == "/opt/homebrew/bin/imsg"
    assert seed["rules"] == [{"name": "owners_dm", "action": "allow"}]
    assert seed["identity_aliases"] == {"+12025550105": "Family Member"}
    # Env bridged so out-of-process callers (cron/standalone) see the config.
    import os
    assert os.environ["IMSG_BINARY"] == "/opt/homebrew/bin/imsg"
    assert os.environ["IMSG_ALLOWED_USERS"] == "+12025550105,+12025550106"
    assert os.environ["IMSG_GROUP_ACTION_ADMINS"] == "+12025550105"
    assert os.environ["IMSG_HOME_CHANNEL"] == "16"


def test_apply_yaml_config_env_wins_over_yaml(monkeypatch):
    monkeypatch.setenv("IMSG_BINARY", "/custom/imsg")
    platform_cfg = {"extra": {"binary": "/opt/homebrew/bin/imsg"}}
    apply_yaml_config_fn({}, platform_cfg)
    import os
    assert os.environ["IMSG_BINARY"] == "/custom/imsg"  # env preserved


def test_apply_yaml_config_bridges_reaction_notification_mode(monkeypatch):
    monkeypatch.delenv(ENV_REACTION_NOTIFICATIONS, raising=False)
    seed = apply_yaml_config_fn(
        {}, {"extra": {"reaction_notifications": "off"}}
    )
    assert seed is not None
    assert seed["reaction_notifications"] == "off"
    assert os.environ[ENV_REACTION_NOTIFICATIONS] == "off"


def test_apply_yaml_config_ignores_non_dict():
    assert apply_yaml_config_fn({}, None) is None
    assert apply_yaml_config_fn({}, "nope") is None


# ---------------------------------------------------------------------------
# Full config integration via _apply_env_overrides (macOS + imsg only)
# ---------------------------------------------------------------------------

def test_env_overrides_enables_imsg(monkeypatch):
    if sys.platform != "darwin" or not shutil.which("imsg"):
        pytest.skip("requires macOS with the imsg CLI installed")
    adapter_mod._ensure_registered()
    from gateway.config import GatewayConfig, _apply_env_overrides

    monkeypatch.setenv("IMSG_ENABLED", "true")
    monkeypatch.setenv("IMSG_HOME_CHANNEL", "16")
    monkeypatch.setenv("IMSG_ALLOWED_USERS", "+12025550101")
    monkeypatch.setenv("IMSG_GROUP_ALLOWED_USERS", "+12025550101,+12025550102")

    config = GatewayConfig()
    _apply_env_overrides(config)

    pconfig = config.platforms[Platform("imsg")]
    assert pconfig.enabled is True
    assert pconfig.home_channel is not None
    assert pconfig.home_channel.chat_id == "16"
    assert pconfig.extra["allowed_handles"] == "+12025550101"
    assert pconfig.extra["group_allowed_handles"] == "+12025550101,+12025550102"
