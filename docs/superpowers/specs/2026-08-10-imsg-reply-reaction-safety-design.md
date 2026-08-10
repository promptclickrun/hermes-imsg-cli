# iMessage Reply and Tapback Safety Design

**Date:** 2026-08-10

**Status:** Approved behavior design

## Problem

The platform adapter currently conflates three different kinds of message
metadata:

1. Hermes supplies `reply_to` while responding to an inbound message. The
   adapter interprets that correlation value as a request for an Apple native
   threaded reply, so ordinary one-to-one responses bind to the immediately
   preceding message.
2. The inbound adapter copies `reply_to_guid`, `reply_to_text`, and
   `reply_to_sender` into `MessageEvent` without proving that the inbound row is
   a native threaded reply. The upstream `reply_to_guid` value is not a reliable
   thread signal and can describe a recent association instead. Hermes then
   renders false `Replying to:` context.
3. `reaction_notifications` defaults to `own`. A Tapback on a bot-authored
   message is converted into a normal `MessageEvent`, which starts an agent turn.
   In a group chat this creates a quiet prompt-injection surface because a
   participant can trigger agent work without sending a message.

## Goals

- Keep ordinary agent responses in the conversation without creating native
  Apple reply threads.
- Preserve explicit native threaded replies through the `imsg_action` tool.
- Add inbound reply context only for a proven native threaded reply.
- Make Tapbacks silent by default and prevent them from reaching the agent under
  that default.
- Preserve explicit reaction notification opt-ins for operators who knowingly
  accept that behavior.
- Deploy the fix to the personal Hermes instance without changing unrelated
  automation, routing, allowlist, or plugin settings.

## Non-goals

- Removing outbound Tapback actions.
- Removing the explicit `own` and `all` inbound reaction modes.
- Changing Hermes core reply rendering.
- Changing chat-rowid routing, message-action authorization, or bridge
  capability checks.
- Sending a live iMessage as part of automated verification.

## Security and Routing Invariants

1. A normal platform response always targets the exact positive Messages chat
   rowid captured from the inbound event.
2. Framework correlation metadata never requests a native Apple threaded reply.
3. A native threaded reply requires the explicit `imsg_action` `reply` action
   and its existing same-chat message lookup.
4. `thread_originator_guid` is the only inbound field that proves native reply
   threading. `reply_to_guid` alone never creates reply context.
5. With reaction notifications set to `off`, a Tapback cannot reach the rule
   engine, ownership/history lookup, read-receipt path, or `handle_message`.
6. Missing or invalid reaction configuration fails to `off`.
7. The personal deployment explicitly stores `reaction_notifications: off` so
   an older persisted `own` value cannot override the new default.

## Outbound Reply Behavior

The normal adapter methods `send`, `send_document`, `send_image_file`,
`send_video`, and `send_voice` will treat Hermes' `reply_to` argument as
conversation correlation only. They will use the ordinary exact-rowid send path
and will not call `send-rich` merely because that argument is present.

The ordinary text path retains its existing capability-gated JSON-RPC `send`
behavior, formatting support, exact rowid target, and fail-closed transport
semantics. Attachments retain regular-file validation and their existing send
path.

Explicit native threading remains available through:

```json
{
  "action": "reply",
  "chat_id": "42",
  "message_id": "MESSAGE-GUID",
  "text": "Threaded response"
}
```

That action continues to resolve the message within the selected chat and use
the private bridge only after the existing capability and target checks pass.
Poll replies supplied explicitly through the poll action remain unchanged.

## Inbound Reply Context

Event conversion will read `thread_originator_guid` and its camel-case alias.
When the value is a nonempty string, the adapter will populate
`reply_to_message_id` and may include the upstream resolved
`reply_to_text` and `reply_to_sender` values. Empty optional values normalize to
`None`.

When `thread_originator_guid` is absent, all three `MessageEvent.reply_to_*`
fields remain `None`, even if `reply_to_guid`, `reply_to_text`, or
`reply_to_sender` are present. This prevents Hermes from injecting false
`Replying to:` context while preserving real replies to older messages.

Reaction rows and poll-only rows do not gain reply context through association
metadata.

## Tapback Handling

`reaction_notifications` will default to `off`; invalid values will also become
`off`. The explicit modes remain:

- `off`: no Tapback may initiate an agent turn.
- `own`: opt in to Tapbacks targeting bot-authored messages.
- `all`: opt in to all authorized Tapbacks.

The default is enforced at two boundaries:

1. The watch command omits `--reactions` when the mode is `off`, preventing
   Tapback rows from entering the stream.
2. `_process_watch_event` recognizes and drops any reaction row that still
   arrives while the mode is `off`. It may advance the durable watch cursor, but
   returns before access-rule evaluation, target-ownership history lookup, read
   receipts, event conversion, or agent dispatch.

The opt-in modes retain their current filtering and ownership behavior. Public
documentation will warn that enabling either mode allows a Tapback to create an
agent turn and should be treated as an explicit security decision, especially
for group chats.

## Configuration and Documentation

The patch release will be version `2.0.1`. Defaults and examples will be aligned
across the adapter, plugin manifest, README, operations guide, parity catalog,
architecture notes, migration guide, verification report, and bundled Hermes
operator skill.

The local deployment will update only the personal plugin's
`reaction_notifications` setting to `off`, preserving all other YAML content and
creating a timestamped backup before writing. Environment and YAML inspection
will confirm that no higher-priority value re-enables reaction delivery.

## Error Handling

- Ordinary sends do not fall back to native threading when a plain send fails.
- Explicit native reply failures retain their structured capability or command
  errors and never retry as an ordinary message after an ambiguous mutation.
- Malformed thread metadata is ignored rather than guessed.
- Reaction suppression does not depend on sender identity, group membership, or
  successful history access.
- Deployment stops before service restart if tests, configuration validation,
  commit, or push fails.

## Test Strategy

Implementation will follow red-green-refactor with focused failing tests for:

1. A normal text response with Hermes `reply_to` uses the plain exact-rowid path
   and never calls the rich threaded path.
2. Document and voice responses with Hermes `reply_to` remain unthreaded.
3. The explicit `imsg_action` `reply` action still creates a native threaded
   reply.
4. An ordinary inbound message containing only unreliable `reply_to_*` fields
   exposes no reply context.
5. A message with `thread_originator_guid` exposes the correct parent GUID,
   text, and author.
6. Default and invalid reaction configuration resolve to `off`.
7. Default watch arguments omit `--reactions`; explicit `own` and `all` include
   it.
8. A reaction event received under `off` returns before authorization, history,
   read receipts, or agent dispatch.
9. Explicit `own` and `all` modes retain their documented behavior.

Full verification requires:

```bash
scripts/validate.sh
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
python3 -m compileall -q hermes_imsg_cli scripts tests
git diff --check
pipx run build
```

## Integration and Deployment

After the tests and checks pass:

1. Commit the implementation on local `main`.
2. Fetch and confirm remote `main` has not diverged.
3. Push local `main` and verify local and remote commit IDs match.
4. Back up and update the personal Hermes configuration to set reaction
   notifications to `off` without changing unrelated settings.
5. Run `scripts/install-local.sh` from the stable main checkout.
6. Use the supported Hermes service control to reload or restart the running
   instance so cached Python modules are replaced.
7. Confirm plugin discovery, `imsg` platform registration, configuration,
   process health, and absence of startup errors.

Automated verification will not send, react to, edit, unsend, or otherwise
mutate a real Messages conversation. The unit and integration fixtures exercise
the exact outbound and inbound boundaries without invoking the real command or
RPC mutation runners.

## Rollback

The previous Git commit remains available on `main` history, and the local
configuration edit creates a timestamped backup. If startup verification fails,
restore the configuration backup and repoint the plugin to the previous known
commit before restarting Hermes again.
