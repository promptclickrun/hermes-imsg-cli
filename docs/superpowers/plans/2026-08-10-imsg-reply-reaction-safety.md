# iMessage Reply and Tapback Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop implicit native reply threading, emit inbound quote context only for proven Apple thread origins, and prevent Tapbacks from starting agent turns by default.

**Architecture:** Keep the existing exact-rowid platform adapter and explicit native action tool. Separate Hermes response correlation from explicit native reply actions, gate inbound reply context on `thread_originator_guid`, and suppress default reaction delivery both at watch subscription and before the adapter pipeline. Preserve the existing `own` and `all` modes as deliberate operator opt-ins.

**Tech Stack:** Python 3.10+, asyncio, pytest, Hermes platform plugin API, `imsg` NDJSON and JSON-RPC, YAML plugin metadata, Markdown documentation.

## Global Constraints

- Release version is `2.0.1` in `pyproject.toml`, `hermes_imsg_cli/__init__.py`, and `hermes_imsg_cli/plugin.yaml`.
- Ordinary responses always preserve the trusted positive Messages chat rowid.
- Framework `reply_to` correlation never requests native Apple threading.
- Only explicit `imsg_action` `reply` requests native threading.
- `thread_originator_guid` is the only inbound proof of a native threaded reply.
- Reaction mode defaults and invalid values fail to `off`.
- Under `off`, reaction rows cannot reach authorization, history lookup, read receipts, or agent dispatch.
- Existing `own` and `all` modes remain explicit opt-ins.
- Tests must fake every mutation boundary; verification must not send or modify a real Messages conversation.
- Do not change unrelated Hermes configuration, allowlists, automation definitions, or routing.

---

### Task 1: Trust only native thread-origin metadata inbound

**Files:**
- Modify: `tests/test_adapter.py`
- Modify: `hermes_imsg_cli/adapter.py:1129-1229`

**Interfaces:**
- Consumes: raw `imsg` event keys `thread_originator_guid`, `threadOriginatorGuid`, `reply_to_text`, `replyToText`, `reply_to_sender`, and `replyToSender`.
- Produces: `MessageEvent.reply_to_message_id`, `reply_to_text`, and `reply_to_author_id`, all `None` unless a nonempty native thread origin is present on a normal message.

- [ ] **Step 1: Write failing event-conversion tests**

Add tests that exercise `ImsgAdapter._to_message_event` directly:

```python
def test_plain_event_ignores_unreliable_reply_to_fields(tmp_path):
    adapter = make_adapter(tmp_path)
    message = adapter._to_message_event({
        "id": 104,
        "chat_id": 3,
        "chat_guid": "iMessage;-;+12025550101",
        "guid": "CURRENT",
        "sender": "+12025550101",
        "text": "ordinary message",
        "reply_to_guid": "MOST-RECENT",
        "reply_to_text": "not an actual quoted parent",
        "reply_to_sender": "+12025550101",
    })
    assert message is not None
    assert message.reply_to_message_id is None
    assert message.reply_to_text is None
    assert message.reply_to_author_id is None


def test_thread_originator_exposes_real_reply_context(tmp_path):
    adapter = make_adapter(tmp_path)
    message = adapter._to_message_event({
        "id": 105,
        "chat_id": 3,
        "chat_guid": "iMessage;-;+12025550101",
        "guid": "CURRENT",
        "sender": "+12025550101",
        "text": "that older one",
        "reply_to_guid": "UNRELIABLE",
        "thread_originator_guid": "ACTUAL-PARENT",
        "reply_to_text": "the message from six turns ago",
        "reply_to_sender": "+12025550199",
    })
    assert message is not None
    assert message.reply_to_message_id == "ACTUAL-PARENT"
    assert message.reply_to_text == "the message from six turns ago"
    assert message.reply_to_author_id == "+12025550199"
```

Add a camel-case fixture and a reaction fixture proving that association fields do not create reply context for reaction rows.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
HERMES_SRC="$HOME/.hermes/hermes-agent" \
PYTHONPATH="$HOME/.hermes/hermes-agent:$PWD" \
"$HOME/.hermes/hermes-agent/venv/bin/python3" -m pytest -q \
  tests/test_adapter.py::test_plain_event_ignores_unreliable_reply_to_fields \
  tests/test_adapter.py::test_thread_originator_exposes_real_reply_context
```

Expected: the plain event test fails because `reply_to_guid` is copied, and the native thread test fails because `thread_originator_guid` is ignored.

- [ ] **Step 3: Implement the minimal inbound gate**

In `_to_message_event`, normalize the thread origin without falling back to `reply_to_guid`:

```python
thread_originator = _event_get(
    event, "thread_originator_guid", "threadOriginatorGuid"
)
thread_originator = (
    thread_originator.strip() if isinstance(thread_originator, str) else ""
)
has_reply_context = bool(thread_originator) and reaction is None and poll_kind is None
```

Populate the three reply fields only when `has_reply_context`; normalize non-string or empty text and sender values to `None`.

- [ ] **Step 4: Run the focused tests and the adapter file**

Run the new tests, then:

```bash
HERMES_SRC="$HOME/.hermes/hermes-agent" \
PYTHONPATH="$HOME/.hermes/hermes-agent:$PWD" \
"$HOME/.hermes/hermes-agent/venv/bin/python3" -m pytest -q tests/test_adapter.py
```

Expected: all selected tests pass with no real command invocation.

- [ ] **Step 5: Commit the inbound context fix**

```bash
git add hermes_imsg_cli/adapter.py tests/test_adapter.py
git commit -m "fix(imsg): trust native thread origins for reply context"
```

---

### Task 2: Keep ordinary outbound responses unthreaded

**Files:**
- Modify: `tests/test_adapter.py`
- Modify: `hermes_imsg_cli/adapter.py:1389-1579`

**Interfaces:**
- Consumes: Hermes platform calls to `send`, `send_document`, `send_image_file`, `send_video`, and `send_voice`, including their existing `reply_to` correlation argument.
- Produces: ordinary exact-rowid sends through `_send_plain`; explicit `handle_imsg_action_tool` `reply` continues to call `ImsgClient.send_rich(..., reply_to=message_id)`.

- [ ] **Step 1: Replace the old automatic-thread expectation with a failing regression**

Create a test with a trusted cached message and both bridge methods advertised. Capture JSON-RPC requests and assert the platform response uses `send`, `chat_id=16`, and no `reply_to` parameter even when `reply_to="MSG-16"` is supplied.

```python
assert rpc_calls == [{
    "jsonrpc": "2.0",
    "id": rpc_calls[0]["id"],
    "method": "send",
    "params": {
        "chat_id": 16,
        "text": "reply",
        "transport": "bridge",
    },
}]
```

Add document and voice tests with regular temporary files. Assert each uses the plain file-send route and never emits `send-rich` or `--reply-to`. Keep the existing action-tool test that proves explicit native `reply` still passes the selected message GUID.

- [ ] **Step 2: Run the new tests and verify RED**

Run the three platform-response tests only. Expected: current code selects `_send_rich_reply` for the cached `reply_to`, so each test fails on the observed command or RPC method.

- [ ] **Step 3: Remove implicit threading from platform methods**

Change the first chunk of `send` to use `_send_plain` without consulting `_trusted_reply_target`. Make `send_document` and `send_voice` use `_send_plain` regardless of the platform `reply_to` argument. Leave image and video delegation intact, and do not modify `actions.py` or explicit poll reply behavior.

Remove `_trusted_reply_target` only if no production caller remains after the platform changes. Preserve recent message and chat caches because standard reactions, typing, and target tracking still use them.

- [ ] **Step 4: Run outbound and explicit-action tests GREEN**

Run the new adapter tests plus:

```bash
HERMES_SRC="$HOME/.hermes/hermes-agent" \
PYTHONPATH="$HOME/.hermes/hermes-agent:$PWD" \
"$HOME/.hermes/hermes-agent/venv/bin/python3" -m pytest -q \
  tests/test_adapter.py tests/test_actions.py tests/test_send_direct.py
```

Expected: platform responses are unthreaded and the explicit native reply action remains green.

- [ ] **Step 5: Commit the outbound separation**

```bash
git add hermes_imsg_cli/adapter.py tests/test_adapter.py
git commit -m "fix(imsg): keep ordinary responses unthreaded"
```

---

### Task 3: Make Tapbacks non-triggering by default

**Files:**
- Modify: `tests/test_adapter.py`
- Modify: `hermes_imsg_cli/adapter.py:426-435, 997-1004, 1102-1119`

**Interfaces:**
- Consumes: `reaction_notifications` with supported values `off`, `own`, and `all`.
- Produces: default and invalid values normalize to `off`; default watch commands omit reaction subscription; defense-in-depth event handling returns before any agent-facing or side-effecting path.

- [ ] **Step 1: Write failing default, watch, and early-drop tests**

Update the existing mode tests so `own` is explicit. Add:

```python
def test_reaction_notifications_default_and_invalid_fail_off(monkeypatch, tmp_path):
    monkeypatch.delenv("IMSG_REACTION_NOTIFICATIONS", raising=False)
    assert make_adapter(tmp_path).reaction_notifications == "off"
    monkeypatch.setenv("IMSG_REACTION_NOTIFICATIONS", "invalid")
    assert make_adapter(tmp_path).reaction_notifications == "off"


def test_default_watch_args_omit_reaction_rows(monkeypatch, tmp_path):
    monkeypatch.delenv("IMSG_REACTION_NOTIFICATIONS", raising=False)
    assert "--reactions" not in make_adapter(tmp_path)._build_watch_args()
    assert "--reactions" in make_adapter(
        tmp_path, reaction_notifications="own"
    )._build_watch_args()
```

Add an async test that supplies a reaction event under `off`, replaces `_should_process_async`, `_remember_reaction_target_ownership`, `mark_read`, and `handle_message` with functions that raise `AssertionError`, then verifies the cursor advanced without any forbidden function running.

- [ ] **Step 2: Run the new tests and verify RED**

Expected failures: current default is `own`, invalid values become `own`, watch always includes `--reactions`, and `_process_watch_event` enters authorization before dropping the event.

- [ ] **Step 3: Implement both suppression boundaries**

Set the constructor default and invalid fallback to `off`. Build watch arguments with:

```python
reactions=self.reaction_notifications != "off"
```

In `_process_watch_event`, preserve durable rowid advancement, then return immediately when the event is a reaction and the mode is `off`, before `_remember_event_ids` and `_should_process_async`.

- [ ] **Step 4: Run reaction tests and full adapter tests GREEN**

Run all reaction tests, the watch-argument tests, the early-drop test, and then the complete `tests/test_adapter.py`. Confirm explicit `own` and `all` behavior still passes.

- [ ] **Step 5: Commit the reaction security boundary**

```bash
git add hermes_imsg_cli/adapter.py tests/test_adapter.py
git commit -m "fix(imsg): silence Tapbacks by default"
```

---

### Task 4: Align the 2.0.1 release contract and operator guidance

**Files:**
- Modify: `pyproject.toml`
- Modify: `hermes_imsg_cli/__init__.py`
- Modify: `hermes_imsg_cli/plugin.yaml`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/MIGRATION.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/PARITY.md`
- Modify: `site/index.html`
- Modify: `skills/apple/hermes-imsg-plugin/references/calling-surfaces-and-targets.md`
- Modify: `skills/apple/hermes-imsg-plugin/references/capability-gates-and-config.md`
- Test: `tests/test_release_safety.py`
- Test: `tests/test_skill_bundle.py`

**Interfaces:**
- Consumes: approved safety design and production behavior from Tasks 1 through 3.
- Produces: synchronized package/manifest version `2.0.1`, default-off examples, explicit-reply guidance, and a warning that reaction opt-in can create agent turns.

- [ ] **Step 1: Move the runtime version first and observe the consistency guard fail**

Change `hermes_imsg_cli.__version__` to `2.0.1`, then run `tests/test_release_safety.py::test_runtime_version_matches_release_manifests`.

Expected: FAIL because `pyproject.toml` and `plugin.yaml` still report `2.0.0`.

- [ ] **Step 2: Align all release metadata**

Set `pyproject.toml` and `plugin.yaml` to `2.0.1`. Change the manifest reaction description to state `default: off`.

- [ ] **Step 3: Update public and operator documentation**

Document these exact behaviors consistently:

- normal Hermes responses ignore framework `reply_to` for native threading;
- `imsg_action` `reply` is the explicit native thread surface;
- inbound context requires `thread_originator_guid`;
- Tapbacks are absent from the default watch and cannot start an agent turn;
- `own` and `all` are explicit security-sensitive opt-ins;
- examples use `reaction_notifications: off`.

Do not rewrite the historical 2.0.0 plan or specification. Update the current website and bundled skill references because operators use them as live guidance.

- [ ] **Step 4: Run release, skill, site, and privacy checks**

```bash
HERMES_SRC="$HOME/.hermes/hermes-agent" \
PYTHONPATH="$HOME/.hermes/hermes-agent:$PWD" \
"$HOME/.hermes/hermes-agent/venv/bin/python3" -m pytest -q \
  tests/test_release_safety.py tests/test_skill_bundle.py
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
git diff --check
```

Expected: all commands exit zero.

- [ ] **Step 5: Commit the release contract**

```bash
git add pyproject.toml hermes_imsg_cli/__init__.py hermes_imsg_cli/plugin.yaml \
  README.md SECURITY.md docs/ARCHITECTURE.md docs/MIGRATION.md \
  docs/OPERATIONS.md docs/PARITY.md site/index.html \
  skills/apple/hermes-imsg-plugin/references/calling-surfaces-and-targets.md \
  skills/apple/hermes-imsg-plugin/references/capability-gates-and-config.md
git commit -m "docs(imsg): publish reply and Tapback safety contract"
```

---

### Task 5: Verify, integrate, push, and deploy

**Files:**
- Modify: `docs/VERIFICATION.md`
- Runtime update: `/Users/gordieai/.hermes/config.yaml`
- Runtime link: `/Users/gordieai/.hermes/plugins/imsg-platform`

**Interfaces:**
- Consumes: complete feature branch, clean `main`, existing `ai.hermes.gateway*` launchd services, and the personal Hermes plugin configuration.
- Produces: green merged `main`, matching remote SHA, installed plugin `2.0.1`, explicit local reaction mode `off`, restarted relevant gateway instance, and non-mutating health evidence.

- [ ] **Step 1: Run the complete verification gate**

```bash
scripts/validate.sh
python3 scripts/validate_site.py
python3 scripts/privacy_scan.py
python3 -m compileall -q hermes_imsg_cli scripts tests
git diff --check
pipx run build
```

Read every command's output and exit status. Do not proceed on any failure.

- [ ] **Step 2: Record observed 2.0.1 evidence**

Update `docs/VERIFICATION.md` with the exact pytest count, warning count, built filenames, registration probe, site result, privacy result, and the statement that no live Messages mutation occurred. Run the full gate again after editing the report.

- [ ] **Step 3: Commit the verification report**

```bash
git add docs/VERIFICATION.md
git commit -m "docs(imsg): record 2.0.1 verification"
```

- [ ] **Step 4: Integrate into local main and retest the merged tree**

Fetch `origin`, confirm the feature branch forked from local `main`, and confirm the main checkout contains no unrelated changes. Fast-forward local `main` to the feature branch, then rerun `scripts/validate.sh`, site validation, privacy scan, compilation, and `git diff --check` from the main checkout.

- [ ] **Step 5: Push and verify remote main**

Push without force. Fetch `origin/main` and verify `git rev-parse HEAD` equals `git rev-parse origin/main`.

- [ ] **Step 6: Back up and migrate only the reaction setting**

Inspect the effective iMessage platform configuration and any `IMSG_REACTION_NOTIFICATIONS` override without printing unrelated values. Create a timestamped backup of `/Users/gordieai/.hermes/config.yaml`, then set the iMessage platform extra value to `reaction_notifications: off` while preserving every other YAML node and comment. Abort if an environment override would re-enable reactions.

- [ ] **Step 7: Install from stable main and restart the relevant gateway**

Run `scripts/install-local.sh` from `/Users/gordieai/hermes-imsg-cli`. Confirm the symlink resolves to `/Users/gordieai/hermes-imsg-cli/hermes_imsg_cli` and the manifest reports `2.0.1`.

Use Hermes' supported `gateway restart` command for the active profile that loads the personal iMessage platform. Do not restart unrelated dashboard or gateway profiles. Preserve in-flight session recovery behavior provided by Hermes.

- [ ] **Step 8: Verify running-instance health without a live message mutation**

Confirm the restarted launchd service has a live PID and zero status, plugin discovery reports `imsg-platform` enabled with no error, the platform registry contains `imsg`, the effective reaction mode is `off`, and recent gateway logs contain no new plugin startup error. Confirm existing enabled plugins and automation configuration were not changed by comparing the sanitized pre/post configuration shape.

- [ ] **Step 9: Clean up the owned feature worktree**

After remote and runtime verification, ensure the installed plugin link points to main, remove only the task-owned `.worktrees/imsg-reply-reaction-safety` worktree, prune worktree metadata, and delete the merged local feature branch. Leave all other registered worktrees and branches untouched.
