# hermes-imsg-cli

A standalone [Hermes Agent](https://github.com/nous-research) **gateway platform
plugin** that adds native **iMessage / SMS** support on macOS, backed by the
local [`imsg`](https://formulae.brew.sh/formula/imsg) CLI.

It registers the platform name **`imsg`** through Hermes' stock plugin API, so
**Hermes core needs no `Platform.IMSG` member and no built-in adapter** — the
capability lives entirely in this repo. This lets a Hermes core checkout return
to clean stock upstream while keeping iMessage working.

## Why this exists

The iMessage adapter used to live inside a modified ("dirty") Hermes core. This
plugin extracts that capability into an update-proof user plugin so core can be
reset to upstream. Everything the built-in adapter did — chat-rowid reply
routing, the allow/deny/prompt/silent_drop rule engine, DM/group allowlists,
participant + mention gating, identity aliases, read receipts, and out-of-process
cron/`send_message` delivery — is preserved here.

This version is source-audited against OpenClaw `imsg` main at commit
[`b5b7464`](https://github.com/openclaw/imsg/commit/b5b7464bc748af482bfc3059b28d5dab0395da9e)
(reviewed 2026-07-13). Compatible public features now include source-correct
chats/group/history/search/watch builders, native NDJSON parsing, attachment
conversion metadata, service-aware text/file sends, standard reactions,
thread metadata, read/typing hooks, native poll create/vote/unvote actions, and
PII-redacted failures. It does **not**
create an OpenClaw adapter or claim SIP-disabled private features are public.

## Documentation

- [Complete command/feature parity matrix](docs/PARITY.md)
- [Architecture, invariants, and decision log](docs/ARCHITECTURE.md)
- [Configuration, operations, permissions, security, and troubleshooting](docs/OPERATIONS.md)
- [Compatibility and migration notes](docs/MIGRATION.md)
- [Verification evidence](docs/VERIFICATION.md)

## Safety invariant

> **Inbound replies route back to the exact Messages chat rowid that produced
> the inbound message. No GUID resolution for routing, no participant matching,
> no cross-chat guessing.**

The adapter's reply path (`send()` → `_send_plain()`) always targets the
`--chat-id` rowid it was invoked with — every reply, threaded or not, is a
plain `imsg send --chat-id` call. Phone/E.164 routing (`--to`) is only used
for explicit outbound sends, never for replies. `send()` accepts `reply_to` /
`metadata` for interface compatibility but never uses them to select a
destination chat: no caller-, cache-, or metadata-supplied `chat_guid` can
redirect a reply to a different chat than the rowid it was invoked with.
Threaded (`send-rich`) replies are intentionally not attempted for this
reason — see `docs/ARCHITECTURE.md` for the full rationale.

## Security & privacy

See `SECURITY.md` for the vulnerability reporting process and the full list
of security properties this plugin must preserve. In summary:

- Reply routing always uses the trusted rowid (above) — never a caller-
  supplied GUID.
- Chat-id targets are validated as positive-integer rowids within a sane
  digit range before any subprocess runs; malformed/oversized targets are
  rejected, not shelled out.
- A zero exit code alone is never treated as a successful send — `imsg send`
  must also return a valid JSON confirmation object.
- Outbound attachments must be regular files: symlinks and special files
  (directories, FIFOs, sockets, device nodes) are rejected before
  `imsg send --file` runs. Inbound attachment `filename`/`file` metadata is
  never expanded into a path — only converted/original paths upstream
  actually resolves are used.
- Sending multiple attachments is fail-fast; a failure partway through
  reports exactly which attachment failed and which ones already sent
  (`failed_index`, `succeeded_message_ids`) instead of leaving the caller to
  guess.
- The resumable cursor (`imsg_state.json`) is written atomically with
  owner-only (`0600`) permissions.
- Logs never include raw `imsg watch` lines; errors and diagnostics are
  redacted for phone numbers, emails, home-directory paths, and GUID-like
  identifiers before they can reach a log line.
- Subprocess timeouts terminate the child first, then bound the wait before
  force-killing — a hung or SIGTERM-ignoring `imsg` process can never block
  cleanup indefinitely.
- `pii_safe` is deliberately not set on this platform's registry entry: the
  agent-facing `imsg_poll` tool requires the exact rowid it was given, so
  hashing chat/user ids for the LLM prompt would silently break polls for no
  privacy benefit (see `build_entry_kwargs()`).
- `python scripts/privacy_scan.py` scans the repository for phone numbers
  outside the NANP reserved `202-555-01xx` fictional range, personal home
  paths, and common secret shapes; it runs in CI on every push/PR.

## Requirements

- **macOS** with Messages.app signed in.
- The **`imsg` CLI** on `PATH` (`brew install steipete/tap/imsg`) or
  `IMSG_BINARY` pointing at it.
- Your terminal/agent granted **Full Disk Access** + **Automation (Messages)**.

The plugin **never grants permissions automatically**. If the binary or
permissions are missing it fails cleanly with an install hint (see
`hermes_imsg_cli.config.requirements_report()`).

## Install

```bash
scripts/install-local.sh
```

This symlinks `hermes_imsg_cli/` into `~/.hermes/plugins/imsg-platform`, adds
`imsg-platform` to `plugins.enabled` in `~/.hermes/config.yaml` (comments
preserved, a timestamped backup is written), and runs a discovery probe.

To uninstall: remove the symlink `~/.hermes/plugins/imsg-platform` and drop
`imsg-platform` from `plugins.enabled`.

## Configuration

Configure via `config.yaml` **or** environment variables (env overrides YAML).

### `config.yaml`

```yaml
platforms:
  imsg:
    enabled: true
    extra:
      binary: /opt/homebrew/bin/imsg
      allow_all: false
      allowed_handles: ["+1555XXXXXXX"]          # DM allowlist
      group_allowed_handles: ["+1555XXXXXXX"]    # group allowlist (separate)
      allowed_chats: ["16", "8"]                 # Messages chat rowids
      require_mention: false
      poll_vote_events:
        dm: false
        group: false
        chats: []
        include_unvotes: false
      auto_launch: true
      send_read_receipts: true
      participant_validation: fast               # fast | strict
      participant_cache_ttl: 300
      fallback_action: silent_drop               # silent_drop | deny | prompt | allow
      identity_aliases:
        "+1555XXXXXXX": Family Member
      rules:
        - name: owners_dm
          context: dm
          senders: ["+1555XXXXXXX"]
          action: allow
        - name: family_group
          context: group
          chats: ["16"]
          senders: any
          action: allow
    home_channel:
      platform: imsg
      chat_id: "16"                              # cron / notification target
      name: Family Chat
```

### Environment variables

| Var | Meaning |
| --- | --- |
| `IMSG_ENABLED` | Enable the platform from env only (`1/true/yes`). |
| `IMSG_BINARY` | Path to the `imsg` CLI. |
| `IMSG_HOME_CHANNEL` | Default cron/notification target (chat rowid or `+E.164`). |
| `IMSG_ALLOWED_USERS` | Comma-separated DM allowlist (E.164 / email). |
| `IMSG_GROUP_ALLOWED_USERS` | Comma-separated group allowlist. |
| `IMSG_ALLOWED_CHATS` | Comma-separated group chat rowids. |
| `IMSG_ALLOW_ALL_USERS` | Allow everyone (dev only). |
| `IMSG_REQUIRE_MENTION` | Require a mention in groups. |
| `IMSG_AUTO_LAUNCH` | Re-arm the Messages.app bridge on connect (default on). |
| `IMSG_SEND_READ_RECEIPTS` | Send read receipts for inbound (default on). |
| `IMSG_FALLBACK_ACTION` | Action when no rule matches. |
| `IMSG_PARTICIPANT_VALIDATION` | `fast` (event data) or `strict` (live CLI lookup). |
| `IMSG_PARTICIPANT_CACHE_TTL` | Seconds to cache strict participant lookups. |
| `IMSG_PHONE_MIN_DIGITS` | All-digit targets with ≥ this many digits are phones (default 10). |
| `IMSG_DEFAULT_COUNTRY_CODE` | Country code prepended to 10-digit numbers (default `1`). |
| `IMSG_POLL_VOTES_DM` | Deliver inbound poll vote events from DMs (default `false`). |
| `IMSG_POLL_VOTES_GROUP` | Deliver inbound poll vote events from groups (default `false`). |
| `IMSG_POLL_UNVOTES` | Include unvote/removal events when poll vote events are enabled (default `false`). |

## Rule engine

Rules are evaluated top-down; the first match wins; if none match,
`fallback_action` applies.

- `action`: `allow` | `deny` | `prompt` | `silent_drop`
- `context`: `dm` | `group` | `any`
- `senders`: allowlist (or `any`)
- `chats`: Messages chat-rowid allowlist (or `any`)
- `require_participants`: `{any_of: [...], all_of: [...]}` (group only)
- `require_mention`: boolean (group only)

When no `rules:` block is present, the flat `allow_all` / `allowed_handles` /
`group_allowed_handles` / `allowed_chats` settings are auto-synthesised into
equivalent rules, so simple configs keep working.

## Cron / `send_message` (out-of-process delivery)

The plugin registers `standalone_sender_fn` and `cron_deliver_env_var=
IMSG_HOME_CHANNEL`, so `send_message` and cron jobs (`deliver=imsg`) reach
iMessage even when they run outside the live gateway process. Text and file
attachments (`imsg send --file`) are supported; a missing attachment returns a
structured error rather than a false success.

### Known core gap: E.164 `+` stripping (worked around plugin-side)

Stock Hermes core's `send_message` target parser
(`tools/send_message_tool._parse_target_ref`) only preserves a leading `+` for
platform names listed in `_PHONE_PLATFORMS`, which **upstream does not include
`imsg`**. As a result:

- `imsg:+12025550105` — core drops the `+` before the plugin sees it.
- `imsg:12025550105` — arrives at the plugin as the bare digit string.
- `imsg:16` — arrives as `16` (a Messages chat rowid).

To avoid patching core, the plugin re-hydrates E.164 in its **standalone send
path only** (`hermes_imsg_cli.config.normalize_imsg_target`): an all-digit
target with ≥ `IMSG_PHONE_MIN_DIGITS` (default 10) digits is treated as a phone
number and routed via `imsg send --to` (a 10-digit number gets
`IMSG_DEFAULT_COUNTRY_CODE`, default `+1`); shorter numeric targets stay chat
rowids routed via `--chat-id`. The **reply path is untouched**, so inbound
reply routing remains byte-for-byte identical to the original adapter.

Recommended addressing:

- Reply to a conversation / group: `imsg:<chat_rowid>` (e.g. `imsg:16`).
- New DM to a number: `imsg:12025550105` **or** set `IMSG_HOME_CHANNEL=+12025550105`.

If you prefer core to preserve the `+` natively, the one-line upstream change is
to add `"imsg"` to `_PHONE_PLATFORMS` in `tools/send_message_tool.py` — but this
plugin does **not** require it.

## Safe read API

The plugin preserves upstream JSON objects rather than maintaining a second,
drift-prone message schema:

```python
from hermes_imsg_cli.client import ImsgClient

client = ImsgClient("/opt/homebrew/bin/imsg", timeout=30)
chats = await client.chats(limit=10, unread_only=False)
group = await client.group("42")
messages = await client.history("42", limit=20, convert_attachments=True)
hits = await client.search("pizza", match="contains", limit=20)
```

`--json` is NDJSON (one object per line). Parse errors report only the line
number, never the private payload. The adapter exposes equivalent additive
`list_chats`, `get_chat_info`, `get_history`, and `search_messages` methods.

## Poll vote event policy

Poll vote rows are preserved by `imsg watch`, but Hermes keeps them disabled by
default so a poll cannot unexpectedly start an agent turn. Enable each scope
independently under `platforms.imsg.extra`:

```yaml
poll_vote_events:
  dm: true                 # direct-message votes
  group: false             # group-chat votes
  chats: ["3", "16"]      # optional exact Messages chat rowids
  include_unvotes: false  # selected -> removed events
```

The equivalent environment variables are `IMSG_POLL_VOTES_DM`,
`IMSG_POLL_VOTES_GROUP`, and `IMSG_POLL_UNVOTES`. When enabled, Hermes converts
a vote row into readable text such as `Poll vote: Yes on "Dinner?"` while
preserving the complete upstream poll object in `raw_message`. Existing
allowlists, ordered rules, participant checks, and group mention gates still
apply; these settings only control whether vote-only rows become eligible for
agent delivery. When `chats` is non-empty, it is an additional exact chat-rowid
allowlist applied before delivery.

## Explicit native poll action

The plugin registers one agent-facing Hermes tool, `imsg_poll`, with explicit
`send`, `vote`, and `unvote` actions. Polls are not inferred from ordinary text:
creation is a distinct native extension mutation and the operator/agent must
provide an exact positive Messages `chat_id` rowid (preferred, and required for
inbound-originated operations) or an exact proactive `chat_guid`. Bare phone or
email participant handles are rejected.

```python
client = ImsgClient("/opt/homebrew/bin/imsg", timeout=30)
created = await client.send_poll(
    "42", "Dinner?", ["Pizza", "Sushi"], comment="Vote by 5pm",
    reply_to="OPTIONAL-PARENT-MESSAGE-GUID",
)
voted = await client.vote_poll("42", "POLL-GUID", option_index=2)
removed = await client.unvote_poll("42", "POLL-GUID", option_id="OPTION-UUID")
```

The CLI supports selecting a vote by exact option UUID, 1-based index, or
case-insensitive option text. Unvote is selective: it removes exactly the
resolved option and upstream rejects an option that is not currently selected.
Poll creation uses the `poll.send` JSON-RPC mutation path, matching OpenClaw's
caller and avoiding the short-lived CLI caption path. Vote and unvote retain
their tested CLI selector behavior for now; the plugin also exposes pure RPC
request builders for integrations.

Every client/tool action first runs read-only `imsg status --json`. Creation
requires advertised RPC method `poll.send` (or alias) and selector
`pollPayloadMessage`; vote/unvote require their advertised RPC methods and
`pollVoteMessage`. Missing methods/selectors return structured failure before a
poll subprocess is attempted. This means the command is implemented in Hermes
even when the installed binary or injected bridge cannot execute it.

Polls require the optional IMCore bridge (`imsg launch`, SIP-disabled injection)
and a sufficiently new upstream build. The plugin never disables SIP, installs
a dylib, grants TCC, launches/restarts services for poll use, or treats a plain
caption failure as poll failure. Upstream sends a best-effort plain caption after
creation because Messages does not render the payload title; retrying after only
that caption fails could duplicate the poll.

## Public versus advanced features

Normal chats/group/history/search/watch/plain send/file send/public standard
reaction operations use the local database and Messages automation. Native
poll create/vote/unvote, read receipts, typing, and targeted tapbacks are
advanced IMCore features in upstream `imsg`; they require a SIP-disabled
injected bridge and can be unavailable on macOS 26. The plugin never disables
SIP or grants permissions. For a normal SIP-enabled Mac, set:

```yaml
platforms:
  imsg:
    extra:
      auto_launch: false
      send_read_receipts: false
```

Threaded/rich replies (`send-rich --reply-to`) are not used at all — every
reply always plain-sends by rowid; see the safety invariant above. See the
[parity ledger](docs/PARITY.md) for every upstream command, including safe
exclusions and alternatives.

## Layout

```
hermes_imsg_cli/          # the plugin package (symlink target)
  __init__.py             # register(ctx) entry point (lazy import)
  plugin.yaml             # manifest (kind: platform)
  adapter.py              # ImsgAdapter + registration + standalone send
  rules.py                # ImsgRule + parser (Hermes-free)
  config.py               # env keys, requirements, normalization, config hooks
  cli.py                  # pure imsg CLI arg builders + subprocess runner
  client.py               # bounded async public-CLI/NDJSON facade (no Hermes import)
tests/                    # pytest suite (no live sends)
docs/                     # parity, architecture, operations, migration, verification
scripts/
  install-local.sh        # symlink + enable + probe
  validate.sh             # tests + import/registration probe
  privacy_scan.py         # repo-wide privacy/security scanner (see CONTRIBUTING.md)
.github/workflows/ci.yml  # pinned-action macOS CI: tests, scan, build
LICENSE                   # MIT
SECURITY.md               # vulnerability reporting + preserved security properties
CONTRIBUTING.md           # development setup and contribution guidelines
```

`rules.py`, `config.py`, and `cli.py` are import-safe **without** a Hermes
checkout; only `adapter.py` imports `gateway.*`. `__init__.py` imports the
adapter lazily so `import hermes_imsg_cli.rules` works standalone.

## Testing

```bash
scripts/validate.sh
# or, explicitly:
HERMES_SRC=/path/to/hermes-agent \
  PYTHONPATH="/path/to/hermes-agent:$PWD" python -m pytest -q

# Repo-wide privacy/security scan (also runs in CI):
python scripts/privacy_scan.py
```

Tests never perform live iMessage sends. Adapter/registration tests
`importorskip("gateway")`, so the pure rule/normalization tests run anywhere.
CI (`.github/workflows/ci.yml`) runs on macOS with pinned action commit SHAs
and executes the test suite, a byte-compile check, the privacy/security
scanner, and a wheel build.

## License

MIT
