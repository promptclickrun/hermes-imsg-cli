# Installation, configuration, operations, and troubleshooting

## Supported deployment

- macOS 14+ with Messages.app signed in.
- `imsg` installed from the upstream-maintained tap: `brew install steipete/tap/imsg`.
- Hermes running under a user session able to access Messages.
- Full Disk Access for the complete launcher chain that reads `~/Library/Messages/chat.db`.
- Automation permission for Messages.app for send; Accessibility + Automation for public `react`.
- Optional `ffmpeg` for CAF/GIF receive conversion.

SMS additionally requires Text Message Forwarding from an iPhone. The plugin is disabled on Linux even though upstream offers copied-database read-only preview: a live Hermes platform needs macOS Messages.app.

## Install and verify discovery

```bash
cd /path/to/hermes-imsg-cli
scripts/install-local.sh
scripts/validate.sh
```

The installer symlinks `hermes_imsg_cli/` to `~/.hermes/plugins/imsg-platform`, backs up the Hermes config, and enables `imsg-platform`. It does not install `imsg`, grant permissions, change SIP, or restart Hermes.

Manual executable checks:

```bash
imsg --version
imsg status --json
imsg chats --limit 3 --json | jq -s
```

`status` succeeding proves the executable runs, not that every private bridge selector is available. Use its JSON capability fields when diagnosing bridge features.

### Poll compatibility gate

Native poll operations require a poll-capable upstream build and an injected
IMCore bridge. Inspect only; do not infer support from the version string:

```bash
imsg status --json | jq '{version,advanced_features,v2_ready,selectors,rpc_methods}'
```

For create, confirm `rpc_methods` contains `poll.send` (or
`messages.poll.send`) and `selectors.pollPayloadMessage` is `true`. For vote,
confirm `poll.vote`/`messages.poll.vote` and `selectors.pollVoteMessage`. For
unvote, confirm one of `poll.unvote`, `polls.unvote`, or
`messages.poll.unvote`, plus `pollVoteMessage`. The Hermes client/tool performs
this read-only check before every mutation and returns a structured error if
either half is absent. The locally observed 0.11.0 binary does not advertise
these methods; upgrade to the reviewed poll-capable upstream build before an
operator attempts live verification.

## Recommended public-only configuration

```yaml
plugins:
  enabled:
    - imsg-platform
platforms:
  imsg:
    enabled: true
    extra:
      binary: /opt/homebrew/bin/imsg
      auto_launch: false              # recommended on normal SIP-enabled Macs
      send_read_receipts: false       # bridge-dependent; enable only after testing
      poll_vote_events:
        dm: false
        group: false
        chats: []
        include_unvotes: false
      debounce: 500ms
      participant_validation: fast
      participant_cache_ttl: 300
      fallback_action: silent_drop
      allowed_handles:
        - "+12025550101"
      group_allowed_handles:
        - "+12025550101"
      allowed_chats:
        - "42"
      require_mention: true
      mention_patterns:
        - '(?<![\w@])@?gordie\b[,:\-]?'
      identity_aliases:
        "+12025550101": Owner
      rules:
        - name: owner-dm
          context: dm
          senders: ["+12025550101"]
          action: allow
        - name: family-mentioned
          context: group
          chats: ["42"]
          senders: any
          require_mention: true
          action: allow
    home_channel:
      platform: imsg
      chat_id: "42"
      name: Family
```

If `rules` is present it is evaluated first-match, top-down. Every populated condition in a rule must match. `senders: any` and `chats: any` mean unconstrained. `require_participants.any_of/all_of` applies only to groups. If no rules exist, legacy allowlists are synthesized. No match uses `fallback_action`; leave it at `silent_drop` unless pairing middleware intentionally handles `prompt`.

### Strict group participant validation

`participant_validation: fast` trusts participants already present in the upstream watch event. `strict` runs `imsg group --chat-id <rowid> --json` and caches the result for the TTL. Strict mode fails closed on command/JSON error, but adds a subprocess and may require Full Disk Access at decision time.

## Environment-only configuration

Set `IMSG_ENABLED=true` plus the needed values. Environment wins over YAML.

| Variable | Purpose |
|---|---|
| `IMSG_BINARY` | Executable path. |
| `IMSG_HOME_CHANNEL` | Cron/send target rowid or handle. |
| `IMSG_ALLOWED_USERS` | Comma-separated DM handles. |
| `IMSG_GROUP_ALLOWED_USERS` | Separate group sender allowlist. |
| `IMSG_ALLOWED_CHATS` | Group chat rowids. |
| `IMSG_ALLOW_ALL_USERS` | Development only; self messages are still dropped. |
| `IMSG_REQUIRE_MENTION` / `IMSG_MENTION_PATTERNS` | Group gating. Patterns may be JSON array or comma/newline separated regex. |
| `IMSG_AUTO_LAUNCH` | Preserve optional `imsg launch` bridge re-arm. Set false for public-only operation. |
| `IMSG_SEND_READ_RECEIPTS` | Best-effort bridge `read`; set false if bridge unavailable. |
| `IMSG_FALLBACK_ACTION` | `silent_drop`, `deny`, `prompt`, or `allow`. |
| `IMSG_PARTICIPANT_VALIDATION` / `IMSG_PARTICIPANT_CACHE_TTL` | `fast`/`strict` and seconds. |
| `IMSG_DEBOUNCE` | Upstream watch debounce, such as `500ms`. |
| `IMSG_STATE_FILE` | Cursor file override. |
| `IMSG_PHONE_MIN_DIGITS` / `IMSG_DEFAULT_COUNTRY_CODE` | Standalone bare-number normalization. |
| `IMSG_COMMAND_TIMEOUT` | Seconds before a bounded read/send/action is killed (default 30); does not apply to long-running watch. |
| `IMSG_POLL_VOTES_DM` | Deliver inbound poll vote events from direct messages (default false). |
| `IMSG_POLL_VOTES_GROUP` | Deliver inbound poll vote events from group chats (default false). |
| `IMSG_POLL_UNVOTES` | Include poll unvote/removal events when vote events are enabled (default false). |

### Poll vote event policy

`imsg watch` emits poll creation, vote, and unvote rows. Hermes converts vote
rows into agent-readable messages only when the matching scope is enabled:

```yaml
platforms:
  imsg:
    extra:
      poll_vote_events:
        dm: true
        group: false
        chats: ["3", "16"]
        include_unvotes: false
```

The switches are default-off and apply after normal Hermes access control.
When enabled, existing sender/chat rules, participant validation, and group
mention requirements still apply. A vote becomes text such as `Poll vote: Yes
on "Dinner?"`; the full upstream poll payload remains in `raw_message` for
structured tools and audit logic. `include_unvotes` controls removal events
separately from selected-vote events. A non-empty `chats` list adds an exact
Messages chat-rowid allowlist on top of the DM/group switches.

## Safe command examples

```bash
# Discover a stable local rowid.
imsg chats --limit 10 --json | jq -s

# Inspect group metadata and participants.
imsg group --chat-id 42 --json

# Read and convert supported attachment formats for model use.
imsg history --chat-id 42 --limit 20 --attachments --convert-attachments --json | jq -s

# Search local history.
imsg search --query 'pizza' --match contains --limit 20 --json | jq -s

# Resume a diagnostic watch after a known cursor.
imsg watch --chat-id 42 --since-rowid 1000 --attachments --convert-attachments --json

# Explicit direct outbound send; verify recipient/content first.
imsg send --to '+12025550101' --text 'On my way' --service auto --json

# Same-chat send by rowid.
imsg send --chat-id 42 --text 'Hello family' --json

# Public standard tapback (most recent incoming message only).
imsg react --chat-id 42 --reaction like --json
```

For Python integrations that need bounded calls without importing Hermes:

```python
from hermes_imsg_cli.client import ImsgClient

client = ImsgClient("/opt/homebrew/bin/imsg", timeout=30)
chats = await client.chats(limit=10)
messages = await client.history("42", limit=20, convert_attachments=True)
hits = await client.search("pizza", match="contains")
```

## Native poll invocation

Use the registered `imsg_poll` Hermes tool for agent actions. It is deliberately
separate from ordinary text send. Parameters:

- `action=send`: exactly one `chat_id` or `chat_guid`, non-empty `question`, at
  least two non-empty `options`; optional `comment` caption override and
  `reply_to` message GUID.
- `action=vote|unvote`: exactly one target, `poll_guid`, and exactly one of
  `option_id`, 1-based `option_index`, or option `option` text.
- For any operation arising from an inbound message, use its captured numeric
  `chat_id` exactly. Do not pass its participant handle or resolve through a
  phone/email. `chat_guid` is for explicit proactive operations only.

Equivalent client examples (these mutate Messages when actually run):

```python
created = await client.send_poll(
    "42", "Dinner?", ["Pizza", "Sushi"],
    comment="Vote by 5pm", reply_to="OPTIONAL-PARENT-GUID",
)
vote = await client.vote_poll("42", "POLL-GUID", option_index=2)
unvote = await client.unvote_poll("42", "POLL-GUID", option_id="OPTION-UUID")
```

Poll creation uses the `poll.send` JSON-RPC mutation path, matching OpenClaw's
long-lived `imsg rpc` caller rather than the short-lived `imsg poll send` CLI
path. This matters because the CLI path's caption echo can be interpreted as a
second native poll by some bridge/runtime combinations. Vote and unvote retain
the CLI selector path while their RPC option-resolution parity is completed.
Do **not** blindly retry any poll mutation after an ambiguous result.

## Cron and `send_message`

The plugin registry declares `IMSG_HOME_CHANNEL` and a standalone sender, so out-of-process delivery does not require a live adapter. Address groups/replies with a short Messages rowid (`imsg:42`). Direct phones may be `imsg:12025550100`; the plugin rehydrates `+` because stock Hermes strips it for unknown phone platform names. Ten-digit numbers use `IMSG_DEFAULT_COUNTRY_CODE` (default `1`). Tune `IMSG_PHONE_MIN_DIGITS` if local rowids could be unusually long.

Before file delivery the standalone path verifies every file exists and is a genuine regular file — symlinks and special files (directories, FIFOs, sockets, device nodes) are rejected. Upstream accepts one file per `send`; multiple files are emitted separately, fail-fast, with a deterministic partial-failure report (`failed_index`, `attempted_attachments`, `succeeded_message_ids`) if one fails partway through. The plugin never reports a missing or unsafe file as success.

## Permissions and security

1. Grant Full Disk Access to the actual parent process chain (Terminal, launch agent, editor, Hermes runtime as applicable). macOS may also consult Terminal.app's grant.
2. Grant Automation → Messages to the launcher for plain sends.
3. Public `react` uses System Events/UI automation and additionally needs Accessibility.
4. Contacts resolution is optional and owned by upstream; raw handles remain available.
5. Do not disable SIP merely to make the bot work. Basic chats/history/search/watch/plain send/react do not require it. Native polls do require the optional injected bridge; enabling that environment remains an operator decision outside this plugin.
6. If advanced features are deliberately tested, follow upstream's exact versioned documentation, understand macOS 26 library-validation/private-entitlement limits, and re-enable SIP afterward.
7. Keep allowlists/rules narrow. `allow_all` and fallback `allow` are unsafe for an agent connected to personal Messages.
8. Treat attachment paths and message JSON as personal data. Logs redact phone/email strings, home-directory paths, and GUID-like identifiers, never include a raw `imsg watch` line, and omit known chat rowids from plugin-owned rule, participant, and action messages. Debug logs and upstream stderr can still reveal operational context.
9. Outbound attachments must be regular files; a symlink is rejected even if it points at a legitimate file, since it could be swapped out between validation and use.
10. The resumable cursor (`imsg_state.json`) is written atomically with owner-only (`0600`) permissions.
11. Run `python scripts/privacy_scan.py` before publishing any change that adds example data (phone numbers, paths, names); see `SECURITY.md` for the full policy and how to report a vulnerability.

## Operational checks

```bash
# Executable and basic read
imsg --version
imsg chats --limit 1 --json

# Plugin tests / registration probe
scripts/validate.sh

# Check cursor without exposing message data
python3 -c 'import json,os; p=os.path.expanduser("~/.hermes/imsg_state.json"); print(json.load(open(p))["last_rowid"])'
```

Stop the gateway before editing the cursor. Back it up first. Lowering it can replay events; raising it skips events. Delete it only when deliberately starting at upstream watch's newest row.

## Troubleshooting

### `unable to open database file`, empty reads, or authorization denied

Verify `~/Library/Messages/chat.db` exists, Messages is signed in, and every launching parent has Full Disk Access. Toggle stale grants after app/runtime/Homebrew upgrades. Do not use a copied or immutable DB for live watch.

### Send is accepted but no visible message

Use the same target directly with `imsg send ... --json`; check Messages sign-in, Automation permission, SMS forwarding, and explicit service choice. Rowid sends are verified by upstream to detect Tahoe group ghost rows. Do not replace a group rowid with a participant handle.

### Watch stops delivering

Check logs for the bounded `stderr:` suffix. The plugin drains stderr and respawns with exponential backoff; upstream also re-arms WAL watches and polls. Repeated immediate exits usually indicate Full Disk Access, a bad `IMSG_BINARY`, unsupported flags on an older binary, or database damage. Compare installed `imsg --version` with `docs/PARITY.md`.

### Attachments are absent

Metadata `missing=true` means Messages has not downloaded the file; the plugin intentionally omits that path. Conversion requires `ffmpeg`. Inspect raw `attachments` for `path`, `converted_path`, MIME, and size. Conversion never alters the original.

### Read receipts / typing fail

These are private IMCore capabilities. Run `imsg status --json`; do not infer availability from basic sends. With SIP enabled, set `auto_launch: false` and `send_read_receipts: false`. Plain same-rowid replies are unaffected — they never depend on the bridge, since threaded/rich replies are not used at all (see `ARCHITECTURE.md`).

### Poll action is unavailable

Read the structured error and inspect `status --json`. “does not advertise”
means the installed `imsg` predates that operation. A missing
`pollPayloadMessage` or `pollVoteMessage` means the command exists but the
running bridge lacks the selector (or is not injected/current). The plugin does
not auto-launch, reinject, disable SIP, or fall back to a fake text poll. Upgrade
and restart/inject only through the upstream operator procedure, then re-check
status. On macOS 26, library validation/private-entitlement restrictions may
still block the bridge.

### Operator-only live poll verification (mutating)

Automated tests never create or vote on polls. Only after the operator has
chosen a disposable iMessage chat and reviewed every value:

1. Capture that chat's rowid with `imsg chats --json`; record its exact GUID too.
2. Verify methods/selectors with the compatibility gate above.
3. Invoke one `imsg_poll action=send` with two unmistakable options. Confirm one
   native poll balloon and one visible caption, and record the returned poll GUID
   and option UUIDs from history.
4. Invoke `action=vote` by option UUID. Re-read history and confirm the expected
   native vote event.
5. Invoke `action=unvote` with that same option UUID. Confirm only that selection
   is removed. Then try unvoting it again and confirm a structured “not currently
   selected” failure rather than success.
6. Separately test an inbound-originated action using the captured source rowid;
   confirm no participant-handle rerouting occurred.
7. Record installed `imsg` version, `status` capability subset, timestamps, and
   returned redacted result objects. Never claim poll success from status alone.

### Reactions fail

Only six names are public: `love`, `like`, `dislike`, `laugh`, `emphasis`, `question`. UI automation reacts to the newest incoming message and needs Messages + System Events permissions. Custom emoji and removal are intentionally not exposed without the bridge.

### Strict participant checks always deny

Run `imsg group --chat-id <rowid> --json` under the same launcher identity. Current upstream `chats` does not accept `--chat-id`; older plugin revisions used that invalid shape. A lookup or parse failure returns no participants and therefore fails gated rules closed.
