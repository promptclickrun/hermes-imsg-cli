"""Regression tests for cross-process serialization of Messages mutations."""

import asyncio
import subprocess
import sys
import time

import pytest

from hermes_imsg_cli import cli


class _FakeProcess:
    def __init__(self, tracker):
        self._tracker = tracker
        self.returncode = 0

    async def communicate(self):
        self._tracker["active"] += 1
        self._tracker["max_active"] = max(
            self._tracker["max_active"], self._tracker["active"]
        )
        await asyncio.sleep(0.05)
        self._tracker["active"] -= 1
        return b'{"status":"sent"}\n', b""

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_mutating_imsg_commands_are_serialized(monkeypatch, tmp_path):
    tracker = {"active": 0, "max_active": 0}

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess(tracker)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(cli, "MUTATION_LOCK_PATH", tmp_path / "imsg-mutation.lock", raising=False)

    await asyncio.gather(
        cli.run_imsg("imsg", ["send", "--chat-id", "3", "--text", "one", "--json"]),
        cli.run_imsg("imsg", ["launch", "--json"]),
    )

    assert tracker["max_active"] == 1


@pytest.mark.asyncio
async def test_read_only_imsg_commands_remain_concurrent(monkeypatch, tmp_path):
    tracker = {"active": 0, "max_active": 0}

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess(tracker)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(cli, "MUTATION_LOCK_PATH", tmp_path / "imsg-mutation.lock", raising=False)

    await asyncio.gather(
        cli.run_imsg("imsg", ["history", "--chat-id", "3", "--json"]),
        cli.run_imsg("imsg", ["chats", "--json"]),
    )

    assert tracker["max_active"] == 2


@pytest.mark.asyncio
async def test_recovery_transaction_blocks_send_during_bridge_settle(monkeypatch, tmp_path):
    calls = []

    class RecordingProcess(_FakeProcess):
        def __init__(self, tracker, command):
            super().__init__(tracker)
            self.command = command

        async def communicate(self):
            calls.append(self.command)
            return await super().communicate()

    tracker = {"active": 0, "max_active": 0}

    async def fake_create_subprocess_exec(_binary, *args, **_kwargs):
        return RecordingProcess(tracker, args[0])

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(cli, "MUTATION_LOCK_PATH", tmp_path / "imsg-mutation.lock", raising=False)

    async def recovery_sequence():
        async with cli.mutation_transaction() as mutation_token:
            await cli.run_imsg(
                "imsg", ["launch", "--json"], mutation_token=mutation_token
            )
            await asyncio.sleep(0.05)
            await cli.run_imsg(
                "imsg",
                ["send", "--chat-id", "3", "--text", "retry", "--json"],
                mutation_token=mutation_token,
            )

    async def competing_send():
        await asyncio.sleep(0.01)
        await cli.run_imsg("imsg", ["send", "--chat-id", "3", "--text", "other", "--json"])

    await asyncio.gather(recovery_sequence(), competing_send())

    assert calls == ["launch", "send", "send"]


@pytest.mark.asyncio
async def test_explicit_transaction_token_reenters_through_wait_for(monkeypatch, tmp_path):
    tracker = {"active": 0, "max_active": 0}

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return _FakeProcess(tracker)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(cli, "MUTATION_LOCK_PATH", tmp_path / "imsg-mutation.lock")

    async with cli.mutation_transaction() as token:
        code, _stdout, _stderr = await asyncio.wait_for(
            cli.run_imsg("imsg", ["launch", "--json"], mutation_token=token),
            timeout=0.5,
        )

    assert code == 0
    assert tracker["max_active"] == 1


@pytest.mark.asyncio
async def test_child_task_cannot_inherit_and_bypass_transaction_lock(monkeypatch, tmp_path):
    calls = []

    async def fake_create_subprocess_exec(_binary, *args, **_kwargs):
        calls.append(args[0])
        return _FakeProcess({"active": 0, "max_active": 0})

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(cli, "MUTATION_LOCK_PATH", tmp_path / "imsg-mutation.lock")

    async with cli.mutation_transaction():
        child = asyncio.create_task(
            cli.run_imsg("imsg", ["send", "--chat-id", "3", "--text", "child", "--json"])
        )
        await asyncio.sleep(0.03)
        assert calls == []

    await child
    assert calls == ["send"]


@pytest.mark.asyncio
async def test_stale_copied_token_cannot_bypass_a_later_transaction(monkeypatch, tmp_path):
    calls = []
    release_child = asyncio.Event()

    async def fake_create_subprocess_exec(_binary, *args, **_kwargs):
        calls.append(args[0])
        return _FakeProcess({"active": 0, "max_active": 0})

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(cli, "MUTATION_LOCK_PATH", tmp_path / "imsg-mutation.lock")

    async with cli.mutation_transaction() as stale_token:
        async def delayed_stale_call():
            await release_child.wait()
            return await cli.run_imsg(
                "imsg",
                ["send", "--chat-id", "3", "--text", "stale", "--json"],
                mutation_token=stale_token,
            )

        child = asyncio.create_task(delayed_stale_call())

    async with cli.mutation_transaction():
        release_child.set()
        await asyncio.sleep(0.03)
        assert calls == []

    await child
    assert calls == ["send"]


def test_sync_mutation_lock_has_bounded_wait(monkeypatch, tmp_path):
    lock_path = tmp_path / "imsg-mutation.lock"
    holder_code = (
        "import fcntl,sys,time; "
        "h=open(sys.argv[1],'a+'); "
        "fcntl.flock(h.fileno(),fcntl.LOCK_EX); "
        "print('ready',flush=True); time.sleep(10)"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(lock_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ready"
        monkeypatch.setattr(cli, "MUTATION_LOCK_PATH", lock_path)
        monkeypatch.setattr(cli, "MUTATION_LOCK_TIMEOUT", 0.1)
        started = time.monotonic()
        code, _stdout, stderr = cli.run_imsg_sync(
            "imsg", ["send", "--chat-id", "3", "--text", "blocked", "--json"]
        )
        assert time.monotonic() - started < 1
        assert code == 1
        assert "mutation lock" in stderr
    finally:
        holder.terminate()
        holder.wait(timeout=5)
