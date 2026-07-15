# OpenClaw `imsg` parity ledger

**Review date:** 2026-07-13
**Upstream branch/commit:** `openclaw/imsg` `main` at [`b5b7464bc748af482bfc3059b28d5dab0395da9e`](https://github.com/openclaw/imsg/commit/b5b7464bc748af482bfc3059b28d5dab0395da9e) (`0.13.1` unreleased tree; latest tagged release observed was `v0.11.0`).

## Sources inspected

This ledger was made from source, not only the README:

- [README command and schema overview](https://github.com/openclaw/imsg/blob/b5b7464bc748af482bfc3059b28d5dab0395da9e/README.md)
- [CHANGELOG through 0.13.1](https://github.com/openclaw/imsg/blob/b5b7464bc748af482bfc3059b28d5dab0395da9e/CHANGELOG.md)
- [`Sources/imsg/Commands`](https://github.com/openclaw/imsg/tree/b5b7464bc748af482bfc3059b28d5dab0395da9e/Sources/imsg/Commands), especially `ChatsCommand.swift`, `GroupCommand.swift`, `HistoryCommand.swift`, `WatchCommand.swift`, `SendCommand.swift`, `ReactCommand.swift`, `ReadCommand.swift`, `TypingCommand.swift`, and `Bridge*Commands.swift`
- [`Sources/imsg/RPCServer*.swift`](https://github.com/openclaw/imsg/tree/b5b7464bc748af482bfc3059b28d5dab0395da9e/Sources/imsg)
- [JSON schema](https://github.com/openclaw/imsg/blob/b5b7464bc748af482bfc3059b28d5dab0395da9e/docs/json.md), [RPC](https://github.com/openclaw/imsg/blob/b5b7464bc748af482bfc3059b28d5dab0395da9e/docs/rpc.md), and command docs in `docs/`
- Upstream Swift tests under [`Tests/imsgTests`](https://github.com/openclaw/imsg/tree/b5b7464bc748af482bfc3059b28d5dab0395da9e/Tests/imsgTests)

The local host had `imsg 0.11.0` during verification. Features added after 0.11.0 are documented below but cannot be live-probed on that binary.

## Status vocabulary

- **Implemented** — Hermes calls the public CLI surface or consumes its schema, with deterministic tests.
- **Implementable** — safe/public upstream surface, but no Hermes gateway primitive currently needs it; available directly through `imsg` and a future additive client method is safe.
- **Adapter-owned** — Hermes policy/routing behavior rather than an upstream CLI feature.
- **Incompatible / private** — requires SIP-disabled injected IMCore or mutates Messages in a way this plugin intentionally does not expose.
- **Environment-blocked** — implemented command composition, but live execution requires macOS permissions, accounts, or optional tooling not available in deterministic CI.
- **Excluded** — intentionally left to the upstream executable; duplicating it would create the prohibited OpenClaw adapter.

## Public, SIP-free command surface

| Upstream surface | Hermes status | Implementation / rationale |
|---|---|---|
| `chats --limit --unread-only --json` | **Implemented** | `cli.build_chats_args`, `ImsgClient.chats`, `ImsgAdapter.list_chats`; NDJSON retained including `unread_count`. |
| `group --chat-id --json` | **Implemented** | `build_group_args`, `client.group`, `adapter.get_chat_info`; also fixes strict participant validation to use this real command (current `chats` has no `--chat-id`). |
| `history --chat-id --limit --attachments --convert-attachments --participants --start --end --json` | **Implemented** | Full builder + client/adapter read methods. Original metadata is preserved. Converted paths are preferred for Hermes media consumption; `missing=true` is fail-closed. |
| `watch` filters, attachments, conversion, `--since-rowid`, debounce | **Implemented** | Resumable adapter watch, all argument hooks in builder, stderr drain, polling/reconnect delegated to upstream, exponential respawn backoff. |
| `watch --reactions` | **Implementable** | Builder exposes it. Gateway watch intentionally leaves it off so tapback rows do not trigger agent replies. Apps can opt in using the builder/client streaming boundary. |
| poll creation/vote/unvote rows from `watch` | **Implemented; policy-gated** | Raw poll objects are preserved. Hermes can normalize vote-only rows into agent events independently for DMs and groups via `poll_vote_events.dm`, `poll_vote_events.group`, optional exact `poll_vote_events.chats`, and `include_unvotes`; all existing rules still apply. |
| `watch --bb-events` | **Incompatible / private** | Bridge event stream requires injected IMCore. Not enabled by Hermes. |
| `search --query --match contains|exact --limit --json` | **Implemented** | Builder validates modes; client and adapter return schema-preserving objects. |
| `send` by `--to` / `--chat-id` | **Implemented** | Explicit outbound handles use `--to`; same-chat replies always use exact inbound rowid via `--chat-id`. No participant/GUID guessing. |
| `send --chat-identifier` / `--chat-guid` | **Implementable** | Safe upstream selectors, but intentionally not used for Hermes reply routing because rowid binding is the safety invariant. Direct callers may use upstream CLI. |
| text and one `--file` | **Implemented** | Text/file paths supported. Builder enforces upstream's single-file option rather than silently pretending multiple files were accepted. Hermes sends multiple attachments as separate sends. |
| `--service`, `--region`, `--no-sms-fallback` | **Implemented in client/builder** | Available for explicit outbound sends; adapter replies retain existing automatic service behavior. |
| contact-name recipient resolution | **Environment-blocked** | Upstream owns Contacts lookup. Requires Contacts permission; Hermes never scrapes or copies contacts. |
| standard `react` tapbacks | **Implemented** | Only love/like/dislike/laugh/emphasis/question are accepted. Unknown/custom emoji fail before subprocess execution. Public automation reacts to most recent inbound message. |
| custom emoji reaction reads | **Implemented by schema preservation** | History/watch JSON is not narrowed. Custom send is private bridge-only. |
| `rpc` and methods `chats.list`, `messages.history`, `watch.*`, `send`, `message.send_status`, `handles.check` | **Excluded** | The plugin uses short-lived CLI calls plus one supervised `watch`; embedding upstream JSON-RPC would duplicate process management and create an adapter. Users may run `imsg rpc` directly. Poll RPC request composition is accounted for separately below. |
| `completions bash|zsh|fish|llm` | **Excluded** | Upstream CLI UX, not a Hermes gateway capability. Install completion output directly from `imsg`. |
| stable NDJSON / JSON schema | **Implemented** | `parse_json_output` supports one object per line, blank lines, and compatibility arrays; parse errors report line number without echoing private payloads. Numeric/boolean/unknown fields remain untouched. |
| attachments metadata | **Implemented** | Filename/transfer name/UTI/MIME/size/sticker/missing/path and future additive keys remain in raw payload. |
| CAF→M4A, GIF→PNG conversion | **Environment-blocked** | Adapter requests `--convert-attachments`; upstream performs cached conversion. Requires `ffmpeg`. Originals are never modified. |
| URL-preview coalescing, native poll reads, read timestamps, scheduled fields | **Implemented by schema preservation** | The plugin does not re-decode chat.db rows; it consumes upstream objects, preserving additive fields such as `balloon_bundle_id`, `url_preview`, `poll`, `is_read`, `date_read`. Poll read objects retain created/vote kind/event, GUID, question, options, votes, creator, participants, and metadata. |
| Linux copied-DB read-only preview | **Incompatible with gateway** | Upstream can inspect copied DBs on Linux, but a live Hermes iMessage platform requires Messages.app/macOS; plugin requirement check correctly disables it. |

## Advanced IMCore command surface

These features require `imsg launch` with SIP disabled and an injected dylib. The plugin does not disable SIP, install/inject a dylib, grant TCC, or claim these are available. Basic read/watch/send stays usable without them.

| Upstream command/feature | Hermes status | Decision |
|---|---|---|
| `status --json` | **Implemented** | Used only as a read-only requirements/capability probe. It never auto-launches. |
| `launch [--dylib|--kill-only]` | **Environment-blocked / existing opt-in** | Existing `auto_launch` behavior is preserved for configured deployments; failures are non-fatal. Set `auto_launch: false` for SIP-on/public-only operation. No SIP changes are attempted. |
| `read` | **Environment-blocked** | `mark_read` composes `--chat-id` and is best-effort. Requires bridge; configurable with `send_read_receipts`. |
| `typing` start/duration/stop | **Environment-blocked** | Builder/client support all public flags; adapter `send_typing` is best-effort. Requires bridge. |
| threaded reply (`send-rich --reply-to`) | **Not used (routing safety)** | The pure builder (`cli.build_rich_reply_args`) exists for reference, but the adapter never calls it: a caller/metadata-supplied `chat_guid` could redirect a threaded reply to a different chat than the rowid it was invoked with, so `send()` always plain-sends by rowid instead. |
| `send-rich` effects/format/URL | **Incompatible / private** | No general Hermes API; bridge-only. Not exposed at all — see the reply routing decision above. |
| `send-multipart` | **Incompatible / private** | Bridge-only; excluded. |
| `send-attachment --reply-to/--audio/--transport` | **Incompatible / private** | Bridge-only. Public `send --file` is the safe alternative. |
| `send-sticker` / `send.sticker` | **Incompatible / private** | 0.13 bridge-only, bounded/staged upstream. Not reproduced. |
| `tapback` custom/remove | **Incompatible / private** | Bridge-only. Standard public `react` is the safe alternative. Existing targeted standard tapback path remains bridge-dependent when GUIDs are available. |
| CLI `poll send` | **Implemented; environment-blocked** | Pure builder + `ImsgClient.send_poll` + adapter API + `imsg_poll action=send`. Exact rowid (`--chat-id`) or exact GUID (`--chat`) only; question, repeated options, comment/caption override, and creation-only `--reply-to` match `PollCommand.swift`. Requires advertised `poll.send` and `selectors.pollPayloadMessage`. |
| CLI `poll vote` | **Implemented; environment-blocked** | Supports exactly one upstream selector: option UUID, 1-based index, or case-insensitive text. Upstream resolves/validates it against decoded local poll history and strips `p:N/` from the associated poll GUID before bridge send. Requires advertised `poll.vote` and `selectors.pollVoteMessage`. |
| CLI `poll unvote` | **Implemented; environment-blocked** | Selective unvote uses the same option selectors. Upstream additionally verifies the option is currently selected and sends remaining option identifiers. Requires advertised unvote RPC alias and `selectors.pollVoteMessage`. |
| RPC `poll.send` / `messages.poll.send` request | **Implemented; environment-blocked** | Builder emits named params for rowid/GUID, question, options, optional comment and reply-to. Hermes now uses the RPC mutation path, matching OpenClaw's caller and avoiding the CLI-specific caption/duplicate path. |
| RPC `poll.vote`, `messages.poll.vote`, `poll.unvote` / `polls.unvote` / `messages.poll.unvote` request | **Implemented as pure request composition; execution excluded** | Pinned RPC accepts exact `option_id` only (not CLI index/text); builders use canonical `poll.vote` and `polls.unvote`. Upstream validates poll membership/current selection. |
| `edit`, `unsend`, `delete-message`, `notify-anyways` | **Incompatible / private** | Message mutation is deliberately not exposed; `SUPPORTS_MESSAGE_EDITING=False`. |
| `chat-create`, `chat-name`, `chat-photo` | **Incompatible / private** | Group/chat mutation excluded. |
| `chat-add-member`, `chat-remove-member`, `chat-leave`, `chat-delete`, `chat-mark` | **Incompatible / private** | Excluded; no safe public Messages automation equivalent. |
| `account`, `whois`, `nickname` bridge modes | **Incompatible / private** | No gateway use; do not surface private account data. |
| `account --local`, `whois --local`, `nickname --local` | **Implementable** | Safe local lookups available directly in upstream; no Hermes platform primitive needs them. |
| `name-photo status/share` | **Incompatible / private** | Status/share uses bridge/private contacts APIs in 0.13; excluded. |

## New 0.13 read-only surfaces

| Surface | Status | Decision |
|---|---|---|
| `stats` / `messages.stats` | **Implementable** | Safe snapshot-consistent analytics. Not gateway behavior; direct upstream command is preferred over duplicating a broad option schema. |
| `scheduled list` / `messages.scheduled` | **Implementable** | Read-only future Send Later inspection. No gateway primitive currently consumes it. |
| `chat-background status` | **Implementable** | Safe read-only metadata. No Hermes messaging primitive needs it. |

## Hermes-owned behavior preserved

These are intentionally **not** copied from OpenClaw: plugin registration; `Platform("imsg")` dynamic resolution; default-deny rules; distinct DM/group allowlists; first-match allow/deny/prompt/silent-drop rules; participant gates; mention gates/cleanup; identity aliases; self-message suppression; exact same-chat rowid replies; resumable cursor state; cron and `send_message` standalone delivery; E.164 rehydration for Hermes core's target parser; PII-redacted logs; background task retention; and watch reconnect supervision.
