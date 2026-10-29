# Installation, configuration, operations, and troubleshooting

## Supported deployment

- macOS 14 or later with Messages.app signed in.
- `imsg` installed with `brew install steipete/tap/imsg` or selected through
  `IMSG_BINARY`.
- Hermes running in a user context that can access Messages.app.
- Full Disk Access for the complete process chain that reads
  `~/Library/Messages/chat.db`.
- Automation permission for Messages.app when sending.
- Accessibility and Automation permission for the basic most-recent-message
  reaction helper.
- Optional `ffmpeg` for supported inbound attachment conversion.

SMS also requires Text Message Forwarding from an iPhone. The live platform is
macOS-only even though `imsg` can perform some read-only work on copied
databases elsewhere.

## Install and verify discovery

```bash
cd /path/to/hermes-imsg-cli
scripts/install-local.sh
scripts/validate.sh
```

The installer links `hermes_imsg_cli/` into
`~/.hermes/plugins/imsg-platform`, preserves the existing Hermes configuration,
and enables the plugin when it can do so safely. It does not install `imsg`,
grant permissions, change SIP, or restart Hermes.

Read-only executable checks:

```bash
imsg --version
imsg status --json
imsg rpc --help
imsg chats --limit 3 --json | jq -s
```

`status` proves only the capabilities present in its JSON object. Do not infer a
private action from the version string or from a successful basic send.

## Capability probe

Inspect the action-relevant subset without exposing chat content:

```bash
imsg status --json |
  jq '{version,advanced_features,v2_ready,selectors,rpc_methods}'
```

Every private action requires `advanced_features: true` and `v2_ready: true`.
It then requires the relevant method and selectors:

| Action | Accepted method | Required selector |
| --- | --- | --- |
| Tapback add/remove | `tapback` | none |
| Edit | `message.edit` | `editMessage` or `editMessageItem` |
| Unsend | `message.unsend` | `retractMessagePart` |
| Threaded reply/effect | `send.rich` | none |
| Native attachment | `send.attachment` | none |
| Group operation | matching `group.*` method | none |
| Poll create | `poll.send` or `messages.poll.send` | `pollPayloadMessage` |
| Poll vote | `poll.vote` or `messages.poll.vote` | `pollVoteMessage` |
| Poll unvote | an advertised unvote alias | `pollVoteMessage` |

The client repeats this read-only check before each advanced mutation. Missing
capability returns a structured error before the mutation command starts.

## Recommended basic configuration

Use this shape on a SIP-enabled host where private bridge actions are not
available:

```yaml
plugins:
  enabled:
    - imsg-platform
platforms:
  imsg:
    enabled: true
    extra:
      binary: /opt/homebrew/bin/imsg
      auto_launch: false
      send_read_receipts: false
      reaction_notifications: "off"
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
      group_action_admins:
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

On a host where the private bridge has already been deliberately configured,
set `auto_launch: true` to let the plugin re-arm it on connect and enable
`send_read_receipts` only after the read action has been tested in that same
launch context. Native action availability remains controlled by the live
capability object.

## Access rules

Rules are evaluated in order and the first match wins. Every populated
condition must match.

- `action`: `allow`, `deny`, `prompt`, or `silent_drop`
- `context`: `dm`, `group`, or `any`
- `senders`: a handle list or `any`
- `chats`: a Messages rowid list or `any`
- `require_participants`: `any_of` and `all_of` handle lists for groups
- `require_mention`: a group-only boolean

Without a `rules` block, the adapter synthesizes equivalent rules from the
simple allowlists. No match uses `fallback_action`; its default is
`silent_drop`. Self messages are always suppressed.

`participant_validation: fast` uses participant data already present in the
watch event. `strict` runs `imsg group --chat-id <rowid> --json`, caches the
result for the configured TTL, and fails closed on command or JSON errors.

Group action authorization is separate from conversation admission. A sender
may talk to the agent in an allowed group without being able to rename it,
change its icon, change membership, or leave it. Non-local callers must match
`group_action_admins`; when omitted, that list defaults to `allowed_handles`.
Only interactive local CLI, TUI, desktop, Codex, and local sessions bypass this
sender check. Unauthenticated API and webhook contexts fail closed.

## Environment-only configuration

Set `IMSG_ENABLED=true` and the required values. Environment values take
precedence over YAML.

| Variable | Purpose |
| --- | --- |
| `IMSG_BINARY` | Executable path. |
| `IMSG_HOME_CHANNEL` | Cron and standalone delivery rowid or handle. |
| `IMSG_ALLOWED_USERS` | Comma-separated direct-message handles. |
| `IMSG_GROUP_ALLOWED_USERS` | Separate group sender allowlist. |
| `IMSG_GROUP_ACTION_ADMINS` | Authenticated senders allowed to mutate groups. Defaults to `IMSG_ALLOWED_USERS`. |
| `IMSG_ALLOWED_CHATS` | Group chat rowids. |
| `IMSG_ALLOW_ALL_USERS` | Development-only admission. Self events remain suppressed. |
| `IMSG_REQUIRE_MENTION` / `IMSG_MENTION_PATTERNS` | Group mention gating and patterns. |
| `IMSG_GROUP_SILENCE` | Default `true`: unaddressed group messages may end silently (`reply_expected: false`). `false` keeps Hermes' visible silence warning. |
| `IMSG_AUTO_LAUNCH` | Re-arm an already configured private bridge on connect. |
| `IMSG_SEND_READ_RECEIPTS` | Best-effort private bridge read marking. |
| `IMSG_REACTION_NOTIFICATIONS` | `own`, `all`, or `off`; default `off`. Opt-in modes can start agent turns. |
| `IMSG_FALLBACK_ACTION` | `silent_drop`, `deny`, `prompt`, or `allow`. |
| `IMSG_PARTICIPANT_VALIDATION` / `IMSG_PARTICIPANT_CACHE_TTL` | `fast` or `strict`, plus cache seconds. |
| `IMSG_DEBOUNCE` | Watch debounce such as `500ms`. |
| `IMSG_STATE_FILE` | Recovery cursor override. |
| `IMSG_PHONE_MIN_DIGITS` / `IMSG_DEFAULT_COUNTRY_CODE` | Standalone bare-number normalization. |
| `IMSG_COMMAND_TIMEOUT` | Timeout for bounded commands; default 30 seconds. |
| `IMSG_POLL_VOTES_DM` | Deliver direct-message poll vote events. |
| `IMSG_POLL_VOTES_GROUP` | Deliver group poll vote events. |
| `IMSG_POLL_UNVOTES` | Include poll vote removal events. |

## Reaction and poll event policy

The default `off` mode omits `--reactions` from the watch command. If a reaction
still arrives, the adapter advances the recovery cursor and drops it before
authorization, ownership/history lookup, read receipts, conversion, or agent
dispatch. Configure an explicit opt-in only when Tapbacks should initiate agent
turns:

- `own`: deliver reactions to bot-authored messages only
- `all`: deliver every authorized inbound reaction
- `off`: suppress reaction notifications; this is the default

`own` first checks a bounded cache of confirmed sent message IDs and then uses
a same-chat history lookup if needed. Admission policy runs before that lookup.
Both `own` and `all` convert accepted Tapbacks into prompt-bearing Hermes
events, so group deployments should leave the mode `off` unless that behavior
is deliberately required.

Poll vote events are disabled by default. Enable direct-message and group
scopes independently:

```yaml
poll_vote_events:
  dm: true
  group: false
  chats: ["3", "16"]
  include_unvotes: false
```

The normal sender, chat, participant, and mention policy still applies. The
full poll object remains in `raw_message`.

## Native action invocation

Use `imsg_action` for native bridge actions. Each call requires exactly one of
`chat_id`, `chat_guid`, `chat_identifier`, or `to`.

| Action | Required action fields |
| --- | --- |
| `react` | `message_id`; `reaction` unless removing the one current bot tapback; optional `remove`, `part_index` |
| `edit` | `message_id`, `text`; optional fallback text and part index |
| `unsend` | `message_id`; optional part index |
| `reply` | `message_id` plus `text`, `file_path`, or both |
| `sendWithEffect` | `text`, `effect` |
| `upload-file` | `file_path`; optional `as_voice` |
| Group actions | action-specific display name, file path, or participant address |
| `poll` | `question`, 2 to 12 `options`; optional `comment`, `reply_to` |
| `poll-vote`, `poll-unvote` | `poll_guid` and exactly one option ID, index, or text |

The legacy `imsg_poll` tool remains available with `send`, `vote`, and
`unvote`. It delegates to the same resolver and capability checks.

For any action triggered by an inbound message, use that event's exact numeric
rowid. Do not replace a group rowid with a participant handle. Do not blindly
retry a mutation after an ambiguous external result.

## Read-only command examples

```bash
# Discover stable local rowids.
imsg chats --limit 10 --json | jq -s

# Inspect group metadata and participants.
imsg group --chat-id 42 --json

# Inspect recent metadata without sending.
imsg history --chat-id 42 --limit 20 --attachments --json | jq -s

# Search local history.
imsg search --query 'pizza' --match contains --limit 20 --json | jq -s

# Inspect the default watch contract in a disposable diagnostic session.
imsg watch --chat-id 42 --since-rowid 1000 --attachments --json

# Explicit reaction diagnostic only; this is not the safe platform default.
imsg watch --chat-id 42 --since-rowid 1000 --attachments --reactions --json
```

These commands can expose private message metadata on stdout. Run them only in
an operator-controlled terminal and do not paste their output into logs or
issues.

## Cron and `send_message`

The registry declares `IMSG_HOME_CHANNEL` and a standalone sender, so
out-of-process delivery does not require a live adapter. Use a rowid for a
known conversation or group. Direct phone targets are normalized because stock
Hermes may remove the leading plus sign for plugin platform names.

The standalone path validates every attachment as an existing regular file.
Multiple files are sent sequentially because `imsg` accepts one file per
command. A partial failure reports the failed index, number attempted, and
already confirmed message IDs.

## Permissions and security

1. Grant Full Disk Access to the real parent process chain, not only to an
   unrelated interactive shell.
2. Grant Automation for Messages.app to the process context that sends.
3. Treat private bridge setup as a deliberate host security decision. It
   requires disabling SIP and should normally use a dedicated Mac or bot user.
4. Keep rules and allowlists narrow. `allow_all` and fallback `allow` expose a
   personal messaging account to prompt-driven behavior.
5. Keep the group action owner list narrower than general group admission.
6. Treat message JSON and attachment paths as personal data.
7. Outbound symlinks, directories, devices, sockets, and FIFOs are rejected.
8. The recovery cursor is atomically written with mode `0600`.
9. Run `python3 scripts/privacy_scan.py` before publishing changes.
10. Read `SECURITY.md` before changing routing, redaction, state, subprocess,
    attachment, or authorization behavior.

## Operational checks

```bash
imsg --version
imsg status --json | jq '{advanced_features,v2_ready,rpc_methods,selectors}'
scripts/validate.sh
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
```

Stop the gateway before deliberately editing the cursor, and back it up first.
Lowering it can replay events. Raising it skips events.

## Troubleshooting

### Database access fails or reads are empty

Verify the database exists, Messages is signed in, and the full launching
process chain has Full Disk Access. Permissions may need to be toggled after an
application, runtime, or Homebrew upgrade.

### A send reports failure or no visible message appears

Check Messages sign-in, Automation permission, SMS forwarding, and the explicit
service. Keep the original rowid for a known conversation. The plugin requires
JSON confirmation and intentionally reports ambiguous output as failure.

### The watch process repeatedly exits

Check the bounded redacted stderr suffix. Common causes are missing Full Disk
Access, an invalid executable path, unsupported flags on an old binary, or a
database problem. The adapter drains both pipes and reconnects with bounded
backoff.

### Attachments are absent

`missing=true` means Messages has not downloaded the file. Conversion requires
`ffmpeg`. The plugin uses only resolved original or converted paths and does not
turn a display filename into a filesystem path.

### Read receipts or typing indicators fail

Inspect `status --json`. These are private bridge operations. With the bridge
unavailable, set `auto_launch: false` and `send_read_receipts: false`. Ordinary
rowid sends remain available.

### A native action is unavailable

Read the structured error and inspect the exact method and selector named
there. Update or re-arm the bridge only through the operator procedure for the
installed `imsg` release. The plugin does not fake a private action with an
ordinary text message.

### A normal response is not threaded

This is intentional. Hermes `reply_to` is response correlation and ordinary
platform text or media never turns it into an Apple Messages thread. Use
`imsg_action` with action `reply`, one exact conversation target, and the exact
same-chat message ID when a native threaded reply is intended.

### A group mutation is denied

Confirm the target metadata reports `is_group: true`. Outside an interactive
local Hermes surface, confirm session context contains the authenticated sender
and that the sender is in `group_action_admins` or the fallback DM owner list.
General group admission and an unauthenticated API context are intentionally
insufficient.

### Reaction removal without a name fails

Unnamed removal requires exactly one current bot-authored tapback in the target
message's `reactions` array. Supply the standard reaction name explicitly when
history cannot determine it safely.

### Strict participant checks always deny

Run `imsg group --chat-id <rowid> --json` under the same process identity.
Strict mode intentionally denies when that lookup or its JSON decoding fails.
