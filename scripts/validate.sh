#!/usr/bin/env bash
#
# validate.sh — run the plugin test suite and an import/registration probe
# against a Hermes source tree (stock official preferred).
#
# Usage:
#   scripts/validate.sh
#   HERMES_SRC=/path/to/hermes-agent scripts/validate.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# No machine-specific default path here: point HERMES_SRC at any local Hermes
# checkout, or fall back to the standard per-user install location.
HERMES_SRC="${HERMES_SRC:-$HOME/.hermes/hermes-agent}"

PY="${HERMES_PY:-$HOME/.hermes/hermes-agent/venv/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "repo:       $REPO_ROOT"
echo "hermes src: $HERMES_SRC"
echo "python:     $PY"
echo

echo "== pytest =="
( cd "$REPO_ROOT" && HERMES_SRC="$HERMES_SRC" PYTHONPATH="$HERMES_SRC:$REPO_ROOT" "$PY" -m pytest -q )

echo
echo "== import / registration probe =="
PYTHONPATH="$HERMES_SRC:$REPO_ROOT" "$PY" - <<'PY'
from gateway.config import Platform
from gateway.platform_registry import platform_registry, PlatformEntry
from hermes_imsg_cli.adapter import register, build_entry_kwargs

# Confirm stock core has no built-in IMSG member (plugin must not depend on it).
assert not hasattr(Platform, "IMSG"), "core unexpectedly has Platform.IMSG"

class Ctx:
    def __init__(self):
        self.tools = []

    def register_platform(self, **kw):
        kw.setdefault("plugin_name", "imsg-platform")
        platform_registry.register(PlatformEntry(source="plugin", **kw))

    def register_tool(self, **kw):
        self.tools.append(kw)

ctx = Ctx()
register(ctx)
entry = platform_registry.get("imsg")
assert entry is not None, "imsg not registered"
assert entry.standalone_sender_fn is not None, "missing standalone_sender_fn"
assert entry.cron_deliver_env_var == "IMSG_HOME_CHANNEL"
assert Platform("imsg").value == "imsg"
assert [tool["name"] for tool in ctx.tools] == ["imsg_poll"]
print("OK: imsg registered as a plugin platform; Platform('imsg') resolves via _missing_")
print("    standalone_sender_fn:", bool(entry.standalone_sender_fn))
print("    cron_deliver_env_var:", entry.cron_deliver_env_var)
print("    apply_yaml_config_fn:", bool(entry.apply_yaml_config_fn))
print("    env_enablement_fn:   ", bool(entry.env_enablement_fn))
print("    plugin tools:        ", ", ".join(tool["name"] for tool in ctx.tools))
PY

echo
echo "== requirements report =="
PYTHONPATH="$REPO_ROOT" "$PY" - <<'PY'
from hermes_imsg_cli.config import requirements_report
print(requirements_report())
PY

echo
echo "validate: PASS"
