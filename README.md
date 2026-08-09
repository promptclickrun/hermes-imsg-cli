# hermes-imsg-cli

`hermes-imsg-cli` is a standalone [Hermes Agent](https://github.com/nous-research)
gateway platform plugin for iMessage and SMS on macOS. It uses the local
[`imsg`](https://formulae.brew.sh/formula/imsg) CLI and registers the platform
name `imsg` through Hermes' stock plugin API. Hermes core does not need a
`Platform.IMSG` member or a built-in adapter.

The iMessage adapter once lived in a modified Hermes core checkout. Keeping it
in this user plugin lets Hermes core remain aligned with stock upstream. The
plugin retains exact chat-rowid reply routing,
the `allow`/`deny`/`prompt`/`silent_drop` rule engine, separate DM and group
allowlists, participant and mention gates, identity aliases, read receipts, and
out-of-process delivery for cron and `send_message`.

Compatibility was reviewed against OpenClaw `imsg` main at commit
[`b5b7464`](https://github.com/openclaw/imsg/commit/b5b7464bc748af482bfc3059b28d5dab0395da9e),
on 2026-07-13. The reviewed surface includes source-correct chats, group,
history, search, and watch commands; native NDJSON parsing; attachment
conversion metadata; service-aware text and file sends; standard reactions;
thread metadata; read and typing hooks; explicit, capability-gated native poll
creation, voting, and unvoting; and failures with PII redacted. This repository
is not an OpenClaw adapter, and it does not claim that private, SIP-disabled
features are public.

## Start with the routing invariant

> Inbound replies return to the exact Messages chat rowid that produced the
> inbound message. The plugin does not resolve a GUID for routing, match a
> participant, or guess across chats.

The adapter's reply path, `send()` to `_send_plain()`, always uses
`imsg send --chat-id` with the rowid it received. Explicit outbound sends may
use phone or E.164 routing through `--to`, but replies never do.

Hermes may pass `reply_to` or `metadata` to `send()` for interface
compatibility. The plugin ignores both when choosing a destination. A caller,
cache entry, or metadata field cannot supply a `chat_guid` that redirects the
reply. The adapter also never attempts a threaded `send-rich` reply, because a
threaded destination cannot safely override the captured rowid. See
[Architecture and decision log](docs/ARCHITECTURE.md) for the full reasoning.

## Requirements

- macOS 14 or later with Messages.app signed in.
- The `imsg` CLI on `PATH`, installed with
  `brew install steipete/tap/imsg`, or an explicit `IMSG_BINARY` path.
- Full Disk Access for the complete launcher chain that reads
  `~/Library/Messages/chat.db`.
- Automation permission for Messages.app when sending.
- Accessibility and Automation permission for public `react` operations.
- Optional `ffmpeg` for CAF and GIF attachment conversion on receipt.

SMS also requires Text Message Forwarding from an iPhone. Although upstream
supports a copied-database read-only preview on Linux, this live Hermes platform
requires macOS and Messages.app.

The plugin never grants permissions, edits TCC, or changes SIP. If the binary
or required permissions are missing, it fails with an installation hint from
`hermes_imsg_cli.config.requirements_report()`.

## Install and remove the plugin

From the repository root, run:

```bash
scripts/install-local.sh
```

The installer symlinks `hermes_imsg_cli/` to
`~/.hermes/plugins/imsg-platform`. When it can update an existing Hermes
configuration, it adds `imsg-platform` to `plugins.enabled`, preserves comments,
and writes a timestamped backup. It runs a discovery probe when the local Hermes
source is available. It does not install `imsg`, grant permissions, change SIP,
or restart Hermes.

To uninstall the plugin, remove the
`~/.hermes/plugins/imsg-platform` symlink and remove `imsg-platform` from
`plugins.enabled`.

## Configure the plugin

You can configure the plugin in `config.yaml`, with environment variables, or
with both. Environment variables take precedence over YAML.

### YAML configuration

This example shows the main policy shape used throughout this README:

```yaml
platforms:
  imsg:
    enabled: true
    extra:
      binary: /opt/homebrew/bin/imsg
      allow_all: false
      allowed_handles: ["+12025550101"]          # DM allowlist
      group_allowed_handles: ["+12025550101"]    # separate group allowlist
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
        "+12025550101": Family Member
      rules:
        - name: owners_dm
          context: dm
          senders: ["+12025550101"]
          action: allow
        - name: family_group
          context: group
          chats: ["16"]
          senders: any
          action: allow
    home_channel:
      platform: imsg
      chat_id: "16"                              # cron and notification target
      name: Family Chat
```

`auto_launch` and `send_read_receipts` retain the original adapter defaults of
`true`. Both are bridge-facing settings, and read receipts depend on advanced
IMCore behavior. On a normal SIP-enabled Mac, use the public-only settings in
[Public and advanced feature boundaries](#public-and-advanced-feature-boundaries).

### Environment variables

| Variable | Meaning |
| --- | --- |
| `IMSG_ENABLED` | Enable the platform from the environment only (`1`, `true`, or `yes`). |
| `IMSG_BINARY` | Path to the `imsg` CLI. |
| `IMSG_HOME_CHANNEL` | Default cron or notification target, as a chat rowid or `+E.164` number. |
| `IMSG_ALLOWED_USERS` | Comma-separated DM allowlist using E.164 numbers or email addresses. |
| `IMSG_GROUP_ALLOWED_USERS` | Comma-separated group allowlist. |
| `IMSG_ALLOWED_CHATS` | Comma-separated group chat rowids. |
| `IMSG_ALLOW_ALL_USERS` | Allow every user. Use this only for development. |
| `IMSG_REQUIRE_MENTION` | Require a mention in group chats. |
| `IMSG_MENTION_PATTERNS` | Group mention patterns as a JSON array or comma- or newline-separated regular expressions. |
| `IMSG_AUTO_LAUNCH` | Re-arm the Messages.app bridge on connect. The default is `true`. |
| `IMSG_SEND_READ_RECEIPTS` | Send read receipts for inbound messages. The default is `true`. |
| `IMSG_FALLBACK_ACTION` | Action to take when no rule matches. |
| `IMSG_PARTICIPANT_VALIDATION` | Use `fast` event data or a `strict` live CLI lookup. |
| `IMSG_PARTICIPANT_CACHE_TTL` | Number of seconds to cache strict participant lookups. The default is `300`. |
| `IMSG_DEBOUNCE` | Upstream watch debounce, such as `500ms`. |
| `IMSG_STATE_FILE` | Optional path for the resumable last-rowid cursor. |
| `IMSG_PHONE_MIN_DIGITS` | Minimum length at which an all-digit target is treated as a phone number. The default is `10`. |
| `IMSG_DEFAULT_COUNTRY_CODE` | Country code added to a bare 10-digit number. The default is `1`. |
| `IMSG_COMMAND_TIMEOUT` | Timeout in seconds for bounded CLI commands. The default is `30`; the supervised watch process is excluded. |
| `IMSG_POLL_VOTES_DM` | Deliver inbound poll vote events from DMs. The default is `false`. |
| `IMSG_POLL_VOTES_GROUP` | Deliver inbound poll vote events from group chats. The default is `false`. |
| `IMSG_POLL_UNVOTES` | Include unvote and removal events when poll vote delivery is enabled. The default is `false`. |

### Access rules

Rules are evaluated from top to bottom. The first match wins. If no rule
matches, `fallback_action` applies. Its default is `silent_drop`.

- `action`: `allow` | `deny` | `prompt` | `silent_drop`
- `context`: `dm` | `group` | `any`
- `senders`: an allowlist or `any`
- `chats`: a Messages chat-rowid allowlist or `any`
- `require_participants`: `{any_of: [...], all_of: [...]}` for groups only
- `require_mention`: a boolean for groups only

Every populated condition in a rule must match. DM and group allowlists remain
separate. If there is no `rules` block, the plugin synthesizes equivalent rules
from `allow_all`, `allowed_handles`, `group_allowed_handles`, and
`allowed_chats`, so existing simple configurations continue to work. Self
messages are always dropped, including when `allow_all` is enabled.

`participant_validation: fast` uses participant data from the upstream watch
event. `strict` runs `imsg group --chat-id <rowid> --json` and caches the result
for `participant_cache_ttl` seconds. Strict mode fails closed if the command or
JSON parsing fails.

### Poll vote delivery

`imsg watch` preserves poll vote rows, but Hermes does not deliver them to the
agent by default. This prevents a vote from starting an agent turn without an
operator's explicit choice. Enable direct-message and group scopes separately:

```yaml
poll_vote_events:
  dm: true                 # direct-message votes
  group: false             # group-chat votes
  chats: ["3", "16"]      # optional exact Messages chat rowids
  include_unvotes: false  # selected -> removed events
```

The equivalent environment variables are `IMSG_POLL_VOTES_DM`,
`IMSG_POLL_VOTES_GROUP`, and `IMSG_POLL_UNVOTES`. When vote delivery is enabled,
the plugin converts a vote row into readable text such as
`Poll vote: Yes on "Dinner?"` and keeps the complete upstream poll object in
`raw_message`.

The existing sender and chat rules, participant checks, and group mention gates
still apply. These settings only decide whether a vote-only row is eligible for
delivery. A non-empty `chats` list adds an exact chat-rowid allowlist before
delivery, and `include_unvotes` controls removal events separately.

## Send from cron and `send_message`

The plugin registers `standalone_sender_fn` and sets
`cron_deliver_env_var=IMSG_HOME_CHANNEL`. Cron jobs with `deliver=imsg` and the
`send_message` tool can therefore reach iMessage without a live gateway
adapter.

The standalone path supports text and file attachments through
`imsg send --file`. It validates every attachment before sending. A missing or
unsafe attachment returns a structured error rather than a false success.
Because upstream accepts one file per command, the plugin sends multiple files
separately and stops at the first failure. A partial failure reports
`failed_index`, `attempted_attachments`, and `succeeded_message_ids`.

### E.164 normalization in stock Hermes

Stock Hermes core parses targets in
`tools/send_message_tool._parse_target_ref`. It preserves a leading `+` only
for platform names in `_PHONE_PLATFORMS`, and upstream does not include `imsg`
in that list. The target therefore reaches this plugin as follows:

- `imsg:+12025550105`: core removes the leading `+`.
- `imsg:12025550105`: the plugin receives the bare digit string.
- `imsg:16`: the plugin receives `16`, which is a Messages chat rowid.

The plugin handles this in the standalone send path through
`hermes_imsg_cli.config.normalize_imsg_target`. An all-digit target with at
least `IMSG_PHONE_MIN_DIGITS` digits, `10` by default, is treated as a phone
number and sent with `imsg send --to`. A 10-digit number receives
`IMSG_DEFAULT_COUNTRY_CODE`, which defaults to `+1`. Shorter numeric targets
remain chat rowids and use `--chat-id`.

Use these target forms:

- Reply to a conversation or group with `imsg:<chat_rowid>`, such as
  `imsg:16`.
- Start a DM with `imsg:12025550105`, or set
  `IMSG_HOME_CHANNEL=+12025550105`.

This workaround does not change inbound reply routing, which remains the same
as the original adapter. If you want core to preserve `+` itself, add `"imsg"`
to `_PHONE_PLATFORMS` in `tools/send_message_tool.py`. The plugin does not
require that core change.

## Read through Python

The client keeps upstream JSON objects intact instead of maintaining another
message schema that could drift:

```python
from hermes_imsg_cli.client import ImsgClient

client = ImsgClient("/opt/homebrew/bin/imsg", timeout=30)
chats = await client.chats(limit=10, unread_only=False)
group = await client.group("42")
messages = await client.history("42", limit=20, convert_attachments=True)
hits = await client.search("pizza", match="contains", limit=20)
```

Upstream `--json` output is NDJSON, with one object per line. Parse errors name
the line number without quoting its private payload. The adapter exposes the
same additive read surface through `list_chats`, `get_chat_info`, `get_history`,
and `search_messages`.

## Use native polls explicitly

The plugin registers one agent-facing Hermes tool, `imsg_poll`, with `send`,
`vote`, and `unvote` actions. Poll creation is a separate native extension
mutation. The plugin never infers a poll from ordinary text.

Poll creation requires either an exact positive Messages `chat_id` rowid or an
exact proactive `chat_guid`. Use the captured rowid for any operation that
begins with an inbound message. A phone number or email participant handle is
not accepted as a poll target.

```python
client = ImsgClient("/opt/homebrew/bin/imsg", timeout=30)
created = await client.send_poll(
    "42", "Dinner?", ["Pizza", "Sushi"], comment="Vote by 5pm",
    reply_to="OPTIONAL-PARENT-MESSAGE-GUID",
)
voted = await client.vote_poll("42", "POLL-GUID", option_index=2)
removed = await client.unvote_poll("42", "POLL-GUID", option_id="OPTION-UUID")
```

Vote and unvote selectors may be an exact option UUID, a 1-based index, or
case-insensitive option text. Unvote is selective: it removes only the resolved
option, and upstream rejects an option that is not currently selected.

Creation uses the `poll.send` JSON-RPC mutation path used by OpenClaw's caller,
avoiding the short-lived CLI caption path. Vote and unvote retain their tested
CLI selector behavior. The plugin also provides pure RPC request builders for
integrations.

Before every poll mutation, the client runs the read-only
`imsg status --json` check. Creation requires an advertised `poll.send` method,
or its alias, together with `pollPayloadMessage`. Vote and unvote require their
advertised RPC methods and `pollVoteMessage`. If a method or selector is
missing, the plugin returns a structured failure before starting the poll
mutation subprocess. The Hermes tool remains implemented when the installed
binary or bridge lacks the feature; it reports that capability failure instead
of claiming success.

Native polls require a sufficiently new upstream build and the optional IMCore
bridge started by `imsg launch`, which uses SIP-disabled injection. The plugin
does not disable SIP, install a dylib, grant TCC access, or launch or restart a
service for poll use.

Upstream sends a best-effort plain caption after creating a poll because
Messages does not render the payload title. A caption failure does not mean the
poll failed. Retrying solely because the caption failed can create a duplicate
poll.

## Public and advanced feature boundaries

Chat listing, group lookup, history, search, watch, plain send, file send, and
standard public reactions use the local Messages database and Messages
automation. Basic operation does not require SIP-disabled injection. Public
`react` accepts the six standard tapbacks: `love`, `like`, `dislike`, `laugh`,
`emphasis`, and `question`. Custom emoji and removal are not exposed through
the public path.

Native poll creation, voting, and unvoting do require the optional IMCore
bridge. Read receipts, typing indicators, and targeted tapbacks are also
advanced IMCore features in upstream `imsg`. They can be unavailable on macOS
26. The plugin does not disable SIP or grant permissions for any of them.

For a normal SIP-enabled Mac, use:

```yaml
platforms:
  imsg:
    extra:
      auto_launch: false
      send_read_receipts: false
```

Threaded and rich replies through `send-rich --reply-to` are never used. Every
reply is a plain rowid send. The [parity ledger](docs/PARITY.md) records every
reviewed upstream command, including safe exclusions and direct alternatives.

## Security and privacy

Read [SECURITY.md](SECURITY.md) before changing routing, subprocess handling,
attachments, logs, or local state. The following properties are part of the
plugin's operating contract:

- Replies use the trusted positive rowid and never a caller-supplied GUID.
- Chat-id send targets must be positive integer rowids no longer than 19 digits.
  Invalid or oversized targets are rejected before a subprocess starts.
- A zero exit code from `imsg send` is not enough to report success. The command
  must also return a valid JSON confirmation object.
- Outbound attachments must be regular files. The plugin rejects symlinks,
  directories, FIFOs, sockets, and device nodes before running
  `imsg send --file`.
- Inbound attachment `filename` and `file` metadata are not expanded into paths.
  The plugin uses only `converted_path`, `path`, or `cached_path` values that
  upstream resolved, and it omits paths marked `missing=true`.
- Multi-attachment sends stop at the first failure and report the failed index,
  attempted attachment count, and already-confirmed message IDs.
- The resumable `imsg_state.json` cursor is written atomically with owner-only
  `0600` permissions.
- Raw `imsg watch` lines never enter logs. Errors and diagnostics are redacted
  for phone numbers, email addresses, home-directory paths, and GUID-like
  identifiers before they reach a log line or agent-visible error.
- A timed-out or cancelled subprocess receives a terminate signal first. The
  plugin bounds that wait and then force-kills a child that does not exit, so
  cleanup cannot hang indefinitely.
- The platform registry deliberately leaves `pii_safe` unset. The `imsg_poll`
  tool must receive the exact rowid shown to the agent; hashing that value in
  the prompt would break poll actions without improving routing privacy. See
  `build_entry_kwargs()` for the registry entry.
- The plugin never edits TCC, disables SIP, signs or injects a dylib, or signs
  in to Messages on the operator's behalf.

Before publishing a change, run the repository privacy scan:

```bash
python scripts/privacy_scan.py
```

The scan checks for phone numbers outside the NANP reserved fictional
`202-555-01xx` range, personal home paths, and common secret shapes. CI runs it
for pushes to `main` and for pull requests.

## Repository layout

```text
hermes_imsg_cli/          # plugin package and symlink target
  __init__.py             # lazy register(ctx) entry point
  plugin.yaml             # platform plugin manifest
  adapter.py              # adapter, registration, rules integration, and standalone send
  rules.py                # Hermes-independent rules and redaction
  config.py               # configuration, requirements, state path, and target normalization
  cli.py                  # pure argument builders and subprocess runners
  client.py               # bounded async CLI and NDJSON client without Hermes imports
tests/                    # pytest suite; no live sends
docs/                     # architecture, operations, parity, migration, and verification
scripts/
  install-local.sh        # local symlink, enablement, and discovery probe
  validate.sh             # tests and import/registration probe
  privacy_scan.py         # repository privacy and security scanner
.github/workflows/ci.yml  # pinned-action macOS CI
LICENSE                   # MIT license
SECURITY.md               # vulnerability reporting and required security properties
CONTRIBUTING.md           # development setup and contribution guidance
```

`rules.py`, `config.py`, and `cli.py` can be imported without a Hermes
checkout. Only `adapter.py` imports `gateway.*`, and `__init__.py` imports the
adapter lazily. This keeps `import hermes_imsg_cli.rules` usable on its own.

## Verify a change

Run the repository validation script:

```bash
scripts/validate.sh
```

Or run the suite explicitly against a Hermes Agent checkout:

```bash
HERMES_SRC=/path/to/hermes-agent \
  PYTHONPATH="/path/to/hermes-agent:$PWD" python -m pytest -q
```

Run the privacy and security scan separately when needed:

```bash
python scripts/privacy_scan.py
```

Tests never send a live iMessage or SMS. They also do not create polls, cast or
remove votes, or run reactions against a real Messages account. Adapter and
registration tests call `pytest.importorskip("gateway")`, so the pure rule,
normalization, command-builder, and client tests can run without a Hermes
checkout.

The CI workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) is
configured for macOS with action versions pinned by commit SHA. It runs the
tests, byte-compiles the Python sources, checks shell syntax, runs the privacy
and security scan, checks pull-request diffs for whitespace errors, builds a
wheel, and uploads the wheel artifact.

## Further documentation

- [Architecture, invariants, and decision log](docs/ARCHITECTURE.md)
- [Installation, configuration, permissions, security, and troubleshooting](docs/OPERATIONS.md)
- [Complete command and feature parity matrix](docs/PARITY.md)
- [Compatibility and migration notes](docs/MIGRATION.md)
- [Verification evidence](docs/VERIFICATION.md)
- [Security policy and vulnerability reporting](SECURITY.md)
- [Contribution guidelines](CONTRIBUTING.md)

## License

MIT
