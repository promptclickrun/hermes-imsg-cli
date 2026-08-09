# Supported functionality

`hermes-imsg-cli` connects stock Hermes to the local Apple Messages database
and Messages.app through the `imsg` command line interface. The plugin keeps
ordinary replies bound to the exact Messages chat rowid that produced the
inbound event and exposes private bridge actions only after a capability probe.

## Messaging and delivery

- Receive direct messages and group messages from the supervised `imsg watch`
  stream.
- Send text, images, video, audio, and general file attachments.
- Send voice attachments through the native attachment action.
- Address a conversation by `chat_id:`, `chat_guid:`, `chat_identifier:`, an
  `imessage:` or `sms:` handle, a phone number, or an Apple ID email address.
- Keep normal conversation replies on the captured positive Messages rowid.
- Send an exact same-chat threaded reply when Hermes supplies a trusted message
  reference from the current conversation.
- Attach one file to a trusted threaded reply.
- Split long text at the Hermes platform limit of 4,000 characters.
- Convert authored bold, italic, underline, and strikethrough markup to native
  attributed-text ranges. Offsets use UTF-16 code units. Inline code, fenced
  code, escapes, and Python dunder identifiers remain literal.
- Deliver scheduled and out-of-process messages through the standalone sender
  used by Hermes cron and `send_message`.
- Mark accepted inbound chats read when `send_read_receipts` is enabled.
- Show a best-effort typing indicator while Hermes processes an accepted turn.

## Native message actions

The `imsg_action` tool accepts exactly one conversation target and validates
message references inside that conversation before running a mutation.

| Action | Functionality |
| --- | --- |
| `react` | Add or remove love, like, dislike, laugh, emphasize, or question tapbacks. Part indexes are supported. When removal omits the reaction name, the plugin uses the single current bot-authored tapback on that message. |
| `edit` | Edit a bot-authored message after same-chat and authorship checks. A backwards-compatible fallback string and part index are supported. |
| `unsend` | Retract a bot-authored message after same-chat and authorship checks. |
| `reply` | Send a native threaded reply with text, one attachment, or both. |
| `sendWithEffect` | Send text with slam, loud, gentle, invisible ink, confetti, lasers, fireworks, balloon, heart, echo, happy birthday, shooting star, sparkles, or spotlight. Aliases are converted to canonical Apple effect identifiers. |
| `upload-file` | Send a validated regular file, optionally as a voice attachment. |

Message IDs from another chat are rejected. Edit and unsend are additionally
limited to messages whose history record reports `is_from_me: true`. Unsafe
attachment paths, including symlinks, directories, devices, sockets, and FIFOs,
are rejected before the send command starts.

## Polls

- Create native Apple Messages polls with a question and 2 to 12 options.
- Add an optional visible comment and an optional same-chat reply reference.
- Vote using exactly one option ID, one-based option index, or option text.
- Selectively remove a vote with the same option selectors.
- Use either the unified `imsg_action` actions `poll`, `poll-vote`, and
  `poll-unvote`, or the backward-compatible `imsg_poll` tool actions `send`,
  `vote`, and `unvote`.
- Render accepted inbound polls with their question, numbered options, vote
  counts, and stable poll ID.
- Deliver inbound vote and unvote events only in the DM or group scopes enabled
  by `poll_vote_events`. Optional chat allowlists still apply.

Poll creation requires the bridge poll payload selector. Voting and unvoting
require the bridge poll vote selector and the matching RPC method.

## Groups

The native action tool supports:

- `renameGroup`
- `setGroupIcon`
- `addParticipant`
- `removeParticipant`
- `leaveGroup`

Each action requires an exact target whose live chat metadata identifies it as
a group. Group icon paths receive the same attachment validation as outbound
files. A local interactive Hermes operator may use group mutations directly.
Every other caller must have an authenticated sender ID in
`IMSG_GROUP_ACTION_ADMINS`; when that setting is absent, the plugin uses
`IMSG_ALLOWED_USERS` as the owner list. Broader group conversation admission
and unauthenticated programmatic access do not grant group mutation authority.

## Inbound events and attachments

- Preserve the complete decoded `imsg` object in `raw_message`.
- Expose attachment metadata and resolvable converted or original paths.
- Preserve reply GUID, thread originator, reply text, reply sender, chat GUID,
  group metadata, participant metadata, and poll objects when present.
- Subscribe to structured tapback events with `watch --reactions`.
- Deliver reaction notifications as readable system-style events without
  treating them as ordinary message text.
- Support reaction notification modes `own`, `all`, and `off`; `own` is the
  default and uses sent-message evidence from memory or same-chat history.
- Suppress outbound echoes and every event reported as `is_from_me`.
- Optionally convert supported inbound attachments before model ingestion.

All inbound variants pass through the same sender, chat, participant, mention,
and fallback policy before dispatch to Hermes.

## Access policy and routing

- First-match `allow`, `deny`, `prompt`, and `silent_drop` rules.
- Separate direct-message and group sender allowlists.
- Exact group chat rowid allowlists.
- Group mention requirements with configurable patterns.
- Fast participant validation from event metadata or strict validation through
  a live group lookup with a bounded cache.
- Identity aliases for policy matching and readable sender context.
- A safe `silent_drop` fallback when no rule matches.
- Exact positive rowid reply routing. GUIDs and participant matching never
  replace the captured rowid for an ordinary inbound reply.
- Explicit proactive targets that preserve whether the caller chose a rowid,
  chat GUID, chat identifier, iMessage handle, SMS handle, phone number, or
  email address.

## Recovery and reliability

- Supervise the long-running watch subprocess and reconnect with bounded
  backoff after unexpected exit.
- Resume from an atomically persisted `last_rowid` cursor.
- Create the cursor file with owner-only mode `0600`.
- Apply watch debounce without changing the exact source chat.
- Re-arm the private Messages bridge on connect when `auto_launch` is enabled.
- Track background read-receipt and policy-notice tasks so shutdown can cancel
  them cleanly.
- Require a structured JSON success object for mutations. A zero process exit
  without confirmation is treated as failure.
- Redact phone numbers, email addresses, home paths, and stable GUID-like values
  from surfaced command failures and diagnostic logs.

## Capability requirements

Basic messaging requires:

- macOS with Messages.app signed in
- the `imsg` executable
- Full Disk Access for the process that reads the Messages database
- Automation permission for the process that sends through Messages.app

Private message actions, native polls, group management, read receipts, and
typing indicators depend on the injected `imsg` private bridge. The bridge
requires the host-specific private API setup described by `imsg`, including the
associated System Integrity Protection tradeoff.

Before each advanced mutation, the plugin reads `imsg status --json` and
requires `advanced_features: true`, `v2_ready: true`, the action's RPC method,
and any required selector. Missing capability produces a structured error and
the mutation is not attempted. Basic watch, history, text, and attachment paths
remain usable when the private bridge is unavailable.

## Verification status

The automated suite uses in-memory runners and temporary files. It covers
literal command composition, formatted UTF-16 ranges, capability rejection,
all action branches, exact target preservation, cross-chat rejection,
bot-authorship checks, group authorization, reaction policy, inbound poll
rendering, recovery state, standalone delivery, and plugin registration.

The current commands, outputs, package-build result, privacy scan, and read-only
host probe are recorded in [VERIFICATION.md](VERIFICATION.md). Automated
verification does not send a live message, react, edit, unsend, create or vote
on a poll, change a group, mark a real chat read, or launch the private bridge.
