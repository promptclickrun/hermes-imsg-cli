# Compatibility and migration notes

## Scope

The plugin uses the locally installed `imsg` executable and keeps platform
policy in Hermes. Upgrading the plugin does not modify Messages.app, TCC, SIP,
the `imsg` package, or the Hermes core checkout.

## Version 2.0.2 group silence

- A group message that doesn't name the agent (`mention_patterns`) or reply
  to one of its messages now reaches Hermes with `reply_expected: false`, so
  the agent can stay silent when people talk to each other. Hermes no longer
  answers those with its "returned only a silence marker" warning. Group
  Tapbacks and poll votes are treated the same way. DMs are unchanged.
- `group_silence: false` (`IMSG_GROUP_SILENCE=false`) restores the old
  behavior.

## Version 2.0.1 safety changes

- Ordinary text, document, image, video, and voice responses ignore Hermes
  `reply_to` correlation and stay unthreaded on the exact source rowid. Use the
  explicit `imsg_action` `reply` action when a native Apple Messages thread is
  intended.
- Inbound quote context now requires `thread_originator_guid` or
  `threadOriginatorGuid`. A standalone `reply_to_guid` no longer produces a
  synthetic “Replying to” context block.
- Reaction notifications now default to `off`. The default watch command omits
  `--reactions`, and any reaction still received is discarded before agent
  processing.

An existing explicit `reaction_notifications: own` or `all` setting remains an
opt-in after upgrade. Change it to `off` unless Tapbacks are intentionally
allowed to initiate agent turns.

## Preserved behavior

- platform name `imsg` and plugin name `imsg-platform`
- exact rowid routing for ordinary inbound replies
- legacy flat allowlists and synthesized rules
- default `silent_drop`, mention behavior, identity aliases, read receipts,
  and bridge re-arm setting
- state file format containing `last_rowid`
- cron and standalone sender with the stock Hermes phone-target workaround
- text, document, image, video, and voice platform methods
- chats, group, history, search, and native NDJSON parsing

## Added behavior

- native styled text with UTF-16 formatting ranges
- explicit same-chat threaded replies and reply attachments through
  `imsg_action`
- structured inbound reaction delivery with `own`, `all`, and `off` modes
- exact-message tapback add and remove, including unnamed safe removal
- message edit and unsend with bot-authorship checks
- expressive send effects with canonical Apple effect identifiers
- capability-gated file and voice actions
- native poll creation, vote, and selective unvote
- native group rename, icon, membership, and leave operations
- authenticated owner gating for group mutations from messaging surfaces
- a unified `imsg_action` tool plus the compatible `imsg_poll` tool
- per-action method and selector checks from `imsg status --json`

The action layer is additive. Existing plain sends remain available when the
private bridge is not configured.

## Strengthened behavior

1. Strict participant validation uses `group --chat-id`.
2. Command JSON is parsed as NDJSON while one compatibility array is accepted.
3. Missing and zero chat rowids fail closed.
4. Converted attachment paths are preferred and `missing=true` files are not
   handed to Hermes.
5. Outbound paths must be regular files.
6. A send or mutation requires structured JSON confirmation.
7. Raw watch lines and private payloads are excluded from logs.
8. The recovery cursor is atomically persisted with owner-only permissions.
9. Native action references are verified inside the selected conversation.
10. Caller metadata cannot redirect ordinary replies or implicitly request a
    native thread.
11. Native reply context is emitted only from the thread-originator field, not
    from an unreliable recent-message reply field.
12. Inbound reactions default to silent and are dropped before the agent
    pipeline when disabled.

## From direct scripts

| Direct script concept | Plugin behavior |
| --- | --- |
| Run and restart `watch` | One supervised process resumes from the persisted rowid. |
| Route by participant or handle | Ordinary inbound replies use the exact source rowid. |
| Maintain a script allowlist | Use direct-message, group, chat, participant, mention, or ordered rules. |
| Parse `jq -s` arrays | The plugin parses NDJSON and preserves unknown object fields. |
| Store a cursor | Use `IMSG_STATE_FILE` or the profile-local state file. |
| Send from cron | Use `deliver=imsg` and `IMSG_HOME_CHANNEL`. |
| Invoke native mutations | Use `imsg_action` with one exact target. |
| Create or vote on polls | Use `imsg_action` or the compatible `imsg_poll` tool. |

Scripts that use native account, sticker, scheduled-message, or database
maintenance commands remain separate operator tools. They are outside the
Hermes messaging action contract.

## Installed executable compatibility

Basic watch, history, text, and file behavior depends on the command flags
accepted by the installed executable. Advanced behavior additionally depends
on private bridge state and the granular methods and selectors in
`status --json`.

Use this read-only upgrade check:

```bash
brew update
brew upgrade steipete/tap/imsg
imsg --version
imsg watch --help
imsg send --help
imsg rpc --help
imsg status --json | jq '{advanced_features,v2_ready,rpc_methods,selectors}'
scripts/validate.sh
```

Do not infer action availability from a version string. The plugin uses the
status object at mutation time.

## Rollback

1. Stop Hermes through the normal operator procedure.
2. Restore the previous plugin checkout or symlink target.
3. Keep the state file because its `last_rowid` format is unchanged.
4. Restore an earlier `imsg` package only after proving a command
   incompatibility.
5. Start Hermes and verify read-only discovery before any user-approved live
   messaging test.

Deleting the cursor changes replay behavior and is not a routine rollback step.
