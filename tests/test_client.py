"""Hermes-native async client facade over the upstream imsg CLI."""

import asyncio
import json

import pytest

from hermes_imsg_cli.client import ImsgClient, ImsgCommandError


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, binary, args):
        self.calls.append((binary, args))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_client_lists_ndjson_chats_and_preserves_schema():
    runner = FakeRunner([(0, '{"id":1,"unread_count":2}\n{"id":2}\n', "")])
    client = ImsgClient("/opt/imsg", runner=runner)
    result = await client.chats(limit=2, unread_only=True)
    assert result == [{"id": 1, "unread_count": 2}, {"id": 2}]
    assert runner.calls[0][1] == ["chats", "--limit", "2", "--unread-only", "--json"]


@pytest.mark.asyncio
async def test_client_group_requires_one_json_object():
    client = ImsgClient("imsg", runner=FakeRunner([(0, "", "")]))
    with pytest.raises(ImsgCommandError, match="no JSON object"):
        await client.group("42")


@pytest.mark.asyncio
async def test_client_history_and_search_use_source_backed_builders():
    runner = FakeRunner([
        (0, '{"id":9,"chat_id":42}\n', ""),
        (0, '{"id":10,"text":"pizza"}\n', ""),
    ])
    client = ImsgClient("imsg", runner=runner)
    assert (await client.history("42", limit=5))[0]["id"] == 9
    assert (await client.search("pizza", match="contains", limit=1))[0]["id"] == 10
    assert runner.calls[0][1][:5] == ["history", "--chat-id", "42", "--limit", "5"]
    assert runner.calls[1][1][:5] == ["search", "--query", "pizza", "--match", "contains"]


@pytest.mark.asyncio
async def test_client_send_returns_first_ndjson_object():
    runner = FakeRunner([(0, json.dumps({"status": "sent", "guid": "G"}), "")])
    client = ImsgClient("imsg", runner=runner)
    result = await client.send("+12025550100", "hello", service="auto")
    assert result["guid"] == "G"
    assert runner.calls[0][1][:3] == ["send", "--to", "+12025550100"]


@pytest.mark.asyncio
async def test_client_send_requires_json_confirmation():
    client = ImsgClient("imsg", runner=FakeRunner([(0, "", "")]))
    with pytest.raises(ImsgCommandError, match="no JSON object"):
        await client.send("42", "hello")


@pytest.mark.asyncio
async def test_client_send_rejects_symlink_file_before_runner(tmp_path):
    real_file = tmp_path / "real.txt"
    real_file.write_text("synthetic attachment")
    link = tmp_path / "link.txt"
    link.symlink_to(real_file)
    runner = FakeRunner([(0, json.dumps({"status": "sent", "guid": "G"}), "")])
    client = ImsgClient("imsg", runner=runner)

    with pytest.raises(ImsgCommandError, match="symlink"):
        await client.send("42", file=str(link))

    assert runner.calls == []


@pytest.mark.asyncio
async def test_client_nonzero_exit_is_redacted_and_structured():
    runner = FakeRunner([(7, "", "failed for +12025550100 alice@example.com")])
    client = ImsgClient("imsg", runner=runner)
    with pytest.raises(ImsgCommandError) as exc:
        await client.chats()
    assert exc.value.exit_code == 7
    assert "+12025550100" not in str(exc.value)
    assert "alice@example.com" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)


@pytest.mark.asyncio
async def test_client_timeout_has_command_context_without_payload():
    async def slow(binary, args):
        await asyncio.sleep(1)
        return 0, "", ""

    client = ImsgClient("imsg", runner=slow, timeout=0.001)
    with pytest.raises(ImsgCommandError, match="timed out") as exc:
        await client.search("private message")
    assert "private message" not in str(exc.value)


@pytest.mark.asyncio
async def test_client_advanced_actions_are_explicit_not_auto_launched():
    runner = FakeRunner([
        (0, '{"success":true}\n', ""),
        (0, '{"status":"started"}\n', ""),
        (0, '{"success":true}\n', ""),
    ])
    client = ImsgClient("imsg", runner=runner)
    assert await client.mark_read("42") is True
    assert await client.typing("42") is True
    await client.react("42", "like")
    assert [call[1][0] for call in runner.calls] == ["read", "typing", "react"]
    assert all(call[1][0] != "launch" for call in runner.calls)
