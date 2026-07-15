# Architecture and decision log

## Boundary

```text
Messages chat.db / Messages.app
              ^
              | upstream command + NDJSON contract
       local `imsg` executable
              ^
              | subprocess boundary
  cli.py  <-> client.py
              ^
              | Hermes platform methods
          adapter.py
              ^
              | stock plugin registry
       Hermes gateway / cron
```

This repository is a **Hermes plugin**. It does not vendor OpenClaw Swift code, read `chat.db` itself, implement JSON-RPC, or emulate an OpenClaw process. `imsg` remains the source of truth for Messages schema compatibility, filesystem watching, WAL rotation, contact resolution, AppleScript staging, and optional attachment conversion.

## Module responsibilities

- `cli.py`: pure, testable argument builders (including poll CLI and non-executing RPC request composition); NDJSON parser; the only generic subprocess runners.
- `client.py`: Hermes-independent async convenience API for bounded CLI calls. It adds timeouts, structured PII-redacted failures, object-shape checks, and fail-before-mutation poll capability probes. It is not a protocol adapter.
- `adapter.py`: Hermes `BasePlatformAdapter`, event conversion, policy enforcement, same-chat routing, watch lifecycle, read/typing/reaction/poll integration, explicit `imsg_poll` tool registration, and standalone cron send.
- `rules.py`: import-safe default-deny authorization model and redaction helpers.
- `config.py`: plugin-owned YAML/environment translation, binary checks, state path, and the Hermes E.164 parser workaround.

## Non-negotiable invariants

1. **Exact inbound rowid routing.** `MessageEvent.source.chat_id` is the positive upstream `chat_id`; every reply — threaded or not — calls `imsg send --chat-id <same value>`. A GUID or participant is never substituted, and no caller/metadata-supplied `chat_guid` can redirect a reply to a different chat (see the Reply routing decision below).
2. **Fail closed on unresolved chats.** Missing, blank, or zero chat rowids do not become direct-message-shaped events. Chat-id send targets are validated as positive-integer rowids within a sane digit range (≤19 digits) before any subprocess runs.
3. **Self messages never invoke the agent.** This check occurs before policy matching, including `allow_all`.
4. **Default deny.** If no rule matches, `silent_drop` is the default. DM and group allowlists remain separate. Participant list entries from event data are type-validated (string handles only); non-scalar/boolean entries are dropped rather than coerced.
5. **No permission automation.** The plugin never edits TCC, disables SIP, signs/injects a dylib, or signs into Messages.
6. **No payload leakage on parser/log surfaces.** NDJSON exceptions identify a line number but do not quote message text. Raw `imsg watch` lines are never logged, even on parse failure. Subprocess errors, exceptions, and diagnostics are redacted for phone numbers, emails, home-directory paths, and GUID-like identifiers before reaching a log line or an agent-visible error.
7. **Private capability honesty.** Read receipts, typing, and targeted tapbacks can fail when the bridge is absent. Basic reads/watch/plain sends continue. Rich/threaded replies are not attempted at all (see below), independent of bridge availability.
8. **Polls are explicit and capability-gated.** Poll creation/voting is never inferred from text. `status --json` must advertise both the operation method and required selector before the mutation command runs; absence is an error, not success.
9. **No participant routing for polls.** Inbound-originated polls use the captured exact rowid. Explicit proactive operations may use an exact chat GUID; phone/email handles are not accepted.
10. **Fail closed on ambiguous send confirmation.** A zero exit code from `imsg send` is not itself success; stdout must parse as a JSON confirmation object. Outbound attachments must be regular files — symlinks and special files (directories, FIFOs, sockets, device nodes) are rejected before `--file` is used. Multi-attachment sends are fail-fast with a deterministic partial-failure report (`failed_index`, `succeeded_message_ids`) rather than an ambiguous single error.
11. **Local state is written safely.** The resumable cursor file is written to a same-directory temp file and atomically renamed into place with owner-only (`0600`) permissions.

## Event flow

1. `connect()` verifies the executable and optionally preserves the existing `auto_launch` bridge re-arm behavior.
2. `_watch_loop()` starts one `imsg watch --json --attachments --convert-attachments` process with the persisted `--since-rowid` cursor.
3. Stdout is consumed line by line (via `_handle_watch_line()`); stderr is drained concurrently into a bounded redacted tail to prevent pipe deadlock. A line that fails to parse as JSON is dropped with only its length logged — the raw content is never logged.
4. The cursor is advanced and persisted (atomically, `0600`) before policy handling. This prevents an unauthorized or malformed event from replaying forever.
5. Self/unresolved events are dropped. Rules are evaluated top-down; strict participant checks call the source-backed `group --chat-id` command; participant handles from event data are type-validated.
6. Allowed messages become Hermes `MessageEvent`s. Converted attachment paths are preferred; a bare `filename`/`file` with no resolvable path is never expanded into one. Missing/unresolvable paths are omitted while raw metadata stays in `raw_message`.
7. Replies always use the exact source rowid via a plain `imsg send --chat-id` call — see the Reply routing decision below.

## Reply routing: why rich/threaded replies are not attempted

An earlier revision let `send()` attempt a threaded `send-rich --reply-to`
reply whenever reply metadata and a `chat_guid` were available — the
`chat_guid` could come from a per-chat cache **or from caller-supplied
`metadata`**. That is a routing vulnerability: nothing bound the supplied
`chat_guid` to the `chat_id` the reply was actually invoked for, so a stale
cache entry or a forged/attacker-influenced `metadata` value could silently
redirect a reply to a different chat than intended, defeating the exact-
rowid safety invariant above.

There is no safe way to accept a caller-controlled routing override here.
`send()` therefore never attempts `send-rich`: `reply_to`/`metadata` are
accepted only for `BasePlatformAdapter` interface compatibility and are
never used to select a destination chat. Every reply, threaded or not,
plain-sends to the exact `chat_id` it was invoked with. This trades away the
visual "threaded reply" bubble for a routing guarantee that cannot be
subverted by any input the adapter does not itself control.

## Cursor and reconnect model

The state file stores only `last_rowid` and an update timestamp under the active Hermes home unless overridden, written atomically (temp file + `os.replace()`) with owner-only (`0600`) permissions. Upstream watch owns database filesystem events, WAL sidecar re-arming, polling fallback, row metadata retry, URL-preview coalescing, and ordering. The plugin owns process supervision: it drains both pipes, restarts an ended stream, and uses 1–30 second exponential backoff. A real stdout line resets backoff; successful spawn alone does not.

This is **at-least-once around the crash window**, not a transactional queue. The cursor is stored before gateway handling, favoring loop prevention and safety over replaying a message whose agent invocation crashed. Operators who need replay can stop the gateway, back up the state file, set a deliberate lower cursor, and restart; see `OPERATIONS.md`.

## Attachment decisions

- Request metadata and conversion on watch/history.
- Prefer `converted_path`/`convertedPath`, then original `path`.
- Ignore `missing=true` paths rather than handing Hermes a nonexistent file.
- Do not copy or upload attachment files automatically.
- Send uses upstream's one-file-per-command contract. Multiple Hermes files become separate sends; no builder emits repeated `--file` options that upstream may silently collapse.

## Reaction decisions

The public `react` command supports exactly six standard tapbacks and targets the newest inbound message in a chat. Unknown/custom emoji are rejected before execution. If the adapter has trusted recent chat/message GUIDs, its existing bridge `tapback` path can target that message, but this remains an optional private capability and is never advertised as SIP-free.

## Poll decisions

Hermes executes native polls through short-lived upstream CLI commands, not an
embedded `imsg rpc` process. Pure RPC request builders document/test the pinned
wire shape for parity, while preserving the repository boundary. Before every
mutation, the client reads `status --json`: create requires `poll.send` plus
`pollPayloadMessage`; vote requires `poll.vote` plus `pollVoteMessage`; unvote
requires an advertised unvote alias plus `pollVoteMessage`. An older binary or
missing injected selector therefore fails before the `poll` command runs.

Creation accepts question, at least two non-empty options, optional caption
override, and optional reply-to GUID. The poll payload may succeed while its
best-effort plain caption fails; upstream intentionally keeps exit success to
avoid duplicate-poll retries. Vote and selective unvote accept one of ID,
1-based index, or text in the CLI. Upstream—not Hermes—validates the decoded
poll, option membership, and current selection for unvote.

## Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-07-13 | Pin parity review to upstream commit `b5b7464…`; document host binary 0.11.0 separately. | Prevent a moving `main` from making claims unverifiable. |
| 2026-07-13 | Fix strict participant lookup from invalid `chats --chat-id` to `group --chat-id`. | Verified in current command source. |
| 2026-07-13 | Parse upstream output as NDJSON, while accepting a single compatibility array. | Upstream guarantees one JSON object per line, not a JSON array. |
| 2026-07-13 | Preserve unknown schema keys and raw payloads. | Upstream schema is additive; re-modeling it would lose 0.13 fields and create adapter drift. |
| 2026-07-13 | Expose safe bounded reads through a native async client/adapter API, but do not embed RPC. | Meets Hermes needs without creating an OpenClaw adapter. |
| 2026-07-13 | Keep private IMCore mutations out of the plugin and document direct safe alternatives. | They require SIP-off injection and have no stable public Messages automation equivalent. |
| 2026-07-13 | Preserve existing bridge auto-launch and plain reply fallback, but label both clearly. | No existing Hermes behavior removed; operators can disable bridge launch. |
| 2026-07-13 | Expose native polls only as explicit `imsg_poll` send/vote/unvote actions, not as ordinary text-send inference. | Polls mutate a native extension, require extra selectors, have duplicate-retry risk around the best-effort caption, and need exact poll/option identity; explicit intent keeps routing and failure semantics auditable. |
| 2026-07-15 | Remove the `send-rich` reply path entirely; every reply always plain-sends by rowid. | A caller/metadata-supplied `chat_guid` could redirect a threaded reply to a different chat than the rowid it was invoked with. There is no safe caller-controlled override; the rowid-only path cannot be redirected. |
| 2026-07-15 | Fail closed on ambiguous `imsg send` output; validate outbound attachments as regular files; stop expanding a bare inbound `filename`/`file` into a path. | A zero exit code, a symlink/special-file attachment, or a bare display-name-as-path each previously risked reporting false success or touching the wrong file. |
| 2026-07-15 | Write the resumable cursor atomically with `0600` permissions; validate chat-id send targets and event participant types; add bounded terminate-then-kill subprocess cleanup; never log a raw `imsg watch` line; extend redaction to home paths and GUID-like identifiers. | Defense-in-depth hardening ahead of public release; none change the documented command surface. |
| 2026-07-15 | Do not set `pii_safe=True` on the platform registry entry. | The agent-facing `imsg_poll` tool requires the LLM to pass back the exact rowid it was given; hashing chat/user ids for the prompt (what `pii_safe` enables) would silently break every poll action for no privacy benefit, since routing already uses the untouched value regardless of what the prompt shows. |
