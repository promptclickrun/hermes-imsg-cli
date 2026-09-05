# Architecture and decision log

## Boundary

```text
Messages chat.db / Messages.app
              ^
              | local command, NDJSON, and JSON-RPC contracts
       local `imsg` executable
              ^
              | bounded subprocess boundary
  cli.py <-> client.py <-> capabilities.py
              ^
              | exact target and message validation
          actions.py
              ^
              | Hermes platform and tool interfaces
          adapter.py
              ^
              | stock plugin registry
       Hermes gateway / cron
```

This repository is a Hermes platform plugin. It does not read `chat.db`
directly or reproduce the Messages schema. The installed `imsg` executable
owns database compatibility, watch behavior, contact resolution, Messages.app
automation, the optional private bridge, and native mutation mechanics.

## Module responsibilities

- `cli.py`: pure command builders, JSON-RPC request builders, target parsing,
  NDJSON parsing, and generic subprocess runners.
- `formatting.py`: supported authored markup to native Messages ranges using
  UTF-16 coordinates.
- `capabilities.py`: immutable parsing of `imsg status --json` and strict
  per-action method and selector requirements.
- `client.py`: bounded async read, send, and native mutation operations with
  structured errors and attachment validation.
- `actions.py`: Hermes schemas, exact target resolution, same-chat reference
  checks, owner authorization for group mutations, unified native dispatch,
  and the poll compatibility handler.
- `adapter.py`: Hermes event conversion, policy enforcement, trusted reply
  caches, watch supervision, reaction and poll normalization, media methods,
  read and typing hooks, tool registration, and standalone delivery.
- `attachments.py`: regular-file validation shared by adapter, client, action,
  and standalone sends.
- `rules.py`: default-deny authorization and PII redaction helpers.
- `config.py`: plugin-owned YAML and environment translation, executable
  checks, state-path resolution, explicit target parsing, and the stock Hermes
  phone-target normalization workaround.

## Non-negotiable invariants

1. **Exact ordinary reply routing.** `MessageEvent.source.chat_id` is the
   positive rowid from the inbound object. A normal reply uses that same rowid.
2. **Explicit threading only.** Framework `reply_to` is correlation metadata,
   never a native-thread request. Only the `imsg_action` `reply` action may
   create a thread, after resolving its message GUID in the selected chat.
3. **Exact native targets.** An action accepts one of `chat_id`, `chat_guid`,
   `chat_identifier`, or `to`. Conflicts fail before the client is created.
4. **Same-chat references.** Message and poll IDs are looked up in the target
   conversation. Edit and unsend require `is_from_me: true`.
5. **Fail closed on unresolved chats.** Missing, zero, invalid, or conflicting
   targets do not become direct-message-shaped operations.
6. **Self messages never invoke the agent.** Self suppression runs before
   rules, including configurations that otherwise allow every sender.
7. **Default deny.** Rules are first-match and the fallback is `silent_drop`.
   Direct-message and group allowlists remain separate.
8. **Group mutation ownership.** Group admission and unauthenticated
   programmatic context do not grant mutation authority. Non-local callers must
   match the group action admin list.
9. **No permission automation.** The plugin never edits TCC, disables SIP,
   installs or signs a dylib, or signs in to Messages.
10. **Capability honesty.** Every private action requires both private bridge
    flags, an advertised RPC method, and any required selector before mutation.
11. **No payload leakage.** Raw watch lines are never logged. Errors redact
    phone numbers, emails, home paths, and stable GUID-like identifiers.
12. **Confirmed mutation results.** A zero exit code is insufficient. A send or
    mutation must return a structured JSON success object.
13. **Safe files.** Outbound paths must be existing regular files. Symlinks and
    special files are rejected before command execution.
14. **Safe local state.** The cursor is written atomically with mode `0600`.

## Event flow

1. `connect()` checks the executable and may re-arm a previously configured
   private bridge when `auto_launch` is enabled.
2. `_watch_loop()` starts one `imsg watch --json --attachments
   --convert-attachments` process with the persisted `--since-rowid` cursor.
   It adds `--reactions` only for the explicit `own` or `all` modes.
3. Stdout is consumed one line at a time. Stderr is drained concurrently into
   a bounded redacted tail so neither pipe can block the process.
4. A malformed line is dropped with only its size logged. Its content is not
   copied to a log message.
5. The cursor advances before policy handling so unauthorized or malformed
   events cannot replay forever.
6. In the default `off` mode, reactions are dropped immediately after cursor
   persistence, before authorization, history lookup, read receipts, or agent
   dispatch.
7. Self and unresolved events are dropped. Sender, chat, participant, mention,
   and fallback policy is evaluated. Reactions in opt-in `own` or `all` mode
   and poll events then receive their configured filters.
8. Accepted objects become Hermes `MessageEvent` values with the complete
   decoded object retained in `raw_message`.
9. Hermes replies by exact rowid. A native thread requires a separate explicit
   `imsg_action` `reply` invocation.

## Reply routing

The rowid remains the routing authority for every ordinary platform response.
Hermes can pass `reply_to` for response correlation, including a direct previous
message ID, but the adapter intentionally ignores it when selecting the send
operation. Text and media responses therefore stay plain and unthreaded.

Native threading is an explicit mutation. The `imsg_action` `reply` handler
accepts one exact conversation target and a message GUID, looks up that message
inside the selected chat, and only then calls the bridge reply method. This
separates framework response bookkeeping from user-visible Apple Messages
threading.

Inbound reply context follows the same explicit-evidence rule. A non-empty
`thread_originator_guid` or `threadOriginatorGuid` remains authoritative. For a
short confirmation without one, the adapter may perform a bounded history read
and expose context only after the current row resolves to a bot-authored
question in the exact source chat. `reply_to_guid` alone remains ignored because
it is not reliable evidence of the user's intended referent.

## Native action flow

1. Validate the action name and reject fields not accepted by that action.
2. Enforce the group action owner boundary before creating a client or reading
   chat metadata.
3. Resolve exactly one conversation target.
4. Confirm that group actions target a group.
5. Resolve message or poll IDs from up to 1,000 records in that same chat.
6. Confirm bot authorship for edit and unsend.
7. Validate any file path.
8. Probe `status --json` and require the action's method and selectors.
9. Execute one native mutation and require structured JSON confirmation.
10. Return the action, resolved target, and native result or a redacted error.

The compatibility `imsg_poll` handler maps `send`, `vote`, and `unvote` onto
the same dispatcher, so target, capability, and failure behavior cannot drift.

## Capability model

Each private action has an immutable requirement containing one or more
accepted method names and, where needed, one or more acceptable selectors.
All actions require `advanced_features: true` and `v2_ready: true`.

Edit accepts either supported edit selector. Unsend requires the retract
selector. Poll creation requires the poll payload selector. Vote and unvote
require the poll vote selector. A malformed or incomplete status object behaves
as unavailable and stops the mutation.

Status is read lazily at action time. This allows a newly re-armed bridge to
serve the first action without requiring a separate manual refresh.

## Cursor and reconnect model

The state file stores `last_rowid` and an update timestamp under the active
Hermes home unless overridden. It is written to a same-directory temporary
file, set to mode `0600`, and atomically replaced.

The plugin supervises the watch process, drains both pipes, and restarts an
ended stream with exponential backoff from 1 to 30 seconds. A valid stdout
event resets the backoff; process creation alone does not.

The delivery model is at-least-once around process restarts but intentionally
stores the cursor before agent dispatch. This favors loop prevention over
replaying an event whose later agent invocation failed.

## Attachment decisions

- Request attachment metadata and conversion on watch and history.
- Prefer `converted_path`, then an original resolved path.
- Ignore objects marked `missing=true`.
- Treat `filename` and `file` as display metadata, not local paths.
- Validate every outbound path as a regular file.
- Use one file per command. Multi-file standalone sends execute sequentially
  and report a deterministic partial failure.
- An explicit native `reply` action accepts at most one attachment.

## Plain-send transport decisions

- Preserve the exact caller-selected rowid, chat GUID, chat identifier, or
  recipient handle across capability discovery.
- For ordinary plain text, use JSON-RPC `send` with `transport=bridge` only
  when a ready bridge advertises that method.
- Use legacy `imsg send` only before mutation when discovery positively shows
  that bridge `send` is unavailable. A discovery error fails closed.
- Never retry through legacy send after an RPC attempt, timeout, or ambiguous
  failure because that could duplicate a message.
- Keep explicit SMS and every attachment send on the legacy command path.
- Start each POSIX CLI or RPC subprocess in its own process group and signal
  the complete group on cancellation. Keep direct-process cleanup elsewhere.

## Reaction decisions

The normal adapter reaction helper supports the six reliable standard
tapbacks. The native action tool targets an exact same-chat message and supports
addition, removal, and part indexes. If removal omits a reaction name, the
handler inspects the message's current `reactions` array and requires exactly
one bot-authored tapback to remove.

Inbound reaction mode defaults to `off`. The default watch command omits
`--reactions`, and defense-in-depth drops any reaction that still arrives after
advancing the recovery cursor but before authorization, ownership/history
lookup, read receipts, conversion, or agent dispatch. Modes `own` and `all`
remain explicit opt-ins because accepted reaction events become agent turns.
In `own` mode, the adapter checks a bounded cache of sent message GUIDs and then
performs a same-chat history lookup when needed; authorization runs before that
lookup.

## Poll decisions

Creation uses one bounded JSON-RPC request. Vote and selective unvote use the
validated native command path. Options accept an exact ID, a one-based index,
or text. The upstream bridge remains responsible for decoding a poll, resolving
option membership, and confirming current selection.

Polls are never inferred from ordinary text. Inbound vote delivery is disabled
by default and can be enabled separately for direct messages and groups.

## Group mutation decisions

The handler requires live chat metadata with `is_group: true`. Only interactive
local surfaces such as CLI, TUI, desktop, and Codex are treated as local
operators. Every other surface obtains its authenticated sender from Hermes
task-local session context. That sender must match
`IMSG_GROUP_ACTION_ADMINS`, or `IMSG_ALLOWED_USERS` when the dedicated setting
is absent. An empty identity or owner list denies non-local callers, including
unauthenticated API and webhook contexts.

## Decision log

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-07-13 | Parse command output as NDJSON while accepting one compatibility array. | The command contract is one JSON object per line and its schema is additive. |
| 2026-07-15 | Require structured send confirmation and validate outbound files. | Process exit alone cannot prove delivery, and special files are unsafe send inputs. |
| 2026-07-15 | Persist the recovery cursor atomically with owner-only permissions. | A partial or broadly readable state file weakens recovery and local privacy. |
| 2026-08-09 | Permit native threading only from a trusted same-rowid binding. Superseded on 2026-08-10. | This restored threaded replies without allowing metadata or stale cross-chat state to redirect delivery. |
| 2026-08-09 | Centralize all native mutations in `imsg_action` and route poll compatibility through it. | One resolver and capability boundary prevents divergent safety behavior. |
| 2026-08-09 | Convert supported markup to native UTF-16 ranges. | Messages can render authored emphasis without exposing markup tokens. |
| 2026-08-09 | Gate each native action from the live status object. | Private bridge availability is granular and may change across host versions. |
| 2026-08-09 | Prefer advertised bridge RPC for ordinary plain text and allow legacy fallback only before mutation. | AppleScript send can hang on newer macOS releases, while retrying after an ambiguous RPC result can duplicate delivery. |
| 2026-08-09 | Cancel POSIX subprocesses by process group. | Terminating only `imsg` can leave helper grandchildren such as `osascript` running. |
| 2026-08-09 | Require authenticated owners for group mutations from messaging surfaces. | Conversation access must not imply authority to change group identity or membership. |
| 2026-08-09 | Keep exact identifiers visible to the native action tools. | Hashing rowids or GUIDs would break same-chat validation and native targeting. |
| 2026-08-10 | Treat framework reply metadata as correlation only and require `imsg_action` for native threads. | Automatic threading bound every response to the immediately previous message and made framework bookkeeping user-visible. |
| 2026-08-10 | Default inbound reactions to `off` and drop them before the agent pipeline. | Tapbacks are social acknowledgements by default and must not become quiet prompt-bearing turns, especially in groups. |
