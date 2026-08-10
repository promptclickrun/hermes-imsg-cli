# Capability gates and configuration

Load this reference when deciding whether a capability is public/basic or bridge-backed, interpreting a structured failure, or configuring the plugin. Source basis: `hermes_imsg_cli/capabilities.py`, `hermes_imsg_cli/client.py`, `hermes_imsg_cli/cli.py`, `hermes_imsg_cli/plugin.yaml`, `tests/test_cli_parity.py`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, and `README.md`.

## Capability boundaries

Bridge-independent paths:

- chat listing,
- group lookup,
- history,
- search,
- supervised watch,
- explicit SMS through legacy `imsg send`,
- ordinary regular-file send through legacy `imsg send`,
- basic most-recent-message standard reaction helper.

Capability-selected ordinary unformatted iMessage/chat text:

- Run `imsg status --json` before mutation.
- Use JSON-RPC method `send` with `transport=bridge` only when `advanced_features: true`, `v2_ready: true`, and `rpc_methods` contains `send`.
- Use legacy `imsg send` only when a successfully decoded status object positively lacks that complete gate. Only literal `true` readiness flags and the exact advertised method count as present; missing or malformed individual gate fields count as absent under the current client parser.
- Abort on a status command failure, JSON decoding failure, or non-object result. Do not treat incomplete discovery as proof that bridge send is unavailable.
- After an RPC process starts, never switch to legacy send because of an RPC error, timeout, cancellation, malformed response, or other ambiguous result.

Private bridge path:

- exact-message tapback add/remove,
- edit and unsend,
- native threaded reply,
- expressive effects,
- native file/voice action,
- native polls,
- group metadata and membership changes,
- read receipts,
- typing indicators.

Explicit SMS and ordinary attachment messaging remain usable when a private action is unavailable. Ordinary unformatted iMessage/chat text remains compatible with a bridge-free installation only after successful capability discovery selects the legacy path.

## Capability probe and native-action gate

Before each advanced mutation, and before an ordinary unformatted iMessage/chat text send chooses its transport, the client runs:

```bash
imsg status --json
```

Every advanced action requires:

- `advanced_features: true`,
- `v2_ready: true`,
- at least one accepted RPC method for that action,
- at least one required selector marked exactly `true`, when applicable.

Malformed status fields are ignored. For an advanced action, a missing gate returns a structured error and the mutation is not attempted.

For ordinary unformatted text, `ImsgCapabilities.supports_bridge_rpc_method("send")` requires both readiness flags and method `send`. A false result from a successfully decoded status object, including one with missing or malformed individual gate fields, selects legacy send before mutation. A status execution failure, JSON decoding failure, or non-object result raises an error and selects no transport. Text with authored formatting ranges follows the existing JSON-RPC `send` path with `transport=auto` and does not run this unformatted-text probe.

## Per-action contracts

| `imsg_action` value | Accepted RPC method | Required selector |
| --- | --- | --- |
| `react` | `tapback` | none |
| `edit` | `message.edit` | `editMessage` or `editMessageItem` |
| `unsend` | `message.unsend` | `retractMessagePart` |
| `reply` | `send.rich` | none |
| `sendWithEffect` | `send.rich` | none |
| `upload-file` | `send.attachment` | none |
| `renameGroup` | `group.rename` | none |
| `setGroupIcon` | `group.setIcon` | none |
| `addParticipant` | `group.addParticipant` | none |
| `removeParticipant` | `group.removeParticipant` | none |
| `leaveGroup` | `group.leave` | none |
| `poll` | `poll.send` or `messages.poll.send` | `pollPayloadMessage` |
| `poll-vote` | `poll.vote` or `messages.poll.vote` | `pollVoteMessage` |
| `poll-unvote` | `poll.unvote`, `polls.unvote`, or `messages.poll.unvote` | `pollVoteMessage` |

Inspect only capability metadata through `terminal`:

```bash
imsg status --json
```

Do not infer private capability from `imsg --version`, a successful basic send, or a selector without the matching method.

## Recommended SIP-enabled configuration

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
      reaction_notifications: off
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
    home_channel:
      platform: imsg
      chat_id: "42"
      name: Example Group
```

Environment values override YAML.

## Configuration keys

| YAML `extra` key | Purpose |
| --- | --- |
| `binary` | Exact `imsg` executable path. |
| `allow_all` | Development-only broad admission. |
| `allowed_handles` | DM sender allowlist. |
| `group_allowed_handles` | Separate group sender allowlist. |
| `group_action_admins` | Authenticated group-mutation owners. |
| `allowed_chats` | Exact allowed group rowids. |
| `rules` | Ordered first-match policy rules. |
| `identity_aliases` | Readable names for policy-matched handles. |
| `require_mention` | Group mention gate. |
| `mention_patterns` | Mention regular expressions. |
| `reaction_notifications` | `off` by default. `own` and `all` opt in to Tapback-triggered agent turns. |
| `poll_vote_events` | DM/group/chat/unvote delivery policy. |
| `auto_launch` | Re-arm an already configured bridge on connect. |
| `send_read_receipts` | Best-effort accepted-chat read marking. |
| `participant_validation` | `fast` or `strict`. |
| `participant_cache_ttl` | Strict lookup cache seconds. |
| `fallback_action` | `silent_drop`, `deny`, `prompt`, or `allow`. |
| `debounce` | Watch debounce such as `500ms`. |
| `state_file` | Recovery cursor override. |
| `command_timeout` | Bounded CLI/RPC command timeout, default 30 seconds; supervised watch is excluded and cancellation cleans up the complete isolated POSIX process group. |

## Environment variables

- `IMSG_ENABLED`
- `IMSG_BINARY`
- `IMSG_HOME_CHANNEL`
- `IMSG_ALLOWED_USERS`
- `IMSG_GROUP_ALLOWED_USERS`
- `IMSG_GROUP_ACTION_ADMINS`
- `IMSG_ALLOWED_CHATS`
- `IMSG_ALLOW_ALL_USERS`
- `IMSG_REQUIRE_MENTION`
- `IMSG_MENTION_PATTERNS`
- `IMSG_AUTO_LAUNCH`
- `IMSG_SEND_READ_RECEIPTS`
- `IMSG_REACTION_NOTIFICATIONS`
- `IMSG_FALLBACK_ACTION`
- `IMSG_PARTICIPANT_VALIDATION`
- `IMSG_PARTICIPANT_CACHE_TTL`
- `IMSG_DEBOUNCE`
- `IMSG_STATE_FILE`
- `IMSG_PHONE_MIN_DIGITS`
- `IMSG_DEFAULT_COUNTRY_CODE`
- `IMSG_COMMAND_TIMEOUT`
- `IMSG_POLL_VOTES_DM`
- `IMSG_POLL_VOTES_GROUP`
- `IMSG_POLL_UNVOTES`

## Common failure interpretation

| Failure | Meaning | Next check |
| --- | --- | --- |
| private API bridge unavailable | Bridge flags are false | Keep basic mode or follow the operator-managed bridge procedure. |
| installed imsg does not advertise method | RPC method absent | Update `imsg`, re-arm, then refresh status. |
| ready status lacks ordinary method `send` | Bridge text send is positively unavailable | Select legacy text send before mutation. |
| status command or JSON decoding fails | Capability discovery is incomplete | Abort the text send; do not select a fallback transport. |
| bridge selector unavailable | Method exists but selector is false/missing | Do not attempt the action on this host build. |
| current conversation mismatch | Message or poll GUID is cross-chat/stale | Retrieve the reference from the intended chat. |
| bot-authored message required | Edit/unsend target is not `is_from_me: true` | Select an outbound message from the same chat. |
| group action admin required | Caller lacks mutation authority | Verify authenticated sender and admin allowlist. |
| exact group conversation required | Target metadata is not a group | Use the group rowid/GUID, not a participant handle. |
| unsafe attachment | Path is missing or not a regular file | Use an existing nonsymlink regular file. |
| RPC error, timeout, cancellation, or no JSON object | The mutation result is failed or ambiguous | Do not fall back or resend; reconcile read-only evidence first. |

## Split failure: healthy inbound and bridge, hung outbound transport

Inbound watch, history, read receipts, typing, and bridge status exercise different paths from legacy `imsg send`. They can remain healthy while the Messages Automation or AppleScript send path hangs and leaves an `osascript` grandchild. Do not use a successful watch event, read/typing action, or status probe as proof that legacy sending works.

Diagnose an existing hang without sending another message:

1. Establish which transport was selected. A complete ordinary-text `send` gate should produce `imsg rpc`, not legacy `imsg send`. Use the legacy branch below only when successful status lacked that gate, the request was explicit SMS or an ordinary attachment, or exact process evidence identifies `imsg send`. For a bridge RPC hang, apply the same correlation and process-group checks to `imsg rpc`; do not search only for AppleScript helpers.
2. In gateway logs, locate the same request window's response-ready and sending markers. Record only timestamps and an internal correlation token. Do not copy message text, targets, handles, GUIDs, or full command lines. Response-ready proves generation completed; sending proves dispatch began, not that Messages persisted a row.
3. Inspect `~/Library/Messages/chat.db` read-only with `sqlite3 -readonly` and `PRAGMA query_only=ON`. Compare recent `message` rows where `is_from_me=1` against the sending timestamp. Select only metadata needed for diagnosis, such as local row number, `date`, `service`, `is_sent`, `is_delivered`, and `error`; do not select text, attributed bodies, handles, participants, account fields, or GUIDs. Check `PRAGMA table_info(message)` first because macOS schema details and date units can vary. Validate timestamp normalization before comparing systems; otherwise use row ordering and label the result ambiguous.
4. Interpret the correlation conservatively. Sending marker plus no new outbound row means dispatch began but Messages did not persist the send. A row with pending/error state means Messages recorded the attempt. A sent row with a hung gateway means completion, output parsing, or child reaping may be stuck. Any plausible row means the mutation may have happened, so do not retry it. If concurrent outbound activity prevents one row from being tied to the request window, keep the outcome ambiguous.
5. Resolve exact process IDs without a broad `ps | grep` search. Find legacy sends with `pgrep -f '(^|/)[i]msg[[:space:]]+send([[:space:]]|$)'`, bridge transports with `pgrep -f '(^|/)[i]msg[[:space:]]+rpc([[:space:]]|$)'`, and helpers with `pgrep -x osascript`. The bracketed `imsg` patterns and exact process-name match avoid matching the inspection commands themselves.
6. For each candidate, inspect privacy-safe identity and ancestry fields only:

   ```bash
   ps -p <pid> -o pid=,ppid=,pgid=,stat=,lstart=,etime=,comm=
   ```

   Follow each observed PPID to the gateway/service parent and inspect descendants one level at a time with `pgrep -P <pid>`. Enumerate every member of the recorded process group with `pgrep -g <pgid>` and inspect each PID with the same `ps` fields. Confirm the actual chain and process-group IDs; do not assume the chain or print `args`/`command`, which may contain message content. An exact-name `osascript` match alone is not proof that the process belongs to this send; require matching ancestry, start time, or process group.
7. Let the configured timeout/cancellation cleanup finish unless separate recovery authority exists. The command timeout defaults to 30 seconds and is configurable; process-group cleanup then allows a default 2-second terminate grace before force-kill. Repeat the exact PID and process-group checks. Confirm that the attempted `imsg send` or `imsg rpc` PID and every related `osascript` PID are gone, no matching send/RPC remains for that gateway chain and request window, and the supervised `imsg watch` process is still healthy. Compare another read-only database snapshot for a late row before deciding the outcome.

On POSIX, both CLI and RPC runners start each subprocess in a new session and isolated process group. Timeout-driven cancellation or direct cancellation sends `SIGTERM` to the whole group, waits the bounded grace interval, then sends `SIGKILL` to the whole group if anything remains. Non-POSIX runners retain bounded direct-process cleanup. Never verify this behavior by sending a probe message.

## Host security boundary

The plugin does not install `imsg`, grant TCC permissions, sign in to Messages, disable SIP, or install/sign bridge code. Private bridge setup is an operator-owned host security decision and normally belongs on a dedicated Mac or bot user. `auto_launch` only re-arms an environment that has already been deliberately configured.
