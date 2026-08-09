# Reads, events, and automation

This reference covers basic read surfaces, inbound event handling, formatting, reactions, read receipts, and typing. Source basis: `hermes_imsg_cli/cli.py`, `hermes_imsg_cli/client.py`, `hermes_imsg_cli/adapter.py`, `docs/PARITY.md`, and `README.md` “Read through Python.”

## Important surface distinction

The plugin registers two agent mutation tools: `imsg_action` and `imsg_poll`. It does not register separate agent tools for chat listing, history, or search. Those reads are available through:

- adapter integration methods,
- Python `ImsgClient`,
- direct `imsg` commands invoked through `terminal`,
- accepted inbound events delivered by the supervised watch process.

Do not invent `imsg_history` or `imsg_search` tool names.

## Read capability matrix

| Capability | Adapter | Python client | Direct CLI |
| --- | --- | --- | --- |
| Recent chats | `list_chats(limit, unread_only)` | `chats(limit, unread_only)` | `imsg chats` |
| Group metadata | live group lookup | `group(chat_id)` | `imsg group` |
| Chat history | `get_history(...)` | `history(...)` | `imsg history` |
| Text search | `search_messages(...)` | `search(...)` | `imsg search` |
| Bridge status | requirement/capability probes | `status()` | `imsg status` |
| Continuous events | supervised adapter watch | integration-owned stream | `imsg watch` |

## Python reads

```python
from hermes_imsg_cli.client import ImsgClient

client = ImsgClient("/opt/homebrew/bin/imsg", timeout=30)
chats = await client.chats(limit=10, unread_only=False)
group = await client.group("42")
messages = await client.history(
    "42",
    limit=20,
    attachments=True,
    convert_attachments=True,
)
hits = await client.search("pizza", match="contains", limit=20)
status = await client.status()
```

History also accepts `participants`, `start`, and `end`. Search `match` accepts `contains` or `exact`. Upstream JSON objects are preserved rather than converted to a reduced local schema.

## Direct read commands

Invoke through `terminal` in an operator-controlled context:

```bash
imsg chats --limit 10 --json
imsg chats --limit 10 --unread-only --json
imsg group --chat-id 42 --json
imsg history --chat-id 42 --limit 20 --attachments --json
imsg history --chat-id 42 --limit 20 --attachments --convert-attachments --json
imsg search --query 'pizza' --match contains --limit 20 --json
imsg status --json
```

`imsg` emits NDJSON, one object per line. These commands can expose message text, handles, participants, GUIDs, and local attachment paths. Do not paste raw output into logs or public issues.

## Continuous inbound watch

The adapter supervises one watch process equivalent to:

```bash
imsg watch --json --attachments --convert-attachments --reactions --debounce 500ms
```

Optional filters include:

```bash
imsg watch --chat-id 42 --since-rowid 1000 --attachments --reactions --json
imsg watch --participants '+12025550101,+12025550102' --start '...' --end '...' --json
```

The adapter:

- reconnects with bounded backoff,
- resumes from an atomically stored `last_rowid`,
- writes the cursor with owner-only mode `0600`,
- drains stderr into a bounded redacted suffix,
- drops outbound echoes and every `is_from_me` event,
- applies sender, chat, participant, mention, and fallback policy,
- preserves the complete decoded object in `raw_message`.

## Inbound event content

Accepted events can preserve:

- exact chat rowid and chat GUID,
- message GUID and text,
- sender and participant metadata,
- group metadata,
- attachments and resolved converted/original paths,
- reply GUID, thread originator, reply text, and reply sender,
- structured reaction context,
- complete poll objects and vote updates.

Display filenames are not treated as local paths. `missing=true` means Messages has not downloaded the attachment. Supported conversion may require `ffmpeg`.

## Access policy

Rules are first match wins. Every populated condition must match.

| Field | Values |
| --- | --- |
| `action` | `allow`, `deny`, `prompt`, `silent_drop` |
| `context` | `dm`, `group`, `any` |
| `senders` | handle list or `any` |
| `chats` | rowid list or `any` |
| `require_participants` | `any_of` and `all_of` lists |
| `require_mention` | group-only boolean |

Without explicit rules, the adapter synthesizes equivalent rules from allowlists. No match uses `fallback_action`, default `silent_drop`. Self messages remain suppressed even under `allow_all`.

Participant validation:

- `fast`: use participant data already in the event.
- `strict`: run `imsg group --chat-id <rowid> --json`, cache for the configured TTL, and deny if lookup or JSON decoding fails.

## Inbound reactions

Reaction notification modes:

- `own`: deliver reactions to bot-authored messages only; default.
- `all`: deliver every authorized inbound reaction.
- `off`: suppress reaction notifications.

`own` checks a bounded cache of confirmed sent message IDs, then same-chat history if needed. Admission policy runs before ownership lookup. Reaction events become readable system-style events rather than ordinary user text.

## Poll vote events

Vote-only rows are disabled by default. Configure DM and group delivery independently:

```yaml
poll_vote_events:
  dm: true
  group: false
  chats: ["3", "42"]
  include_unvotes: false
```

The normal sender, chat, participant, and mention policy still applies. The full poll object remains in `raw_message`.

## Authored formatting

Text sends inspect authored markup and convert supported runs to native UTF-16 ranges:

- bold,
- italic,
- underline,
- strikethrough.

Inline code, fenced code, escapes, and Python dunder identifiers remain literal. When formatting ranges exist, `ImsgClient.send` uses JSON-RPC `send` with one exact target and the established `transport=auto` formatting path without running the unformatted-text capability-selection probe. For unformatted iMessage/chat text, it first probes `imsg status --json`; a ready bridge advertising method `send` selects JSON-RPC with `transport=bridge`, while a successfully decoded status object lacking that gate selects legacy `imsg send` before mutation. Status execution, decoding, or non-object failure aborts. Explicit SMS and any ordinary attachment stay on legacy `imsg send`, and no RPC error, timeout, cancellation, or ambiguous result permits a legacy fallback or resend.

## Basic most-recent-message reaction

The adapter/client basic helper supports standard reactions without exact message targeting:

```python
await client.react("42", "love")
```

```bash
imsg react --chat-id 42 --reaction love --json
```

Accepted basic values are `love`, `like`, `dislike`, `laugh`, `emphasis`, and `question`. Use `imsg_action` for an exact message, removal, or part index.

## Read receipts and typing

These are adapter hooks, not registered agent tools. They depend on the private bridge and are best effort.

Python:

```python
await client.mark_read("42")
await client.typing("42", duration=5)
await client.typing("42", stop=True)
```

Direct CLI:

```bash
imsg read --chat-id 42 --json
imsg typing --chat-id 42 --duration 5s --json
imsg typing --chat-id 42 --stop true --json
```

Use `send_read_receipts: false` and `auto_launch: false` on a normal SIP-enabled host where the bridge is unavailable. Read-only commands, explicit SMS, attachments, and ordinary rowid text replies selected for legacy transport after successful discovery remain usable. Healthy watch, read, typing, or status behavior does not prove that the separate legacy AppleScript send path is healthy; use the split-failure procedure in `capability-gates-and-config.md` for hangs.
