# Hermes iMessage Full Parity Design

## Objective

Expose the complete native iMessage action surface available to the current
OpenClaw iMessage channel through the Hermes `imsg` platform plugin, while
preserving the Hermes-specific routing, policy, cron delivery, and plugin
lifecycle that already work.

The reference snapshot is OpenClaw commit
`34b8a6515d780e07866b437dfc1a6f12d1fc7f18`, whose iMessage plugin package is
version `2026.8.1`. The host currently runs `imsg 0.13.0`; its read-only status
probe reports `advanced_features: true`, `v2_ready: true`, and the RPC methods
needed by this design.

## Definition of parity

Hermes must support these user-visible iMessage capabilities:

- inbound direct messages, groups, attachments, replies, reactions, polls,
  poll votes, read receipts, and typing indicators;
- outbound plain text, formatted text, media, voice attachments, exact-chat
  replies, and proactive sends by rowid, chat GUID, chat identifier, phone
  number, or email address;
- standard tapback addition and removal, including part indexes;
- message editing and unsending for messages sent by the bot;
- threaded replies, reply attachments, and expressive send effects;
- native poll creation, voting, and selective unvoting;
- group rename, group icon, participant addition, participant removal, and
  leaving a group;
- capability discovery that exposes an action only when the installed binary
  and bridge can execute it;
- the existing Hermes rule engine, exact-rowid default reply routing, mention
  gates, participant gates, cursor recovery, cron delivery, standalone sends,
  self-message suppression, and redacted error reporting.

OpenClaw-specific pairing storage, approval orchestration, ACP bindings, and
session-key formats belong to the OpenClaw gateway. Hermes keeps its own
equivalent gateway behavior. They are outside the native iMessage capability
contract.

## Architecture

The plugin remains a Python Hermes platform plugin. Its stable watch process
and reconnect lifecycle remain in place. Native mutations use the same local
`imsg` CLI commands and JSON-RPC methods used by the reference integration.

Five focused units carry the new behavior:

1. `hermes_imsg_cli/cli.py` owns pure, validated command and RPC request
   composition.
2. `hermes_imsg_cli/formatting.py` converts supported Markdown emphasis into
   UTF-16 ranges for native Messages attributed text while preserving code and
   Python dunder identifiers literally.
3. `hermes_imsg_cli/capabilities.py` converts `imsg status --json` into a
   deterministic per-action capability model.
4. `hermes_imsg_cli/client.py` executes typed iMessage operations and converts
   transport failures into PII-redacted `ImsgCommandError` values.
5. `hermes_imsg_cli/actions.py` exposes one Hermes-facing `imsg_action` tool,
   resolves targets and message references, dispatches client calls, and
   returns structured results. The existing `imsg_poll` tool remains as a
   compatibility entry point.

`hermes_imsg_cli/adapter.py` integrates the action layer with live conversation
state. It records trusted chat and message references from inbound events,
uses them for threaded replies, enables reaction events in the watch stream,
and normalizes poll and reaction notifications before applying existing rules.

## Target and message-reference safety

Ordinary Hermes replies keep the captured positive Messages rowid as their
destination. A threaded reply is used only when all of these conditions hold:

- Hermes supplied a `reply_to` message identifier;
- the identifier is known to belong to the same rowid being replied to;
- the associated chat GUID is known from a trusted inbound event or a live
  `imsg` lookup;
- any operation requiring a bot-authored message confirms `is_from_me`.

The action tool accepts one target representation at a time: `chat_id`,
`chat_guid`, `chat_identifier`, or `to`. Conflicting target aliases fail before
a subprocess starts. Message mutations resolve their message identifier within
that target conversation. Cross-chat identifiers are rejected.

Outbound attachment paths must be existing regular files. Symlinks,
directories, devices, sockets, and FIFOs remain rejected. Group icon and reply
attachments use the same validator.

## Capability gates

Every advanced mutation first reads the current `imsg status --json` result.
The capability model requires `advanced_features` and `v2_ready`, then checks
the action's advertised RPC method and any required selector.

The relevant method and selector gates are:

| Capability | Method or command | Required selector |
| --- | --- | --- |
| Tapback | `tapback` | none |
| Edit | `message.edit` | `editMessage` or `editMessageItem` |
| Unsend | `message.unsend` | `retractMessagePart` |
| Rich reply and effects | `send.rich` | none |
| Attachment | `send.attachment` | none |
| Poll create | `poll.send` or `messages.poll.send` | `pollPayloadMessage` |
| Poll vote or unvote | matching poll RPC method | `pollVoteMessage` |
| Group management | matching `group.*` method | none |

Unknown capability state triggers a fresh read-only probe. A missing feature
returns a structured error naming the method or selector. The plugin never
claims a mutation succeeded from an exit code alone.

## Hermes action contract

The registered `imsg_action` tool supports these action names:

`react`, `edit`, `unsend`, `reply`, `sendWithEffect`, `upload-file`,
`renameGroup`, `setGroupIcon`, `addParticipant`, `removeParticipant`,
`leaveGroup`, `poll`, `poll-vote`, and `poll-unvote`.

The handler accepts only parameters relevant to the selected action. It
returns `success`, `action`, resolved target information, and the native result
or a redacted error. Poll compatibility calls delegate through the same action
handler so the two tool surfaces cannot drift.

Plain and rich outbound text converts CommonMark bold, italic, and
strikethrough plus authored `<u>` and `<ins>` spans into the native formatting
range schema accepted by `imsg`. Range offsets and lengths use UTF-16 code
units. Inline and fenced code keep their Markdown-looking contents literal,
and Python dunder identifiers such as `__init__` are not mistaken for bold.

## Inbound behavior

The watch command enables reaction rows. Reaction delivery follows
`reaction_notifications` with values `own`, `all`, or `off`; `own` is the
default. Accepted reactions become readable system-style message text and
retain the complete upstream object in `raw_message`.

Inbound polls are rendered with their question, numbered options, vote counts,
and stable poll/message identifier. Poll vote and unvote events continue to
honor the existing per-context policy settings. Reply metadata and attachment
metadata remain intact in `raw_message`, and the adapter adds normalized reply
context to the agent-visible metadata when Hermes supports it.

All inbound variants still pass through self-message suppression, sender and
chat rules, participant validation, and mention handling before dispatch.

## Documentation and site

`docs/PARITY.md` keeps its filename for existing links and becomes a supported
functionality catalog. It contains no comparison ledger, no status vocabulary,
and no list of excluded OpenClaw features. It groups verified functionality by
messaging, native actions, inbound events, policy, routing, recovery, and
operational requirements.

`README.md`, `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, and the static GitHub
Pages site describe the same verified action surface, capability gates, and
security boundaries. Existing site validation remains authoritative for the
published files.

## Testing and verification

Every production behavior begins with a focused failing test. Tests exercise
literal command arguments, exact RPC payloads, capability rejection, tool
dispatch, cross-chat reference rejection, reaction policy, poll rendering,
threaded reply selection, and backward-compatible poll calls.

Completion requires fresh successful output from:

```bash
scripts/validate.sh
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
python3 -m compileall -q hermes_imsg_cli scripts tests
python3 -m build
git diff --check
```

Read-only live verification also checks the installed version, sanitized
`imsg status --json` capability fields, and `imsg rpc --help`. No live message,
reaction, poll, edit, group mutation, or attachment send is performed without
separate operator authorization.
