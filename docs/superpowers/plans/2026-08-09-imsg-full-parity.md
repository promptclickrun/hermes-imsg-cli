# Hermes iMessage Full Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Hermes `imsg` platform every native iMessage action exposed by the current reference channel while preserving existing Hermes routing, policy, recovery, and standalone delivery.

**Architecture:** Keep the stable Python adapter and supervised `imsg watch` process. Add pure native command builders, a capability model, typed client methods, and a unified Hermes action tool; integrate trusted conversation references into threaded replies and inbound native-event normalization.

**Tech Stack:** Python 3.10+, asyncio subprocesses, Hermes platform and tool plugin APIs, `imsg` CLI and JSON-RPC, pytest, static GitHub Pages HTML/CSS/JavaScript.

## Global Constraints

- Preserve exact positive Messages rowid routing for ordinary inbound replies.
- Advanced actions may use a chat GUID only after it is bound to the current conversation.
- Preserve every existing Hermes rule, allowlist, mention, participant, cron, standalone-send, and recovery behavior.
- Support `react`, `edit`, `unsend`, `reply`, `sendWithEffect`, `upload-file`, `renameGroup`, `setGroupIcon`, `addParticipant`, `removeParticipant`, `leaveGroup`, `poll`, `poll-vote`, and `poll-unvote`.
- Keep `imsg_poll` as a backward-compatible tool.
- Reject conflicting target aliases and cross-chat message identifiers before mutation.
- Reject unsafe outbound attachment paths before mutation.
- Return structured, PII-redacted errors and require valid JSON success objects.
- Do not perform a live message or mutation during automated verification.
- Do not stage, commit, push, or open a pull request without explicit user authorization.
- Do not use em dash characters in repository prose.

---

### Task 1: Native command and formatted-text contracts

**Files:**
- Modify: `hermes_imsg_cli/cli.py`
- Create: `hermes_imsg_cli/formatting.py`
- Create: `tests/test_native_actions.py`
- Create: `tests/test_formatting.py`

**Interfaces:**
- Consumes: validated strings, positive part indexes, and one exact target.
- Produces: `build_tapback_args`, `build_edit_args`, `build_unsend_args`, `build_rich_send_args`, `build_send_rpc_request`, `build_send_attachment_args`, `build_chat_name_args`, `build_chat_photo_args`, `build_chat_member_args`, `build_chat_leave_args`, and `extract_markdown_format_runs`.

- [x] **Step 1: Write failing literal command tests**

```python
def test_build_tapback_remove_includes_part_and_remove():
    assert cli.build_tapback_args("iMessage;+;chat", "MSG", "like", remove=True, part_index=2) == [
        "tapback", "--chat", "iMessage;+;chat", "--message", "MSG",
        "--kind", "like", "--part", "2", "--remove", "--json",
    ]

def test_build_rich_send_combines_reply_effect_and_file():
    assert cli.build_rich_send_args(
        "CHAT", "hello", reply_to="MSG", effect="com.apple.MobileSMS.expressivesend.loud",
        part_index=1, file_path="/tmp/a.jpg",
    ) == [
        "send-rich", "--chat", "CHAT", "--text", "hello", "--part", "1",
        "--effect", "com.apple.MobileSMS.expressivesend.loud", "--reply-to", "MSG",
        "--file", "/tmp/a.jpg", "--json",
    ]

def test_build_send_rpc_request_preserves_exact_rowid_and_formatting():
    assert cli.build_send_rpc_request(
        "req-1", chat_id="16", text="bold",
        formatting=[{"start": 0, "length": 4, "styles": ["bold"]}],
    ) == {
        "jsonrpc": "2.0", "id": "req-1", "method": "send",
        "params": {
            "chat_id": 16, "text": "bold", "service": "auto", "transport": "auto",
            "formatting": [{"start": 0, "length": 4, "styles": ["bold"]}],
        },
    }

@pytest.mark.parametrize(("target", "prefix"), [
    ("chat_id:16", ["send", "--chat-id", "16"]),
    ("chat_guid:iMessage;+;chat", ["send", "--chat-guid", "iMessage;+;chat"]),
    ("chat_identifier:iMessage;-;+12025550101",
     ["send", "--chat-identifier", "iMessage;-;+12025550101"]),
    ("imessage:+12025550101", ["send", "--to", "+12025550101"]),
    ("sms:+12025550101", ["send", "--to", "+12025550101"]),
])
def test_build_plain_send_preserves_explicit_target_kind(target, prefix):
    assert cli.build_plain_send_args(target, "hello")[:3] == prefix
```

- [x] **Step 2: Run the focused tests and confirm missing-builder failures**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_native_actions.py -q`

Expected: failures naming missing native builder attributes.

- [x] **Step 3: Implement strict builders**

```python
def _validate_part_index(value: Any) -> int:
    if isinstance(value, bool) or int(value) < 0:
        raise ValueError("part_index must be a non-negative integer")
    return int(value)

def build_tapback_args(chat_guid, message_guid, kind, *, remove=False, part_index=0):
    args = ["tapback", "--chat", _required(chat_guid, "chat_guid"),
            "--message", _required(message_guid, "message_id"),
            "--kind", _tapback_kind(kind), "--part", str(_validate_part_index(part_index))]
    if remove:
        args.append("--remove")
    return [*args, "--json"]
```

Extend `build_plain_send_args` and `build_send_rpc_request` with a shared pure
target parser. It accepts `chat_id:`, `chat_guid:`, `chat_identifier:`,
`imessage:`, `sms:`, and `auto:` plus the existing bare rowid, phone, and email
forms. Service-prefixed handles strip the prefix and set the matching service.
Conflicting explicit `service` values fail before subprocess execution.

Implement the other builders with these literal contracts:

```python
build_edit_args("CHAT", "MSG", "new", backwards_compatibility_message="fallback", part_index=0)
# ["edit", "--chat", "CHAT", "--message", "MSG", "--new-text", "new",
#  "--bc-text", "fallback", "--part", "0", "--json"]

build_unsend_args("CHAT", "MSG", part_index=0)
# ["unsend", "--chat", "CHAT", "--message", "MSG", "--part", "0", "--json"]

build_send_attachment_args("CHAT", "/tmp/a.m4a", as_voice=True)
# ["send-attachment", "--chat", "CHAT", "--file", "/tmp/a.m4a", "--audio", "--json"]

build_chat_name_args("CHAT", "Family")
# ["chat-name", "--chat", "CHAT", "--name", "Family", "--json"]

build_chat_photo_args("CHAT", "/tmp/icon.png")
# ["chat-photo", "--chat", "CHAT", "--file", "/tmp/icon.png", "--json"]

build_chat_member_args("add", "CHAT", "+12025550101")
# ["chat-add-member", "--chat", "CHAT", "--address", "+12025550101", "--json"]

build_chat_member_args("remove", "CHAT", "+12025550101")
# ["chat-remove-member", "--chat", "CHAT", "--address", "+12025550101", "--json"]

build_chat_leave_args("CHAT")
# ["chat-leave", "--chat", "CHAT", "--json"]
```

- [x] **Step 4: Write failing formatting goldens**

```python
@pytest.mark.parametrize(("source", "expected"), [
    ("plain", {"text": "plain", "ranges": []}),
    ("😀 **bold _and italic_** ~~gone~~", {
        "text": "😀 bold and italic gone",
        "ranges": [
            {"start": 3, "length": 15, "styles": ["bold"]},
            {"start": 8, "length": 10, "styles": ["italic"]},
            {"start": 19, "length": 4, "styles": ["strikethrough"]},
        ],
    }),
    ("<u>x</u> and <ins>y</ins>", {
        "text": "x and y",
        "ranges": [
            {"start": 0, "length": 1, "styles": ["underline"]},
            {"start": 6, "length": 1, "styles": ["underline"]},
        ],
    }),
    ("`*literal*` def __init__(self):", {
        "text": "`*literal*` def __init__(self):", "ranges": [],
    }),
])
def test_extract_markdown_format_runs(source, expected):
    assert extract_markdown_format_runs(source) == expected
```

- [x] **Step 5: Run formatting tests and confirm the missing-module failure**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_formatting.py -q`

Expected: import failure for `hermes_imsg_cli.formatting`.

- [x] **Step 6: Implement formatting with UTF-16 coordinates**

```python
def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2

def extract_markdown_format_runs(source: str) -> dict[str, Any]:
    tokens = _tokenize_code_and_dunder_literals(source)
    rendered_text, styled_spans = _render_supported_markdown(tokens.text)
    restored_text, restored_spans = tokens.restore(rendered_text, styled_spans)
    return {
        "text": restored_text,
        "ranges": [
            {"start": _utf16_length(restored_text[:span.start]),
             "length": _utf16_length(restored_text[span.start:span.end]),
             "styles": list(span.styles)}
            for span in restored_spans
        ],
    }
```

The tokenizer must protect inline code, fenced code, intraword underscores,
and callable, qualified, indexed, or declared Python dunder identifiers. The
renderer handles nested `**` or `__` bold, `*` or `_` italic, `~~` strike, and
`<u>` or `<ins>` underline without consuming unmatched markers.

- [x] **Step 7: Run focused and existing CLI tests**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_native_actions.py tests/test_formatting.py tests/test_cli_parity.py tests/test_send_direct.py -q`

Expected: all selected tests pass.

- [x] **Step 8: Review checkpoint**

Run: `git diff --check && git diff -- hermes_imsg_cli/cli.py hermes_imsg_cli/formatting.py tests/test_native_actions.py tests/test_formatting.py`

Do not commit without explicit authorization.

### Task 2: Capability model

**Files:**
- Create: `hermes_imsg_cli/capabilities.py`
- Modify: `tests/test_native_actions.py`

**Interfaces:**
- Consumes: the dictionary returned by `imsg status --json`.
- Produces: `ImsgCapabilities.from_status(status)`, `supports_action(action)`, and `require_action(action)`.

- [x] **Step 1: Write failing capability tests**

```python
def test_edit_requires_method_and_one_edit_selector():
    caps = ImsgCapabilities.from_status({
        "advanced_features": True,
        "v2_ready": True,
        "rpc_methods": ["message.edit"],
        "selectors": {"editMessage": False, "editMessageItem": False},
    })
    with pytest.raises(ImsgCapabilityError, match="editMessage"):
        caps.require_action("edit")

def test_poll_vote_accepts_rpc_alias_and_selector():
    caps = ImsgCapabilities.from_status({
        "advanced_features": True,
        "v2_ready": True,
        "rpc_methods": ["messages.poll.vote"],
        "selectors": {"pollVoteMessage": True},
    })
    assert caps.supports_action("poll-vote") is True
```

- [x] **Step 2: Verify the tests fail because the module is absent**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_native_actions.py -q`

Expected: import failure for `hermes_imsg_cli.capabilities`.

- [x] **Step 3: Implement immutable capability parsing and action requirements**

```python
@dataclass(frozen=True)
class ActionRequirement:
    methods: tuple[str, ...]
    selectors_any: tuple[str, ...] = ()

ACTION_REQUIREMENTS = {
    "react": ActionRequirement(("tapback",)),
    "edit": ActionRequirement(("message.edit",), ("editMessage", "editMessageItem")),
    "unsend": ActionRequirement(("message.unsend",), ("retractMessagePart",)),
    "reply": ActionRequirement(("send.rich",)),
    "sendWithEffect": ActionRequirement(("send.rich",)),
    "upload-file": ActionRequirement(("send.attachment",)),
    "renameGroup": ActionRequirement(("group.rename",)),
    "setGroupIcon": ActionRequirement(("group.setIcon",)),
    "addParticipant": ActionRequirement(("group.addParticipant",)),
    "removeParticipant": ActionRequirement(("group.removeParticipant",)),
    "leaveGroup": ActionRequirement(("group.leave",)),
    "poll": ActionRequirement(("poll.send", "messages.poll.send"), ("pollPayloadMessage",)),
    "poll-vote": ActionRequirement(("poll.vote", "messages.poll.vote"), ("pollVoteMessage",)),
    "poll-unvote": ActionRequirement(("poll.unvote", "polls.unvote", "messages.poll.unvote"), ("pollVoteMessage",)),
}
```

The parser must ignore malformed method and selector entries, require both advanced flags, and produce actionable errors.

- [x] **Step 4: Run capability tests**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_native_actions.py -q`

Expected: all tests pass.

- [x] **Step 5: Review checkpoint**

Run: `git diff --check && git diff -- hermes_imsg_cli/capabilities.py tests/test_native_actions.py`

Do not commit without explicit authorization.

### Task 3: Typed native client operations

**Files:**
- Modify: `hermes_imsg_cli/client.py`
- Modify: `tests/test_client.py`
- Modify: `tests/test_native_actions.py`

**Interfaces:**
- Consumes: Task 1 builders and Task 2 `ImsgCapabilities`.
- Produces: native formatted `send`, `tapback`, `edit`, `unsend`, `send_rich`, `send_attachment`, `rename_group`, `set_group_icon`, `add_participant`, `remove_participant`, `leave_group`, plus existing poll methods using the shared capability model.

- [x] **Step 1: Write failing client behavior tests**

```python
@pytest.mark.asyncio
async def test_edit_probes_then_executes_exact_cli_contract():
    runner = RecordingRunner([
        (0, json.dumps(CAPABLE_STATUS), ""),
        (0, json.dumps({"success": True}), ""),
    ])
    client = ImsgClient("imsg", runner=runner)
    await client.edit("CHAT", "MSG", "updated", part_index=1)
    assert runner.calls == [
        ["status", "--json"],
        ["edit", "--chat", "CHAT", "--message", "MSG", "--new-text", "updated",
         "--bc-text", "updated", "--part", "1", "--json"],
    ]

@pytest.mark.asyncio
async def test_unsafe_reply_attachment_never_invokes_mutation(tmp_path):
    runner = RecordingRunner([])
    client = ImsgClient("imsg", runner=runner)
    with pytest.raises(ImsgCommandError, match="regular file"):
        await client.send_rich("CHAT", "caption", file=str(tmp_path))
    assert runner.calls == []

@pytest.mark.asyncio
async def test_markdown_send_uses_rpc_formatting_ranges():
    rpc = RecordingRpcRunner([(
        0,
        json.dumps({"jsonrpc": "2.0", "id": "hermes-send", "result": {"guid": "OUT"}}),
        "",
    )])
    client = ImsgClient("imsg", rpc_runner=rpc)
    result = await client.send("16", "**bold**")
    assert result["guid"] == "OUT"
    assert rpc.calls[0]["method"] == "send"
    assert rpc.calls[0]["params"]["text"] == "bold"
    assert rpc.calls[0]["params"]["formatting"] == [
        {"start": 0, "length": 4, "styles": ["bold"]},
    ]
```

- [x] **Step 2: Run client tests and observe missing methods**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_client.py tests/test_native_actions.py -q`

Expected: failures naming the absent client methods.

- [x] **Step 3: Implement methods with capability checks and JSON confirmation**

```python
async def _require_action(self, action: str) -> ImsgCapabilities:
    caps = ImsgCapabilities.from_status(await self.status())
    caps.require_action(action)
    return caps

async def edit(self, chat_guid, message_id, text, *, backwards_compatibility_message=None, part_index=0):
    await self._require_action("edit")
    return await self._one(cli.build_edit_args(
        chat_guid, message_id, text,
        backwards_compatibility_message=backwards_compatibility_message,
        part_index=part_index,
    ))
```

Apply the same sequence to every native method. Reuse `validate_outbound_attachment_path` for all file-bearing actions. Replace poll-only capability code with the shared model.

`send` must call `extract_markdown_format_runs`. When ranges are present it
uses `build_send_rpc_request` and `_rpc_one`; when no range is present it keeps
the existing public CLI path. The exact target representation must survive
both paths.

- [x] **Step 4: Run client and poll tests**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_client.py tests/test_native_actions.py tests/test_polls.py -q`

Expected: all selected tests pass.

- [x] **Step 5: Review checkpoint**

Run: `git diff --check && git diff -- hermes_imsg_cli/client.py tests/test_client.py tests/test_native_actions.py`

Do not commit without explicit authorization.

### Task 4: Unified Hermes action tool and secure resolution

**Files:**
- Create: `hermes_imsg_cli/actions.py`
- Modify: `hermes_imsg_cli/adapter.py`
- Modify: `tests/test_plugin_registration.py`
- Create: `tests/test_actions.py`

**Interfaces:**
- Consumes: `ImsgClient`, exact action parameters, and current Hermes configuration.
- Produces: `IMSG_ACTION_TOOL_SCHEMA`, `handle_imsg_action_tool`, `handle_imsg_poll_tool`, and two registered Hermes tools.

- [x] **Step 1: Write failing registration and dispatch tests**

```python
def test_register_exposes_unified_action_and_legacy_poll_tools():
    ctx = FakeCtx()
    register(ctx)
    assert [tool["name"] for tool in ctx.registered_tools] == ["imsg_action", "imsg_poll"]
    action_enum = ctx.registered_tools[0]["schema"]["parameters"]["properties"]["action"]["enum"]
    assert action_enum == [
        "react", "edit", "unsend", "reply", "sendWithEffect", "upload-file",
        "renameGroup", "setGroupIcon", "addParticipant", "removeParticipant",
        "leaveGroup", "poll", "poll-vote", "poll-unvote",
    ]

@pytest.mark.asyncio
async def test_cross_chat_message_reference_is_rejected_before_edit(monkeypatch):
    client = FakeActionClient(chats=[{"id": 16, "guid": "CHAT-16"}], history=[])
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    result = await actions.handle_imsg_action_tool({
        "action": "edit", "chat_id": "16", "message_id": "OTHER", "text": "x",
    })
    assert result["success"] is False
    assert "current conversation" in result["error"]
    assert client.mutations == []
```

- [x] **Step 2: Run tests and confirm missing tool failures**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_actions.py tests/test_plugin_registration.py -q`

Expected: missing module and old one-tool registration failures.

- [x] **Step 3: Implement target and message reference resolution**

```python
@dataclass(frozen=True)
class ResolvedTarget:
    chat_id: str | None
    chat_guid: str
    chat_identifier: str | None = None

async def resolve_message_reference(client, target, message_id, *, require_from_me=False):
    messages = await client.history(target.chat_id, limit=1000, attachments=False)
    for message in messages:
        guid = str(message.get("guid") or message.get("message_guid") or "")
        if guid == message_id:
            if require_from_me and message.get("is_from_me") is not True:
                raise ValueError("message action requires a bot-authored message")
            return guid
    raise ValueError("message identifier does not belong to the current conversation")
```

Explicit `chat_guid` targets resolve their rowid through `client.chats(limit=1000)` before message mutation. Handle targets may synthesize a direct chat identifier only for send-like actions.

- [x] **Step 4: Implement action dispatch and legacy poll delegation**

Use an explicit branch for every action so each branch validates its own
required parameters before calling the client:

```python
if action == "react":
    result = await client.tapback(target.chat_guid, message_id, reaction,
                                  remove=remove, part_index=part_index)
elif action == "edit":
    result = await client.edit(target.chat_guid, message_id, text,
                               backwards_compatibility_message=bc_text,
                               part_index=part_index)
elif action == "unsend":
    result = await client.unsend(target.chat_guid, message_id, part_index=part_index)
elif action == "reply":
    result = await client.send_rich(target.chat_guid, text, reply_to=message_id,
                                    file=file_path, part_index=part_index)
elif action == "sendWithEffect":
    result = await client.send_rich(target.chat_guid, text, effect=effect_id,
                                    part_index=part_index)
elif action == "upload-file":
    result = await client.send_attachment(target.chat_guid, file_path, as_voice=as_voice)
elif action == "renameGroup":
    result = await client.rename_group(target.chat_guid, display_name)
elif action == "setGroupIcon":
    result = await client.set_group_icon(target.chat_guid, file_path)
elif action == "addParticipant":
    result = await client.add_participant(target.chat_guid, address)
elif action == "removeParticipant":
    result = await client.remove_participant(target.chat_guid, address)
elif action == "leaveGroup":
    result = await client.leave_group(target.chat_guid)
elif action == "poll":
    result = await client.send_poll(target.chat_id or "", question, options,
                                    chat_guid=target.chat_guid)
elif action == "poll-vote":
    result = await client.vote_poll(target.chat_id or "", poll_guid,
                                    option_id=option_id, option_index=option_index,
                                    option=option_text, chat_guid=target.chat_guid)
elif action == "poll-unvote":
    result = await client.unvote_poll(target.chat_id or "", poll_guid,
                                      option_id=option_id, option_index=option_index,
                                      option=option_text, chat_guid=target.chat_guid)
```

`edit` and `unsend` resolve with `require_from_me=True`. Group mutations
require an exact group chat target. Legacy poll arguments map to `poll`,
`poll-vote`, or `poll-unvote` and return the same structured result shape.

- [x] **Step 5: Run action and registration tests**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_actions.py tests/test_plugin_registration.py tests/test_polls.py -q`

Expected: all selected tests pass.

- [x] **Step 6: Review checkpoint**

Run: `git diff --check && git diff -- hermes_imsg_cli/actions.py hermes_imsg_cli/adapter.py tests/test_actions.py tests/test_plugin_registration.py tests/test_polls.py`

Do not commit without explicit authorization.

### Task 5: Live adapter parity for replies and inbound native events

**Files:**
- Modify: `hermes_imsg_cli/adapter.py`
- Modify: `hermes_imsg_cli/config.py`
- Modify: `hermes_imsg_cli/plugin.yaml`
- Modify: `tests/test_adapter.py`
- Modify: `tests/test_cli_parity.py`
- Modify: `tests/test_rules.py`

**Interfaces:**
- Consumes: trusted inbound `chat_id`, `chat_guid`, message GUID, reaction metadata, poll metadata, and existing rules.
- Produces: same-chat threaded replies, reaction notification policy, readable poll events, and preserved `raw_message` data.

- [x] **Step 1: Write failing threaded-reply safety tests**

```python
@pytest.mark.asyncio
async def test_send_uses_rich_reply_only_for_cached_same_chat_reference(adapter, monkeypatch):
    adapter._recent_chat_guids["16"] = "CHAT-16"
    adapter._recent_message_guids["16"] = "MSG-16"
    calls = []
    async def fake_run(args):
        calls.append(args)
        return 0, json.dumps({"guid": "OUT"}), ""
    monkeypatch.setattr(adapter, "_run_imsg", fake_run)
    result = await adapter.send("16", "reply", reply_to="MSG-16")
    assert result.success is True
    assert calls[-1][:5] == ["send-rich", "--chat", "CHAT-16", "--text", "reply"]

@pytest.mark.asyncio
async def test_send_ignores_cross_chat_reply_reference(adapter, monkeypatch):
    adapter._recent_chat_guids["16"] = "CHAT-16"
    adapter._recent_message_guids["16"] = "MSG-16"
    await adapter.send("16", "reply", reply_to="MSG-OTHER")
    assert calls[-1][:3] == ["send", "--chat-id", "16"]
```

- [x] **Step 2: Write failing inbound reaction and poll rendering tests**

```python
def test_reaction_notifications_own_accepts_bot_target(adapter):
    event = reaction_event(reacted_to_is_from_me=True, reaction="like")
    message = adapter._to_message_event(event)
    assert message.content == "Reaction: Like added to your message"

def test_native_poll_renders_numbered_options(adapter):
    message = adapter._to_message_event(poll_event("Dinner?", [("Pizza", 2), ("Sushi", 1)]))
    assert "Poll: Dinner?" in message.content
    assert "1. Pizza (2 votes)" in message.content
```

- [x] **Step 3: Run adapter tests and confirm behavior failures**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_adapter.py tests/test_cli_parity.py tests/test_rules.py -q`

Expected: rich-reply, reaction policy, and poll rendering assertions fail.

- [x] **Step 4: Implement trusted reply selection and native event normalization**

Turn on `reactions=True` in the supervised watch builder. Add
`reaction_notifications` parsing with `own`, `all`, and `off`, defaulting to
`own`. Normalize known reaction and poll fields through alias-aware helpers,
retain the original dictionary in `raw_message`, and apply existing rule gates
unchanged.

Use `build_rich_send_args` only when `reply_to` exactly matches the same-chat
cached message GUID and a same-chat GUID exists. All other sends use the
existing rowid path. Before either path, call `extract_markdown_format_runs`.
Pass its plain `text` to the command and its `ranges` to `send-rich --format`
or the rowid-bound `send` RPC payload. If the formatted RPC call fails, return
that failure without retrying a duplicate plain send.

Update `normalize_imsg_target` through a new typed `parse_imsg_target` helper so
cron and `send_message` preserve explicit `chat_guid:`, `chat_identifier:`,
`imessage:`, `sms:`, and `auto:` targets. Keep the existing two-value wrapper
contract for callers that pass only rowids or handles.

- [x] **Step 5: Run complete adapter-focused tests**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_adapter.py tests/test_cli_parity.py tests/test_rules.py tests/test_send_direct.py -q`

Expected: all selected tests pass.

- [x] **Step 6: Review checkpoint**

Run: `git diff --check && git diff -- hermes_imsg_cli/adapter.py hermes_imsg_cli/config.py hermes_imsg_cli/plugin.yaml tests/test_adapter.py tests/test_cli_parity.py tests/test_rules.py`

Do not commit without explicit authorization.

### Task 6: Supported-functionality documentation and GitHub Pages

**Files:**
- Rewrite: `docs/PARITY.md`
- Modify: `README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/VERIFICATION.md`
- Modify: `site/index.html`
- Modify: `site/script.js` only if interaction copy requires it
- Modify: `scripts/validate_site.py`
- Modify: `tests/test_site_validation.py`
- Modify: `hermes_imsg_cli/__init__.py`
- Modify: `pyproject.toml`
- Modify: `hermes_imsg_cli/plugin.yaml`

**Interfaces:**
- Consumes: functionality proven by Tasks 1 through 5.
- Produces: one consistent user-facing capability catalog in repository docs and the deployed Pages artifact.

- [x] **Step 1: Rewrite `docs/PARITY.md` as a functionality catalog**

Use these top-level sections:

```markdown
# Supported functionality

## Messaging and delivery
## Native message actions
## Polls
## Groups
## Inbound events and attachments
## Access policy and routing
## Recovery and reliability
## Capability requirements
## Verification status
```

Do not use OpenClaw as a comparison, status labels such as implemented or
excluded, or incompatibility tables. Describe only behavior that has direct
code and test evidence.

- [x] **Step 2: Update README and operational documentation**

Lead README with the full action surface, exact installation and bridge
requirements, `imsg_action` examples, capability behavior, safe target forms,
and the preserved rowid default. Remove claims that rich replies, edit,
unsend, effects, and group actions are intentionally unavailable.

- [x] **Step 3: Update GitHub Pages content and its semantic validator**

The site must show the verified action surface, both basic and advanced bridge
requirements, the supported target forms, and a link to `docs/PARITY.md` titled
"Supported functionality". Keep all existing accessibility, responsive,
offline, and workflow validation contracts.

- [x] **Step 4: Bump the release version and Python floor consistently**

Set `hermes_imsg_cli.__version__`, `pyproject.toml`, and `plugin.yaml` to the
same `2.0.0` value because the agent-facing tool contract and advertised
feature surface materially expand. Set `requires-python = ">=3.10"`, matching
the existing PEP 604 type-union syntax already present in production modules.

- [x] **Step 5: Run documentation and site verification**

Run: `$HOME/.hermes/hermes-agent/venv/bin/python3 -m pytest tests/test_site_validation.py tests/test_release_safety.py tests/test_privacy_scan.py -q`

Run: `python3 scripts/validate_site.py`

Run: `python3 scripts/privacy_scan.py`

Expected: every command exits 0.

- [x] **Step 6: Audit terminology and links**

Run: `rg -n --pcre2 "OpenClaw.*parity|Incompatible|Implementable|Excluded|Not used \(routing safety\)|\x{2014}" README.md docs site hermes_imsg_cli`

Expected: no comparison-ledger language in `docs/PARITY.md`, no stale unsupported-action claims, and no em dash characters in repository prose.

- [x] **Step 7: Review checkpoint**

Run: `git diff --check && git diff --stat && git status --short`

Do not commit without explicit authorization.

### Task 7: Full verification and completion audit

**Files:**
- Modify: `docs/VERIFICATION.md` with observed command output summaries and environment-gated checks.

**Interfaces:**
- Consumes: the complete worktree and the objective's deliverables.
- Produces: fresh test/build evidence and a requirement-by-requirement audit.

- [x] **Step 1: Run the complete repository validation**

Run: `scripts/validate.sh`

Expected: pytest, registration probe, requirements report, and script wrapper all exit 0. The registration probe must list `imsg_action, imsg_poll`.

- [x] **Step 2: Run static, privacy, site, and build checks**

```bash
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
$HOME/.hermes/hermes-agent/venv/bin/python3 -m compileall -q hermes_imsg_cli scripts tests
$HOME/.hermes/hermes-agent/venv/bin/python3 -m build
git diff --check
```

Expected: every command exits 0 with no compile or diff errors.

- [x] **Step 3: Run read-only live capability probes**

```bash
/opt/homebrew/bin/imsg --version
/opt/homebrew/bin/imsg status --json | jq '{advanced_features, v2_ready, bridge_version, rpc_methods, selectors, message}'
/opt/homebrew/bin/imsg rpc --help
```

Record only the sanitized capability fields. Do not invoke any mutation.

- [x] **Step 4: Audit every explicit objective requirement**

Check source and tests for each native action, both Hermes tool registrations,
existing platform behaviors, `docs/PARITY.md`, README, static Pages content,
version consistency, and verification evidence. Any missing or indirect item
returns to the owning task.

- [x] **Step 5: Inspect final worktree state**

Run: `git status --short && git diff --stat && git diff --name-only`

Confirm only scoped repository files changed. Leave staging, committing,
pushing, and Pages deployment for explicit authorization.
