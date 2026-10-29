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
out-of-process delivery for cron and `send_message`. It also supports native
formatted text, exact same-chat replies, tapback add and remove, effects,
message edit and unsend, native polls, file and voice attachments, and group
management through per-action private bridge capability checks.

## Start with the routing invariant

> Inbound replies return to the exact Messages chat rowid that produced the
> inbound message. The plugin does not resolve a GUID for routing, match a
> participant, or guess across chats.

The ordinary reply path always preserves the captured rowid. For plain text,
the plugin first checks `imsg status --json` and uses the bridge JSON-RPC
`send` method with `transport=bridge` when that ready bridge advertises the
method. If discovery positively shows that `send` is unavailable, it uses
`imsg send --chat-id` instead. It never retries through the legacy command
after an RPC attempt. Explicit SMS and attachment sends stay on `imsg send`.
Explicit proactive sends may use a rowid, chat GUID, chat identifier, phone
number, or email address.

Hermes may supply `reply_to` as framework correlation metadata. The platform
adapter never interprets that field as a request for an Apple Messages thread:
ordinary text and media responses stay on the plain rowid route. A native
thread is created only through the explicit `imsg_action` `reply` action, which
resolves the referenced message inside the selected conversation. See
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

Native tapback targeting, edit, unsend, threaded replies, effects, polls,
group management, read receipts, and typing indicators require the optional
private bridge reported by `imsg status --json`. That bridge has a deliberate
host security tradeoff because its setup requires System Integrity Protection
to be disabled. Basic messaging remains available without it.

SMS also requires Text Message Forwarding from an iPhone. Although upstream
supports a copied-database read-only preview on Linux, this live Hermes platform
requires macOS and Messages.app.

The plugin never grants permissions, edits TCC, or changes SIP. If the binary
or required permissions are missing, it fails with an installation hint from
`hermes_imsg_cli.config.requirements_report()`.

## Install and remove the plugin

The agent-facing Hermes skill is bundled separately at
[`skills/apple/hermes-imsg-plugin`](skills/apple/hermes-imsg-plugin/SKILL.md).
Keep its `SKILL.md` and complete `references/` directory together when copying
it to `$HERMES_HOME/skills/apple/hermes-imsg-plugin`. The local plugin installer
below manages the runtime plugin only; it does not install or update the skill.

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
      group_action_admins: ["+12025550101"]      # group mutation owners
      allowed_chats: ["16", "8"]                 # Messages chat rowids
      require_mention: false
      reaction_notifications: "off"               # safe default; own/all opt in
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
| `IMSG_GROUP_ACTION_ADMINS` | Comma-separated authenticated senders allowed to mutate group metadata or membership. Defaults to `IMSG_ALLOWED_USERS`. |
| `IMSG_ALLOWED_CHATS` | Comma-separated group chat rowids. |
| `IMSG_ALLOW_ALL_USERS` | Allow every user. Use this only for development. |
| `IMSG_REQUIRE_MENTION` | Require a mention in group chats. |
| `IMSG_MENTION_PATTERNS` | Group mention patterns as a JSON array or comma- or newline-separated regular expressions. |
| `IMSG_GROUP_SILENCE` | Let the agent stay silent on group messages that don't name it or reply to it. The default is `true`; `false` restores Hermes' visible "silence marker" warning. |
| `IMSG_AUTO_LAUNCH` | Re-arm the Messages.app bridge on connect. The default is `true`. |
| `IMSG_SEND_READ_RECEIPTS` | Send read receipts for inbound messages. The default is `true`. |
| `IMSG_REACTION_NOTIFICATIONS` | Deliver inbound tapbacks for `own`, `all`, or `off`. The default is `off`; `own` and `all` intentionally turn accepted Tapbacks into agent turns. |
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

### Group silence

In a group, people mostly talk to each other. When a group message doesn't
match `mention_patterns` (default: `gordie`) and isn't a reply to one of the
agent's own messages, the plugin tells Hermes it wasn't addressed to the agent
(`reply_expected: false`). The agent can then answer with its silence marker
and nothing is sent. Without this, Hermes replaces that silence with
"The model returned only a silence marker for a message that needed a reply."

A message that names the agent or replies to it still expects an answer, and
DMs always do. Set `group_silence: false` (or `IMSG_GROUP_SILENCE=false`) to
keep Hermes' warning in groups. Hermes cores without `reply_expected` ignore
this setting.

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

The standalone path uses the same capability-gated RPC route for ordinary
text and supports file attachments through `imsg send --file`. It validates
every attachment before sending. A missing or unsafe attachment returns a
structured error rather than a false success. Because upstream accepts one
file per command, the plugin sends multiple files separately and stops at the
first failure. A partial failure reports
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
number and normalized to E.164. A 10-digit number receives
`IMSG_DEFAULT_COUNTRY_CODE`, which defaults to `+1`. Shorter numeric targets
remain chat rowids. Ordinary text uses the normalized value as the exact RPC
`to` or `chat_id` target when bridge `send` is available, with legacy command
flags used only by the compatibility path.

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

## Use native actions explicitly

The plugin registers `imsg_action` for private bridge actions and keeps
`imsg_poll` as a backward-compatible poll entry point. Ordinary text never
implicitly becomes an edit, reaction, poll, effect, or group mutation.

`imsg_action` supports:

- `react`, including named or inferred removal and message part indexes
- `edit` and `unsend` for bot-authored messages
- `reply` with text, one attachment, or both
- `sendWithEffect` with supported short names or canonical Apple effect IDs
- `upload-file`, optionally as a voice attachment
- `renameGroup`, `setGroupIcon`, `addParticipant`, `removeParticipant`, and
  `leaveGroup`
- `poll`, `poll-vote`, and `poll-unvote`

Every call supplies exactly one `chat_id`, `chat_guid`, `chat_identifier`, or
`to` target. Message and poll references are resolved inside that conversation
before mutation. Group actions require a live group target. Callers outside an
interactive local Hermes surface must also match `group_action_admins`, which
defaults to the DM owner allowlist.

Vote and unvote selectors may be an exact option UUID, a one-based index, or
case-insensitive option text. Unvote removes only the resolved option. Polls
accept 2 to 12 options and an optional same-chat reply reference.

Before every capability-gated bridge mutation, the client runs the read-only
`imsg status --json` check. It requires the private bridge flags, the action's
advertised RPC method, and any required selector. Advanced actions fail before
mutation when capability is missing. Ordinary plain text alone may use the
legacy send command when discovery positively shows that bridge `send` is
unavailable.

Native actions require the optional bridge started by `imsg launch`. The plugin
may re-arm an already configured bridge when `auto_launch` is enabled. It does
not disable SIP, install or sign a dylib, grant TCC access, or change the host's
security settings.

## Public and advanced feature boundaries

Chat listing, group lookup, history, search, watch, file send, explicit SMS,
and the most-recent-message standard reaction path use the local Messages
database and Messages automation. Ordinary plain text prefers bridge RPC when
the ready bridge advertises `send`, then uses Messages automation only when
read-only discovery shows that method is unavailable. Basic operation remains
compatible with installations that do not provide private bridge injection.

Targeted tapback add and remove, edit, unsend, threaded reply, effects, native
polls, group management, read receipts, and typing indicators use the private
bridge. Each native action is gated independently by the methods and selectors
reported by the installed `imsg` build.

For a normal SIP-enabled Mac, use:

```yaml
platforms:
  imsg:
    extra:
      auto_launch: false
      send_read_receipts: false
```

When the private bridge is unavailable, ordinary replies remain exact rowid
sends. The [supported functionality catalog](docs/PARITY.md) lists the basic
and private paths with their operational requirements.

## Security and privacy

Read [SECURITY.md](SECURITY.md) before changing routing, subprocess handling,
attachments, logs, or local state. The following properties are part of the
plugin's operating contract:

- Ordinary replies use the trusted positive rowid. A threaded reply uses a
  message GUID only through the explicit `imsg_action` `reply` action after a
  same-chat lookup. Framework `reply_to` metadata never requests native
  threading.
- Inbound quote context is exposed only when the event contains a non-empty
  `thread_originator_guid` (or its camel-case alias). The unreliable
  `reply_to_guid` field alone is ignored.
- Tapbacks are silent by default. In `off` mode the watch process does not
  request reaction events, and any reaction that still arrives is dropped
  immediately after cursor persistence, before authorization, history lookup,
  read receipts, or agent dispatch. `own` and `all` are explicit prompt-bearing
  opt-ins.
- Native message and poll references are verified in the selected conversation
  before mutation. Edit and unsend also require a bot-authored history record.
- Group mutations outside interactive local Hermes surfaces require an
  authenticated sender in the owner allowlist. Group conversation access and
  unauthenticated API context are insufficient.
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
  identifiers before they reach a log line or agent-visible error. Plugin-owned
  rule, participant, and action logs omit the known chat rowid.
- A timed-out or cancelled subprocess receives a terminate signal first. On
  POSIX, the plugin isolates each invocation in a process group and signals
  the whole group so helpers such as `osascript` cannot survive their parent.
  It bounds that wait and then force-kills the group if needed. Non-POSIX
  platforms retain bounded direct-process cleanup.
- The platform registry deliberately leaves `pii_safe` unset. The
  `imsg_action` and `imsg_poll` tools must receive exact conversation and
  message references; hashing those values would break native actions without
  improving routing privacy. See `build_entry_kwargs()` for the registry entry.
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
  attachments.py          # shared outbound regular-file validation
  rules.py                # Hermes-independent rules and redaction
  config.py               # configuration, requirements, state path, and target normalization
  cli.py                  # pure argument builders and subprocess runners
  client.py               # bounded async CLI, RPC, and NDJSON client
  formatting.py           # supported markup to native UTF-16 formatting ranges
  capabilities.py         # immutable per-action private bridge capability model
  actions.py              # exact-target Hermes native action tools
tests/                    # pytest suite; no live sends
docs/                     # architecture, operations, functionality, migration, and verification
scripts/
  install-local.sh        # local symlink, enablement, and discovery probe
  validate.sh             # tests and import/registration probe
  validate_site.py        # deterministic static-site and Pages workflow checks
  privacy_scan.py         # repository privacy and security scanner
.github/workflows/ci.yml  # pinned-action macOS CI
.github/workflows/pages.yml # pinned-action GitHub Pages deployment from site/
site/                     # self-contained static project site and design authority
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

Validate the static release site and Pages workflow:

```bash
python scripts/validate_site.py
```

Tests never send a live iMessage or SMS. They also do not react, edit, unsend,
create or vote on polls, change groups, or mark a real chat read. Adapter and
registration tests call `pytest.importorskip("gateway")`, so the pure rule,
normalization, command-builder, and client tests can run without a Hermes
checkout.

The CI workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) is
configured for macOS with action versions pinned by commit SHA. It runs the
tests, byte-compiles the Python sources, checks shell syntax, runs the privacy
and security scan, validates the static site, checks pull-request diffs for
whitespace errors, builds a wheel, and uploads the wheel artifact. The separate
[`pages.yml`](.github/workflows/pages.yml) workflow validates and deploys
`site/` from `main`; repository Pages enablement remains an owner-controlled
setting.

## Further documentation

- [Architecture, invariants, and decision log](docs/ARCHITECTURE.md)
- [Installation, configuration, permissions, security, and troubleshooting](docs/OPERATIONS.md)
- [Supported functionality catalog](docs/PARITY.md)
- [Compatibility and migration notes](docs/MIGRATION.md)
- [Verification evidence](docs/VERIFICATION.md)
- [Security policy and vulnerability reporting](SECURITY.md)
- [Contribution guidelines](CONTRIBUTING.md)

## License

MIT
