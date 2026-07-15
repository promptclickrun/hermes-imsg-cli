# Compatibility and migration notes

## Scope

This plugin consumes the current OpenClaw `imsg` executable; it is not an OpenClaw channel adapter and does not replace OpenClaw configuration. Migration means pointing Hermes at the same local CLI while expressing policy in Hermes.

## From the original standalone Hermes plugin

The release is additive and preserves:

- platform name `imsg` and plugin name `imsg-platform`;
- exact rowid inbound reply routing;
- legacy flat allowlists and synthesized rules;
- default `silent_drop`, mention behavior, aliases, read receipts, bridge auto-launch setting;
- state file format (`last_rowid` remains readable);
- cron/standalone sender and E.164 workaround;
- text, document, image, video, and voice methods.

Behavior strengthened:

1. Strict participant validation now uses real current syntax: `group --chat-id`, replacing invalid `chats --chat-id`.
2. Batch JSON is parsed as upstream NDJSON; compatibility arrays are still accepted.
3. Unresolved `chat_id=0` watch rows fail closed instead of looking like a DM.
4. Converted attachment paths are preferred and `missing=true` files are not handed to Hermes.
5. Unknown/custom public reactions are rejected before any UI automation.
6. Bounded read APIs now cover chats, group, history, and search without narrowing upstream fields.
7. Subprocess and parser failures are structured/redacted rather than leaking payload lines.

One deliberate CLI-builder correction: `build_chats_args` now takes `limit`/`unread_only`, matching upstream. Internal callers that previously passed a chat ID must use `build_group_args(chat_id)`. This was never a working upstream command and is the only source-level correction likely to affect code importing the helper directly.

## From direct `imsg` scripts

| Direct script concept | Hermes equivalent |
|---|---|
| Run one `watch` per integration | One supervised adapter watch with persisted rowid. |
| Route by participant/handle | Inbound replies route by exact positive `chat_id`; explicit outbound handles only. |
| Script-side allowlist | `allowed_handles`, `group_allowed_handles`, `allowed_chats`, or ordered `rules`. |
| Parse `jq -s` arrays | Plugin parses NDJSON natively and preserves objects. |
| Store cursor | `IMSG_STATE_FILE` or profile-local `imsg_state.json`. |
| Call send from cron | `deliver=imsg` / `IMSG_HOME_CHANNEL` standalone sender. |
| Launch bridge before every action | Optional `auto_launch`; public-only deployments should disable it. |
| Treat every reaction as input | Gateway does not enable reaction watch by default, preventing tapback-triggered agent replies. |

Keep unsupported advanced mutation scripts direct. The Hermes plugin intentionally does not expose edit/unsend/group-management/stickers/private account actions. Native polls are now exposed through the explicit `imsg_poll` tool for create, vote, and selective unvote, but remain environment-gated by the installed `imsg` binary and bridge capabilities.

## Hermes versus upstream semantics

- Upstream `--json` means one object per line, not a single array. Hermes APIs return Python lists after streaming parse.
- Upstream `react` targets the newest incoming message. Hermes may use a targeted bridge tapback only when trusted recent GUIDs exist; otherwise the public limitation applies.
- Upstream accepts rowid, identifier, GUID, or handle for sends. Hermes inbound replies accept only the captured rowid for safety. Explicit standalone sends may use phone/email handles.
- Upstream attachment conversion is a cache hook. Hermes consumes the converted path when present but neither uploads nor deletes it.
- Upstream read/typing commands are present even when the bridge is unavailable. Hermes treats failures as best-effort and does not claim capability. Upstream's `send-rich` is present too, but Hermes never calls it — see the reply-routing decision in `ARCHITECTURE.md`.
- Upstream JSON-RPC is designed for a long-running integration. Hermes already owns process lifecycle, access control, session routing, and cron, so it uses the CLI/NDJSON surface instead of nesting another adapter.

## Version compatibility

The source review targets commit `b5b7464…` (0.13.1 development tree). The deterministic builders use options present by 0.11.0 for normal gateway operation: chats/group/history/watch/search/send/react/status/read/typing/tapback. A `send-rich` builder exists for reference but is not used by the adapter (see the reply routing decision in `ARCHITECTURE.md`). `--unread-only` and newer additive JSON fields require 0.13. An older executable will return a clear nonzero error when a newer optional method is requested; core watch/plain send flags remain compatible with 0.11.0.

Recommended upgrade process:

```bash
brew update
brew upgrade steipete/tap/imsg
imsg --version
imsg status --json
scripts/validate.sh
```

Do not upgrade and restart blindly. First compare `imsg watch --help` and `imsg send --help`, run a read-only chats/history probe, then a user-approved test message in a dedicated chat.

## Rollback

1. Stop Hermes gateway using the normal operator procedure (this repository does not restart it).
2. Restore the previous plugin checkout/symlink target.
3. Keep the state file; its `last_rowid` format is unchanged.
4. Restore the prior `imsg` package only if CLI incompatibility is proven.
5. Start Hermes and verify one allowed inbound chat plus one explicit outbound test.

Do not delete the cursor as a routine rollback step; that changes replay semantics independently of code rollback.
