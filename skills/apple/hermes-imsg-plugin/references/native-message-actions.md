# Native message actions

This reference covers `react`, `edit`, `unsend`, `reply`, `sendWithEffect`, and `upload-file`. Source basis: `hermes_imsg_cli/actions.py`, `hermes_imsg_cli/client.py`, `hermes_imsg_cli/cli.py`, and `docs/PARITY.md` “Native message actions.” Every `imsg_action` call also requires exactly one target from `references/calling-surfaces-and-targets.md`.

## Capability matrix

| Action | Required fields | Optional fields | Python client | Direct CLI |
| --- | --- | --- | --- | --- |
| `react` | `message_id`; `reaction` when adding | `remove=false`, `part_index=0` | `tapback(...)` | `imsg tapback` |
| `edit` | `message_id`, `text` | `backwards_compatibility_message`, `part_index=0` | `edit(...)` | `imsg edit` |
| `unsend` | `message_id` | `part_index=0` | `unsend(...)` | `imsg unsend` |
| `reply` | `message_id`; text, file, or both | `text`, `file_path`, `part_index=0` | `send_rich(...)` | `imsg send-rich` |
| `sendWithEffect` | `text`, `effect` | `part_index=0` | `send_rich(...)` | `imsg send-rich` |
| `upload-file` | `file_path` | `as_voice=false` | `send_attachment(...)` | `imsg send-attachment` |

Message IDs are looked up in up to 1,000 records from the selected chat. Cross-chat references fail before mutation. Edit and unsend additionally require `is_from_me: true`.

## Reactions

Add an exact-message tapback:

```json
{"action":"react","chat_id":"42","message_id":"MESSAGE-GUID","reaction":"like"}
```

Remove a named tapback from part 2:

```json
{"action":"react","chat_id":"42","message_id":"MESSAGE-GUID","reaction":"emphasize","remove":true,"part_index":2}
```

Remove the one current bot-authored tapback without naming it:

```json
{"action":"react","chat_id":"42","message_id":"MESSAGE-GUID","remove":true}
```

Accepted names: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`. The builder also normalizes `emphasis` to `emphasize`. Unnamed removal fails unless history contains exactly one bot-authored current reaction.

Python:

```python
await client.tapback("CHAT-GUID", "MESSAGE-GUID", "like")
await client.tapback("CHAT-GUID", "MESSAGE-GUID", "like", remove=True, part_index=0)
```

Direct CLI through `terminal`:

```bash
imsg tapback --chat 'CHAT-GUID' --message 'MESSAGE-GUID' --kind like --part 0 --json
imsg tapback --chat 'CHAT-GUID' --message 'MESSAGE-GUID' --kind like --part 0 --remove --json
```

The separate basic helper `client.react(chat_id, reaction)` calls `imsg react --chat-id <rowid>` against the most recent message. Use `imsg_action` when exact message targeting matters.

## Edit

```json
{"action":"edit","chat_id":"42","message_id":"BOT-MESSAGE-GUID","text":"Corrected text"}
```

With compatibility text and a message part:

```json
{"action":"edit","chat_id":"42","message_id":"BOT-MESSAGE-GUID","text":"Corrected text","backwards_compatibility_message":"Updated: Corrected text","part_index":1}
```

Python:

```python
await client.edit(
    "CHAT-GUID",
    "BOT-MESSAGE-GUID",
    "Corrected text",
    backwards_compatibility_message="Updated: Corrected text",
    part_index=1,
)
```

Direct CLI:

```bash
imsg edit --chat 'CHAT-GUID' --message 'BOT-MESSAGE-GUID' --new-text 'Corrected text' --bc-text 'Corrected text' --part 0 --json
```

When compatibility text is omitted, the client uses the new text for both fields.

## Unsend

```json
{"action":"unsend","chat_id":"42","message_id":"BOT-MESSAGE-GUID"}
```

Python and CLI:

```python
await client.unsend("CHAT-GUID", "BOT-MESSAGE-GUID", part_index=0)
```

```bash
imsg unsend --chat 'CHAT-GUID' --message 'BOT-MESSAGE-GUID' --part 0 --json
```

Only bot-authored messages in the selected chat are eligible.

## Native threaded reply

Text reply:

```json
{"action":"reply","chat_id":"42","message_id":"MESSAGE-GUID","text":"Threaded response"}
```

Attachment reply:

```json
{"action":"reply","chat_id":"42","message_id":"MESSAGE-GUID","file_path":"/absolute/file.pdf"}
```

Text plus one attachment:

```json
{"action":"reply","chat_id":"42","message_id":"MESSAGE-GUID","text":"See attached","file_path":"/absolute/file.pdf","part_index":0}
```

Python:

```python
await client.send_rich(
    "CHAT-GUID",
    "See attached",
    reply_to="MESSAGE-GUID",
    file="/absolute/file.pdf",
    part_index=0,
)
```

Direct CLI:

```bash
imsg send-rich --chat 'CHAT-GUID' --text 'Threaded response' --part 0 --reply-to 'MESSAGE-GUID' --json
imsg send-rich --chat 'CHAT-GUID' --text 'See attached' --part 0 --reply-to 'MESSAGE-GUID' --file '/absolute/file.pdf' --json
```

The tool requires text, one file, or both. The attachment must be an existing regular file.

## Effects

```json
{"action":"sendWithEffect","chat_id":"42","text":"Surprise","effect":"confetti"}
```

Accepted aliases:

- `slam` or `impact`
- `loud`
- `gentle`
- `invisibleink` or `invisible-ink`
- `confetti`, `lasers`, `fireworks`
- `balloon` or `balloons`
- `heart`, `echo`
- `happybirthday` or `happy-birthday`
- `shootingstar` or `shooting-star`
- `sparkles`, `spotlight`

Canonical Apple effect IDs from the plugin are also accepted. The tool converts aliases before mutation.

Python and CLI:

```python
await client.send_rich(
    "CHAT-GUID",
    "Surprise",
    effect="com.apple.MobileSMS.expressivesend.confetti",
)
```

```bash
imsg send-rich --chat 'CHAT-GUID' --text 'Surprise' --part 0 --effect 'com.apple.MobileSMS.expressivesend.confetti' --json
```

## Native file or voice attachment

```json
{"action":"upload-file","chat_id":"42","file_path":"/absolute/file.pdf"}
```

```json
{"action":"upload-file","chat_id":"42","file_path":"/absolute/audio.m4a","as_voice":true}
```

Python and CLI:

```python
await client.send_attachment("CHAT-GUID", "/absolute/audio.m4a", as_voice=True)
```

```bash
imsg send-attachment --chat 'CHAT-GUID' --file '/absolute/audio.m4a' --audio --json
```

Regular files only. Symlinks, directories, FIFOs, sockets, devices, and missing paths fail before send.
