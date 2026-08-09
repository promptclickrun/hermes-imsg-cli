# Polls and group actions

This reference covers native polls and group mutations. Source basis: `hermes_imsg_cli/actions.py`, `hermes_imsg_cli/cli.py`, `hermes_imsg_cli/client.py`, `docs/PARITY.md` sections “Polls” and “Groups,” and `docs/OPERATIONS.md` “Native action invocation.”

## Poll call surfaces

Polls have three supported surfaces:

1. Unified `imsg_action`: actions `poll`, `poll-vote`, and `poll-unvote`.
2. Compatibility `imsg_poll`: actions `send`, `vote`, and `unvote`; delegates to the same resolver and gates.
3. Python `ImsgClient` or direct `imsg poll` commands for integration and operator use.

Every tool call requires exactly one conversation target. Poll creation may use any target selector. Voting and unvoting require the poll to resolve inside an existing conversation.

## Create a poll

Unified tool:

```json
{"action":"poll","chat_id":"42","question":"Dinner?","options":["Pizza","Sushi"]}
```

With optional comment and same-chat reply reference:

```json
{"action":"poll","chat_id":"42","question":"Dinner?","options":["Pizza","Sushi"],"comment":"Vote by 5","reply_to":"MESSAGE-GUID"}
```

Compatibility tool:

```json
{"action":"send","chat_id":"42","question":"Dinner?","options":["Pizza","Sushi"],"comment":"Vote by 5"}
```

Constraints:

- `question` must be non-empty.
- `options` must contain 2 to 12 non-empty, distinct strings.
- `comment` is optional visible text.
- `reply_to` is optional and must resolve in the selected chat.

Python:

```python
await client.send_poll(
    "42",
    "Dinner?",
    ["Pizza", "Sushi"],
    comment="Vote by 5",
    reply_to="MESSAGE-GUID",
)
```

The client creates polls through one `imsg rpc --json` process using method `poll.send`.

Direct CLI through `terminal`:

```bash
imsg poll send --chat-id 42 --question 'Dinner?' --comment 'Vote by 5' --option Pizza --option Sushi --json
imsg poll send --chat 'CHAT-GUID' --question 'Dinner?' --option Pizza --option Sushi --json
```

## Vote on a poll

Choose exactly one selector: exact option ID, one-based index, or option text.

Unified tool:

```json
{"action":"poll-vote","chat_id":"42","poll_guid":"POLL-GUID","option_id":"OPTION-ID"}
```

```json
{"action":"poll-vote","chat_id":"42","poll_guid":"POLL-GUID","option_index":2}
```

```json
{"action":"poll-vote","chat_id":"42","poll_guid":"POLL-GUID","option":"Sushi"}
```

Compatibility tool uses `action: "vote"` with the same remaining fields.

Python:

```python
await client.vote_poll("42", "POLL-GUID", option_id="OPTION-ID")
await client.vote_poll("42", "POLL-GUID", option_index=2)
await client.vote_poll("42", "POLL-GUID", option="Sushi")
```

Direct CLI:

```bash
imsg poll vote --chat-id 42 --poll 'POLL-GUID' --option-id 'OPTION-ID' --json
imsg poll vote --chat-id 42 --poll 'POLL-GUID' --option-index 2 --json
imsg poll vote --chat-id 42 --poll 'POLL-GUID' --option 'Sushi' --json
```

## Remove one vote

Unified tool:

```json
{"action":"poll-unvote","chat_id":"42","poll_guid":"POLL-GUID","option_index":2}
```

Compatibility tool uses `action: "unvote"`. Python uses `client.unvote_poll(...)`. Direct CLI:

```bash
imsg poll unvote --chat-id 42 --poll 'POLL-GUID' --option-index 2 --json
```

Unvote is selective and removes only the resolved option. It does not clear every vote.

## Poll capability requirements

- Create: method `poll.send` or `messages.poll.send`; selector `pollPayloadMessage`.
- Vote: method `poll.vote` or `messages.poll.vote`; selector `pollVoteMessage`.
- Unvote: method `poll.unvote`, `polls.unvote`, or `messages.poll.unvote`; selector `pollVoteMessage`.
- All require `advanced_features: true` and `v2_ready: true`.

## Group action matrix

| Action | Required action field | Python client | Direct CLI |
| --- | --- | --- | --- |
| `renameGroup` | `display_name` | `rename_group(...)` | `imsg chat-name` |
| `setGroupIcon` | `file_path` | `set_group_icon(...)` | `imsg chat-photo` |
| `addParticipant` | `address` | `add_participant(...)` | `imsg chat-add-member` |
| `removeParticipant` | `address` | `remove_participant(...)` | `imsg chat-remove-member` |
| `leaveGroup` | none | `leave_group(...)` | `imsg chat-leave` |

All group actions require a live target whose metadata reports `is_group: true`.

## Rename a group

```json
{"action":"renameGroup","chat_id":"42","display_name":"Example Group"}
```

```python
await client.rename_group("CHAT-GUID", "Example Group")
```

```bash
imsg chat-name --chat 'CHAT-GUID' --name 'Example Group' --json
```

## Set a group icon

```json
{"action":"setGroupIcon","chat_id":"42","file_path":"/absolute/icon.png"}
```

```python
await client.set_group_icon("CHAT-GUID", "/absolute/icon.png")
```

```bash
imsg chat-photo --chat 'CHAT-GUID' --file '/absolute/icon.png' --json
```

The icon must pass regular-file validation.

## Add or remove a participant

```json
{"action":"addParticipant","chat_id":"42","address":"+12025550101"}
```

```json
{"action":"removeParticipant","chat_id":"42","address":"+12025550101"}
```

```python
await client.add_participant("CHAT-GUID", "+12025550101")
await client.remove_participant("CHAT-GUID", "+12025550101")
```

```bash
imsg chat-add-member --chat 'CHAT-GUID' --address '+12025550101' --json
imsg chat-remove-member --chat 'CHAT-GUID' --address '+12025550101' --json
```

## Leave a group

```json
{"action":"leaveGroup","chat_id":"42"}
```

```python
await client.leave_group("CHAT-GUID")
```

```bash
imsg chat-leave --chat 'CHAT-GUID' --json
```

## Group authorization

- Local interactive surfaces `cli`, `codex`, `desktop`, `local`, and `tui` may call group mutations directly.
- Messaging callers must have an authenticated user ID in `IMSG_GROUP_ACTION_ADMINS`.
- If that variable is absent, the plugin falls back to `IMSG_ALLOWED_USERS`.
- Group conversation admission, `allowed_chats`, and `allow_all` do not grant mutation authority.
- Unauthenticated API and webhook contexts fail closed.
- Do not treat a denied group mutation as a capability problem until both group metadata and caller authorization are checked.
