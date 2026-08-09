#!/usr/bin/env bash
#
# install-local.sh: install the imsg platform plugin into the local Hermes
# instance by symlinking hermes_imsg_cli/ into ~/.hermes/plugins/ and enabling
# it in ~/.hermes/config.yaml (comment-preserving, with a backup).
#
# Usage:
#   scripts/install-local.sh              # symlink + enable + discovery probe
#   HERMES_HOME=/path scripts/install-local.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLUGIN_SRC="$REPO_ROOT/hermes_imsg_cli"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"
LINK="$PLUGINS_DIR/imsg-platform"
CONFIG="$HERMES_HOME/config.yaml"

PY="${HERMES_PY:-$HERMES_HOME/hermes-agent/venv/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "repo:        $REPO_ROOT"
echo "plugin src:  $PLUGIN_SRC"
echo "hermes home: $HERMES_HOME"
echo "python:      $PY"

[ -f "$PLUGIN_SRC/plugin.yaml" ] || { echo "ERROR: $PLUGIN_SRC/plugin.yaml missing"; exit 1; }
mkdir -p "$PLUGINS_DIR"

# 1) Symlink the plugin package into the plugins dir (backup anything present).
if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$PLUGIN_SRC" ]; then
  echo "symlink: already correct ($LINK -> $PLUGIN_SRC)"
elif [ -e "$LINK" ] || [ -L "$LINK" ]; then
  BAK="$LINK.bak-$(date +%Y%m%d%H%M%S)"
  mv "$LINK" "$BAK"
  ln -s "$PLUGIN_SRC" "$LINK"
  echo "symlink: replaced (old moved to $BAK)"
else
  ln -s "$PLUGIN_SRC" "$LINK"
  echo "symlink: created ($LINK -> $PLUGIN_SRC)"
fi

# 2) Enable imsg-platform in plugins.enabled (comment-preserving via ruamel).
"$PY" - "$CONFIG" <<'PY'
import sys, time
from pathlib import Path

cfg_path = Path(sys.argv[1])
NAME = "imsg-platform"
if not cfg_path.exists():
    print(f"config.yaml not found at {cfg_path}; skipping enable step")
    sys.exit(0)

try:
    from ruamel.yaml import YAML
except Exception:
    print("ruamel.yaml unavailable; enable 'imsg-platform' under plugins.enabled manually")
    sys.exit(0)

yaml = YAML()
yaml.preserve_quotes = True
# Prevent ruamel from re-wrapping unrelated long scalars (surgical edit).
yaml.width = 100000
original = cfg_path.read_text()
data = yaml.load(original)

plugins = data.get("plugins")
if plugins is None:
    data["plugins"] = {"enabled": [NAME]}
    changed = True
else:
    enabled = plugins.get("enabled")
    if enabled is None:
        plugins["enabled"] = [NAME]
        changed = True
    elif NAME in list(enabled):
        print(f"plugins.enabled: '{NAME}' already present")
        changed = False
    else:
        enabled.append(NAME)
        changed = True

if changed:
    bak = cfg_path.with_name(cfg_path.name + f".bak-imsg-{time.strftime('%Y%m%d%H%M%S')}")
    bak.write_text(original)
    with cfg_path.open("w") as f:
        yaml.dump(data, f)
    print(f"plugins.enabled: added '{NAME}' (backup: {bak.name})")
PY

# 3) Discovery probe against the local Hermes source.
HERMES_SRC="${HERMES_SRC:-$HERMES_HOME/hermes-agent}"
if [ -d "$HERMES_SRC" ]; then
  echo "== discovery probe (PYTHONPATH=$HERMES_SRC) =="
  PYTHONPATH="$HERMES_SRC" "$PY" - <<'PY'
import hermes_cli.plugins as p
p.discover_plugins(force=True)
pm = p.get_plugin_manager()
info = {k: (v.enabled, v.error) for k, v in pm._plugins.items() if "imsg" in k}
print("plugin state:", info or "(imsg plugin not discovered)")
try:
    from gateway.platform_registry import platform_registry
    entry = platform_registry.get("imsg")
    print("registry 'imsg':", "registered" if entry else "MISSING",
          "(source=%s)" % getattr(entry, "source", "-") if entry else "")
except Exception as e:
    print("registry probe error:", e)
PY
else
  echo "HERMES_SRC not found ($HERMES_SRC); skipping discovery probe"
fi

echo "done."
