"""Source-backed OpenClaw imsg CLI compatibility tests (no live Messages access)."""

import asyncio
import json
import os
import signal
import sys

import pytest

from hermes_imsg_cli import cli


def test_parse_ndjson_accepts_stream_array_and_blank_lines():
    stream = '\n'.join([json.dumps({"id": 1}), "", json.dumps({"id": 2})])
    assert cli.parse_json_output(stream) == [{"id": 1}, {"id": 2}]
    assert cli.parse_json_output(json.dumps([{"id": 3}])) == [{"id": 3}]


def test_parse_ndjson_reports_line_number_without_leaking_payload():
    with pytest.raises(cli.ImsgJSONError, match="line 2") as exc:
        cli.parse_json_output('{"id": 1}\nnot-json')
    assert "not-json" not in str(exc.value)


def test_build_chats_surface_matches_upstream():
    assert cli.build_chats_args(limit=5, unread_only=True) == [
        "chats", "--limit", "5", "--unread-only", "--json"
    ]


def test_build_group_surface_matches_upstream():
    assert cli.build_group_args("42") == ["group", "--chat-id", "42", "--json"]


def test_build_history_surface_matches_upstream():
    args = cli.build_history_args(
        "42", limit=10, attachments=True, convert_attachments=True,
        participants=["+111", "+222"], start="2026-01-01T00:00:00Z",
        end="2026-02-01T00:00:00Z",
    )
    assert args == [
        "history", "--chat-id", "42", "--limit", "10", "--attachments",
        "--convert-attachments", "--participants", "+111,+222", "--start",
        "2026-01-01T00:00:00Z", "--end", "2026-02-01T00:00:00Z", "--json",
    ]


def test_build_search_validates_match_mode():
    assert cli.build_search_args("pizza", match="exact", limit=7) == [
        "search", "--query", "pizza", "--match", "exact", "--limit", "7", "--json"
    ]
    with pytest.raises(ValueError, match="contains or exact"):
        cli.build_search_args("pizza", match="regex")


def test_build_watch_exposes_filters_reactions_and_resume_cursor():
    args = cli.build_watch_args(
        "500ms", 91, chat_id="42", reactions=True, participants=["+111"],
        start="2026-01-01T00:00:00Z", end="2026-02-01T00:00:00Z",
    )
    assert args == [
        "watch", "--json", "--attachments", "--convert-attachments",
        "--reactions", "--chat-id", "42", "--participants", "+111",
        "--start", "2026-01-01T00:00:00Z", "--end", "2026-02-01T00:00:00Z",
        "--debounce", "500ms", "--since-rowid", "91",
    ]


def test_build_send_supports_public_transport_options_and_one_file():
    args = cli.build_plain_send_args(
        "+12025550100", "hi", is_phone=True,
        media_files=[("/tmp/a.jpg", False)], service="imessage",
        region="CA", no_sms_fallback=True,
    )
    assert args == [
        "send", "--to", "+12025550100", "--text", "hi", "--file", "/tmp/a.jpg",
        "--service", "imessage", "--region", "CA", "--no-sms-fallback", "--json",
    ]
    with pytest.raises(ValueError, match="one attachment"):
        cli.build_plain_send_args(
            "42", "x", media_files=[("/tmp/a", False), ("/tmp/b", False)]
        )


def test_build_read_and_typing_use_supported_chat_target_syntax():
    assert cli.build_read_args("42") == ["read", "--chat-id", "42", "--json"]
    assert cli.build_typing_args("42", 5) == [
        "typing", "--chat-id", "42", "--duration", "5s", "--json"
    ]
    assert cli.build_typing_args("42", stop=True) == [
        "typing", "--chat-id", "42", "--stop", "true", "--json"
    ]


def test_standard_reaction_validation_is_fail_closed():
    assert cli.build_react_args("42", "like") == [
        "react", "--chat-id", "42", "--reaction", "like", "--json"
    ]
    with pytest.raises(ValueError, match="standard reaction"):
        cli.build_react_args("42", "🔥")


def test_attachment_path_prefers_converted_variant_and_skips_missing():
    # `_attachment_path` is pure but lives in adapter.py (which imports
    # `gateway`); skip gracefully rather than fail when Hermes core isn't on
    # PYTHONPATH, keeping the rest of this file's pure/portable tests
    # runnable standalone.
    pytest.importorskip("gateway", reason="Hermes core not on PYTHONPATH")
    from hermes_imsg_cli.adapter import _attachment_path

    assert _attachment_path({
        "path": "/original/audio.caf", "converted_path": "/cache/audio.m4a",
        "missing": False,
    }) == "/cache/audio.m4a"
    assert _attachment_path({"path": "/gone/file.jpg", "missing": True}) is None


# ---------------------------------------------------------------------------
# chat-id target syntax/range validation (reply-routing safety)
# ---------------------------------------------------------------------------

def test_build_plain_send_rejects_non_numeric_chat_id():
    with pytest.raises(ValueError, match="positive integer rowid"):
        cli.build_plain_send_args("not-a-rowid", "hi")


def test_build_plain_send_rejects_zero_or_negative_chat_id():
    with pytest.raises(ValueError, match="positive integer"):
        cli.build_plain_send_args("0", "hi")


def test_build_plain_send_rejects_oversized_chat_id():
    # 20 digits exceeds a signed 64-bit rowid; never shell out with it.
    with pytest.raises(ValueError, match="exceeds the maximum rowid length"):
        cli.build_plain_send_args("1" * 20, "hi")


def test_build_plain_send_accepts_max_length_rowid():
    # 19 digits is within a signed 64-bit rowid; must not be rejected.
    args = cli.build_plain_send_args("9" * 19, "hi")
    assert args[:3] == ["send", "--chat-id", "9" * 19]


def test_build_plain_send_rejects_whitespace_only_chat_id():
    with pytest.raises(ValueError, match="positive integer rowid"):
        cli.build_plain_send_args("   ", "hi")


# ---------------------------------------------------------------------------
# Bounded async subprocess cleanup: terminate, then kill after a grace period
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_imsg_terminates_then_kills_a_sigterm_ignoring_child(monkeypatch):
    import asyncio
    import sys
    import time

    monkeypatch.setattr(cli, "DEFAULT_TERMINATE_GRACE", 0.2)
    start = time.monotonic()
    task = asyncio.ensure_future(cli.run_imsg(
        sys.executable,
        ["-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"],
    ))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - start
    # Bounded by the (shrunk) grace period, not the child's 30s sleep.
    assert elapsed < 5.0, f"terminate-then-kill did not bound cleanup ({elapsed:.1f}s)"


@pytest.mark.asyncio
async def test_run_imsg_terminates_cleanly_within_grace_when_child_cooperates(monkeypatch):
    import asyncio
    import sys
    import time

    monkeypatch.setattr(cli, "DEFAULT_TERMINATE_GRACE", 2.0)
    start = time.monotonic()
    task = asyncio.ensure_future(cli.run_imsg(sys.executable, ["-c", "import time; time.sleep(30)"]))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - start
    # A cooperative child (default SIGTERM handling exits immediately) should
    # not need to wait out the full grace period before cleanup returns.
    assert elapsed < 2.0, f"cooperative child cleanup took too long ({elapsed:.1f}s)"


@pytest.mark.asyncio
async def test_run_imsg_rpc_terminates_then_kills_a_sigterm_ignoring_child(monkeypatch):
    import asyncio
    import sys
    import time

    monkeypatch.setattr(cli, "DEFAULT_TERMINATE_GRACE", 0.2)
    # `run_imsg_rpc` invokes ``binary rpc --json``; swap in a script that
    # behaves like a hung, SIGTERM-ignoring `imsg rpc` process instead of
    # trying to exec python as a literal "rpc" subcommand. Capture the real
    # create_subprocess_exec first so the fake doesn't recurse into itself.
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    async def fake_create_subprocess_exec(*args, **kwargs):
        forwarded = {
            key: kwargs[key]
            for key in ("stdin", "stdout", "stderr", "start_new_session")
            if key in kwargs
        }
        return await real_create_subprocess_exec(
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
            **forwarded,
        )

    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    start = time.monotonic()
    task = asyncio.ensure_future(cli.run_imsg_rpc(
        "imsg", {"jsonrpc": "2.0", "id": 1, "method": "noop", "params": {}},
    ))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"terminate-then-kill did not bound rpc cleanup ({elapsed:.1f}s)"


def _pid_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_path(path, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not path.exists() and loop.time() < deadline:
        await asyncio.sleep(0.01)
    return path.exists()


async def _wait_for_pid_exit(pid, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while _pid_exists(pid) and loop.time() < deadline:
        await asyncio.sleep(0.01)
    return not _pid_exists(pid)


def _parent_with_sigterm_ignoring_grandchild(pid_path):
    grandchild = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "time.sleep(30)"
    )
    parent = (
        "import pathlib,signal,subprocess,sys,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        f"child=subprocess.Popen([sys.executable,'-c',{grandchild!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid));"
        "time.sleep(30)"
    )
    return parent, str(pid_path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups are required")
@pytest.mark.parametrize("runner", ["cli", "rpc"])
@pytest.mark.asyncio
async def test_cancelled_runner_removes_sigterm_ignoring_grandchild(
    monkeypatch, tmp_path, runner
):
    """Cancellation must not leave an osascript-like grandchild running."""
    monkeypatch.setattr(cli, "DEFAULT_TERMINATE_GRACE", 0.2)
    pid_path = tmp_path / f"{runner}-grandchild.pid"
    parent_script, pid_arg = _parent_with_sigterm_ignoring_grandchild(pid_path)
    real_create_subprocess_exec = asyncio.create_subprocess_exec

    if runner == "rpc":
        async def fake_create_subprocess_exec(*args, **kwargs):
            forwarded = {
                key: kwargs[key]
                for key in ("stdin", "stdout", "stderr", "start_new_session")
                if key in kwargs
            }
            return await real_create_subprocess_exec(
                sys.executable, "-c", parent_script, pid_arg, **forwarded
            )

        monkeypatch.setattr(
            cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )
        task = asyncio.create_task(cli.run_imsg_rpc(
            "imsg", {"jsonrpc": "2.0", "id": 1, "method": "noop", "params": {}}
        ))
    else:
        task = asyncio.create_task(cli.run_imsg(
            sys.executable, ["-c", parent_script, pid_arg]
        ))

    grandchild_pid = None
    try:
        assert await _wait_for_path(pid_path), "parent did not report its grandchild pid"
        grandchild_pid = int(pid_path.read_text())
        assert _pid_exists(grandchild_pid)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await _wait_for_pid_exit(grandchild_pid), (
            f"{runner} cancellation left grandchild pid {grandchild_pid} running"
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if grandchild_pid is not None and _pid_exists(grandchild_pid):
            os.kill(grandchild_pid, signal.SIGKILL)
            await _wait_for_pid_exit(grandchild_pid)


@pytest.mark.asyncio
async def test_non_posix_runners_do_not_request_a_new_session(monkeypatch):
    class SpawnStopped(Exception):
        pass

    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(kwargs)
        raise SpawnStopped

    monkeypatch.setattr(cli, "_POSIX_PROCESS_GROUPS", False)
    monkeypatch.setattr(cli.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(SpawnStopped):
        await cli.run_imsg("imsg", ["status", "--json"])
    with pytest.raises(SpawnStopped):
        await cli.run_imsg_rpc(
            "imsg", {"jsonrpc": "2.0", "id": 1, "method": "noop", "params": {}}
        )

    assert len(calls) == 2
    assert all("start_new_session" not in kwargs for kwargs in calls)


@pytest.mark.asyncio
async def test_non_posix_cleanup_terminates_then_kills_direct_process(monkeypatch):
    class FakeProcess:
        returncode = None

        def __init__(self):
            self.events = []

        def terminate(self):
            self.events.append("terminate")

        def kill(self):
            self.events.append("kill")
            self.returncode = -999

        async def wait(self):
            if self.returncode is None:
                await asyncio.Event().wait()
            return self.returncode

    monkeypatch.setattr(cli, "_POSIX_PROCESS_GROUPS", False)
    process = FakeProcess()
    await cli._terminate_then_kill(process, grace=0.01)
    assert process.events == ["terminate", "kill"]
