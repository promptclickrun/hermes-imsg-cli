# Calling surfaces and targets

Load this reference before choosing how to call a plugin capability. Source basis: `README.md` sections “Start with the routing invariant,” “Send from cron and send_message,” “Read through Python,” and “Use native actions explicitly”; `hermes_imsg_cli/actions.py` target resolver; `hermes_imsg_cli/cli.py` target parser.

## Surface decision table

| Need | Supported surface | Notes |
| --- | --- | --- |
| Reply to an accepted inbound message | `imsg` platform adapter | Canonical path; preserves the inbound positive chat rowid and selects the text transport from live capability status. |
| Proactive text or regular file | Hermes `send_message` | Use target `imsg:<target>`; text is capability-selected and regular files use legacy send. |
| Scheduled text or regular file | `cronjob` delivery | Set `deliver` to `imsg:<target>` or use the configured home channel; the same transport split applies. |
| Exact-message or private native mutation | `imsg_action` | Registered under toolset `imsg`; requires one exact conversation selector. |
| Poll compatibility | `imsg_poll` | Maps `send`, `vote`, and `unvote` to unified native actions. |
| Programmatic Python integration | `ImsgClient` | Thin async client; preserves upstream JSON objects. |
| Operator diagnostics or direct integration | local `imsg` CLI | Invoke through `terminal`; output may expose private message data. |

## Ordinary reply routing

- The inbound source chat rowid is the reply destination.
- A normal unformatted text reply first probes `imsg status --json` while preserving the rowid.
- If `advanced_features: true`, `v2_ready: true`, and RPC method `send` are all present, it becomes one JSON-RPC `send` call with `chat_id=<rowid>` and `transport=bridge`.
- If a successfully decoded status object positively lacks that complete gate, it becomes legacy `imsg send --chat-id <rowid>` before any mutation attempt.
- If discovery fails, or if the RPC attempt starts and then errors, times out, is cancelled, or returns an ambiguous result, stop. Do not switch transports or resend.
- The plugin never substitutes a participant, chat GUID, or chat identifier for that rowid.
- Framework `reply_to` is correlation metadata only. It never changes an ordinary adapter response into a native thread.
- A native thread requires the explicit `imsg_action` `reply` action and a message ID that resolves inside the selected conversation.

Adapter shape:

```python
await adapter.send("42", "Reply text")
await adapter.send("42", "Still plain", reply_to="CORRELATION-ID")
```

Both calls follow the capability-selected plain-text route to rowid `42`. To create a native Apple Messages thread, invoke the action tool explicitly:

```json
{"action":"reply","chat_id":"42","message_id":"MESSAGE-GUID","text":"Threaded text"}
```

## Proactive Hermes delivery

Supported destination forms after the `imsg:` platform prefix:

- `imsg:42`: Messages chat rowid; preferred for known conversations and groups.
- `imsg:12025550105`: bare direct phone target; the standalone path rehydrates the country code according to configuration.
- Configured home channel: `IMSG_HOME_CHANNEL=42` or a direct E.164 handle.

Cron example:

```text
cronjob(action="create", schedule="...", prompt="...", deliver="imsg:42")
```

The standalone sender applies the same status-gated route to unformatted iMessage/chat text. Explicit SMS and every ordinary regular-file attachment use legacy `imsg send`; attachment presence prevents a bridge text attempt. Upstream accepts one file per command, so multiple files are sent sequentially. A partial failure reports `failed_index`, `attempted_attachments`, and `succeeded_message_ids`.

## `imsg_action` target contract

Every call supplies exactly one:

| Field | Meaning | Resolution |
| --- | --- | --- |
| `chat_id` | Positive Messages chat rowid as a string | Existing conversation lookup; rejects zero, non-decimal, or over 19 digits. |
| `chat_guid` | Exact Messages chat GUID | Existing chat lookup, except proactive `sendWithEffect`, `upload-file`, and `poll` may synthesize directly. |
| `chat_identifier` | Exact identifier from chat metadata | Must resolve to an existing conversation. |
| `to` | `imessage:`, `sms:`, phone, or email direct target | Must resolve to an existing chat except proactive effect, file, and poll creation. |

Direct target examples:

```json
{"action":"sendWithEffect","chat_id":"42","text":"Hello","effect":"loud"}
```

```json
{"action":"upload-file","chat_guid":"iMessage;+;chat-guid","file_path":"/absolute/file.pdf"}
```

```json
{"action":"poll","to":"imessage:+12025550101","question":"Dinner?","options":["Pizza","Sushi"]}
```

Do not include two target fields in one call. Message, poll-vote, and group actions require an existing conversation; direct synthesis is intentionally limited.

## Python client target forms

`ImsgClient.send(target, ...)` accepts:

- `chat_id:42`, `chatid:42`, or `chat:42`
- `chat_guid:<guid>`, `chatguid:<guid>`, or `guid:<guid>`
- `chat_identifier:<identifier>`, `chatidentifier:<identifier>`, or `chatident:<identifier>`
- `imessage:<handle>`, `sms:<handle>`, or `auto:<handle>`
- leading `+` or an email address, which forces direct `--to`
- a short decimal string, which is treated as a chat rowid

```python
from hermes_imsg_cli.client import ImsgClient

client = ImsgClient("/opt/homebrew/bin/imsg", timeout=30)
await client.send("chat_id:42", "Hello")
await client.send("imessage:+12025550101", "Hello")
await client.send("sms:+12025550101", "Hello", no_sms_fallback=False)
await client.send("chat_guid:iMessage;+;chat-guid", "Hello")
```

Optional send fields are `file`, `service`, `region`, and `no_sms_fallback`. `service` accepts `imessage`, `sms`, or `auto`.

For unformatted text with service `imessage` or `auto`, the client probes status and selects bridge RPC only when the ready bridge advertises method `send`. Service `sms` and any non-empty `file` select legacy send immediately. Text with supported authored formatting ranges uses the established JSON-RPC formatting path with `transport=auto` without running the unformatted-text capability-selection probe. No RPC outcome selects a second transport.

## Legacy direct CLI targets

Use these commands for explicit SMS, attachments, or a compatibility path selected before mutation because successful discovery showed bridge `send` unavailable:

```bash
imsg send --chat-id 42 --text 'Hello' --json
imsg send --chat-guid 'iMessage;+;chat-guid' --text 'Hello' --json
imsg send --chat-identifier 'iMessage;-;+12025550101' --text 'Hello' --json
imsg send --to '+12025550101' --service imessage --text 'Hello' --json
imsg send --to '+12025550101' --service sms --text 'Hello' --json
imsg send --chat-id 42 --file '/absolute/file.pdf' --json
```

Plain `imsg send` requires text or one file. A chat rowid must be a positive decimal value. Do not bypass the capability decision by manually choosing this command for an ordinary text send. Avoid passing raw message output into logs or issues.

## Result handling

- Native tool success includes `success`, `action`, resolved `chat_id`, `chat_guid`, `chat_identifier`, `to`, and `result`.
- Native tool failure includes `success: false`, `action`, a redacted `error`, and sometimes `exit_code`.
- Standalone delivery returns `success`, `platform`, `chat_id`, and confirmed message ID fields.
- A zero subprocess exit without a JSON object is not success.
- Capability discovery failure selects no transport; report the discovery error.
- After any RPC process starts, its error, timeout, cancellation, malformed output, or missing structured confirmation is terminal for that attempt. Never fall back to legacy send or resend automatically.
- Reconcile an ambiguous result with redacted gateway timing, read-only outbound database rows, and exact process ancestry as described in `capability-gates-and-config.md`.
