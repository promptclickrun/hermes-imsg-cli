---
name: hermes-imsg-plugin
description: Use when operating or diagnosing the Hermes iMessage plugin, including text, SMS, attachments, native actions, polls, groups, inbound events, send timeouts, or lingering imsg and osascript processes.
version: 0.1.0
author: Hermes
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [Apple, Hermes, Imsg, Messaging]
---

# Hermes iMessage Plugin

This skill maps the capabilities of `hermes-imsg-cli` to the supported Hermes tools, adapter paths, Python client methods, and direct `imsg` commands. It does not bypass access rules, same-chat checks, group-owner authorization, attachment validation, or live private-bridge capability gates. Ordinary unformatted iMessage/chat text uses capability-selected transport; explicit SMS and attachment sends keep their documented legacy paths. Live use requires macOS, Messages.app, and the local `imsg` CLI.

## When to Use

- “Send or reply through the Hermes imsg plugin.”
- “React, edit, unsend, or use an iMessage effect.”
- “Create, vote on, or remove a vote from an Apple Messages poll.”
- “Rename a group, change its icon, manage members, or leave it.”
- “List chats, inspect history, search messages, or consume inbound events.”
- “Which target form or calling surface supports this capability?”
- “Why did a native imsg action fail its capability check?”
- “Why did an outbound send hang while inbound events and bridge status stayed healthy?”
- “Did a timed-out imsg send leave an osascript helper running?”

## Prerequisites

- macOS 14 or later with Messages.app signed in.
- Install `imsg` by invoking `brew install steipete/tap/imsg` through the `terminal` tool.
- Grant Full Disk Access to the complete Hermes launcher chain for Messages database reads.
- Grant Messages.app Automation permission for sends; the basic reaction helper also needs Accessibility.
- Install and enable the `imsg-platform` plugin under stock Hermes.
- For bridge-backed text and private actions, deliberately configure the optional bridge and inspect the live `imsg status --json` capabilities.
- SMS additionally requires Text Message Forwarding from an iPhone.

## How to Run

Use the narrowest supported surface:

1. Let the `imsg` platform adapter handle ordinary inbound replies; it preserves the source rowid while the client selects the text transport.
2. Use the Hermes `send_message` tool or `cronjob` delivery for proactive text and regular files.
3. Keep explicit SMS and ordinary attachment sends on their legacy `imsg send` paths.
4. Use `imsg_action` for exact-message, poll, effect, native attachment, and group mutations.
5. Use `imsg_poll` only when compatibility with its `send`, `vote`, and `unvote` vocabulary matters.
6. For operator diagnostics or integration code, invoke documented read-only commands through `terminal` or call `ImsgClient` from Python.

Load the relevant reference on demand with `skill_view(name="hermes-imsg-plugin", file_path="references/<file>.md")` before constructing a call.

## Quick Reference

- Ordinary unformatted iMessage/chat text: probe status; use JSON-RPC `send` with `transport=bridge` only when `advanced_features`, `v2_ready`, and method `send` are present
- Legacy text compatibility: use `imsg send` only after successful discovery positively shows bridge `send` unavailable
- Explicit SMS and ordinary attachments: legacy `imsg send`
- Authored formatting: the existing JSON-RPC `send` path with native UTF-16 ranges and `transport=auto`; it does not run the unformatted-text capability-selection probe
- Post-RPC error, timeout, or ambiguous result: stop and reconcile; never switch transports or retry automatically
- Ordinary reply surface: platform adapter `send(chat_id, text, reply_to=...)`
- Proactive delivery: Hermes `send_message` or `cronjob` target `imsg:<target>`
- Native mutations: `imsg_action`
- Poll compatibility: `imsg_poll`
- Read integration: `ImsgClient.chats`, `.group`, `.history`, `.search`, `.status`
- Direct reads: `imsg chats`, `group`, `history`, `search`, `watch`, `status`
- Target selectors: exactly one of `chat_id`, `chat_guid`, `chat_identifier`, `to`
- Native actions: `react`, `edit`, `unsend`, `reply`, `sendWithEffect`, `upload-file`
- Group actions: `renameGroup`, `setGroupIcon`, `addParticipant`, `removeParticipant`, `leaveGroup`
- Poll actions: `poll`, `poll-vote`, `poll-unvote`
- Poll compatibility actions: `send`, `vote`, `unvote`

## Reference Files

- [Calling surfaces and targets](references/calling-surfaces-and-targets.md): load for target syntax, routing, `send_message`, cron, adapter, client, and CLI surface selection.
- [Native message actions](references/native-message-actions.md): load for reactions, edits, unsends, threaded replies, effects, attachments, exact arguments, and examples.
- [Polls and group actions](references/polls-and-group-actions.md): load for poll creation/voting and owner-gated group mutations.
- [Reads, events, and automation](references/reads-events-and-automation.md): load for chats, history, search, watch events, formatting, read receipts, typing, and inbound policy.
- [Capability gates and configuration](references/capability-gates-and-config.md): load for text transport selection, method/selector requirements, split-failure diagnosis, process-group cleanup, YAML, environment variables, and troubleshooting.

## Procedure

1. Identify whether the request is a reply, proactive send, read, native mutation, poll, or group mutation. Completion criterion: one capability class and one calling surface are selected.
2. Resolve the destination without guessing. For an inbound reply, preserve its positive Messages rowid; for tool mutations, supply exactly one target selector. Completion criterion: no competing target aliases remain.
3. Load the matching reference file and copy its exact field names. Completion criterion: the call contains only fields accepted by that action.
4. Select the transport from observable inputs. For unformatted iMessage/chat text, require a successfully decoded status object: ready bridge plus method `send` selects one bridge RPC attempt; any missing or non-literal gate in that object selects legacy send before mutation. Status execution, decoding, or non-object failure aborts. Authored formatting uses its existing `transport=auto` RPC path without this probe. Explicit SMS and ordinary attachments select legacy send directly. Completion criterion: exactly one transport is selected before mutation.
5. Check authorization and per-action capability requirements. Completion criterion: required owner identity, RPC method, and selector are present.
6. Execute once through the selected Hermes tool or documented command. Completion criterion: a structured success object or explicit failure is returned.
7. Verify the destination and native result. After any RPC attempt, timeout, or ambiguous mutation result, do not fall back to another transport and do not resend. Reconcile logs, database state, and processes first. Completion criterion: the returned target and structured result confirm success, or the unresolved outcome is reported without another mutation.

## Pitfalls

- A participant handle is not a group rowid. Ordinary replies must keep the captured rowid.
- `imsg_action` requires exactly one target; conflicting aliases fail before client creation.
- Edit and unsend require a same-chat message with `is_from_me: true`.
- Unnamed tapback removal requires exactly one current bot-authored tapback.
- `reply_to`, `message_id`, and `poll_guid` are verified inside the selected conversation.
- Poll votes require exactly one of `option_id`, one-based `option_index`, or `option` text.
- Group admission does not grant group mutation authority.
- Successful watch, read, typing, or bridge status does not prove the legacy AppleScript send path is healthy; the paths can fail independently.
- A status command or JSON-decoding failure does not prove bridge send unavailable. Abort rather than selecting legacy send.
- Never use legacy send as recovery after bridge RPC begins. A timeout or malformed result may hide a completed mutation.
- Outbound files must be existing regular files. Symlinks, directories, devices, sockets, and FIFOs are rejected.
- A zero exit code without a structured JSON confirmation is a failure.

## Verification

Invoke `imsg status --json` through the `terminal` tool. For ordinary unformatted iMessage/chat text, use bridge RPC only when both bridge flags and method `send` are present. For a private action, also require its exact method and selector from [Capability gates and configuration](references/capability-gates-and-config.md). After a timeout, do not send a probe message; use that reference's read-only split-failure procedure and verify that no attempted `imsg send`, `imsg rpc`, or related `osascript` process remains.
