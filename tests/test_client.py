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


class FakeRpcRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, binary, request):
        self.calls.append((binary, request))
        return self.responses.pop(0)


def action_status(*methods, selectors=None):
    return json.dumps(
        {
            "advanced_features": True,
            "v2_ready": True,
            "rpc_methods": list(methods),
            "selectors": selectors or {},
        }
    )


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
async def test_client_legacy_send_returns_first_ndjson_object():
    runner = FakeRunner([(0, json.dumps({"status": "sent", "guid": "G"}), "")])
    client = ImsgClient("imsg", runner=runner)
    result = await client.send("sms:+12025550100", "hello")
    assert result["guid"] == "G"
    assert runner.calls[0][1][:3] == ["send", "--to", "+12025550100"]


@pytest.mark.asyncio
async def test_client_send_requires_json_confirmation():
    client = ImsgClient(
        "imsg",
        runner=FakeRunner(
            [
                (0, action_status(), ""),
                (0, "", ""),
            ]
        ),
    )
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


@pytest.mark.asyncio
async def test_client_edit_probes_then_executes_exact_cli_contract():
    runner = FakeRunner(
        [
            (0, action_status("message.edit", selectors={"editMessage": True}), ""),
            (0, json.dumps({"success": True}), ""),
        ]
    )
    client = ImsgClient("imsg", runner=runner)
    await client.edit("CHAT", "MSG", "updated", part_index=1)
    assert [call[1] for call in runner.calls] == [
        ["status", "--json"],
        [
            "edit",
            "--chat",
            "CHAT",
            "--message",
            "MSG",
            "--new-text",
            "updated",
            "--bc-text",
            "updated",
            "--part",
            "1",
            "--json",
        ],
    ]


@pytest.mark.asyncio
async def test_client_markdown_send_uses_rpc_formatting_ranges():
    cli_runner = FakeRunner([])
    rpc = FakeRpcRunner(
        [
            (
                0,
                json.dumps(
                    {"jsonrpc": "2.0", "id": "hermes-send", "result": {"guid": "OUT"}}
                ),
                "",
            )
        ]
    )
    client = ImsgClient("imsg", runner=cli_runner, rpc_runner=rpc)
    result = await client.send("16", "**bold**")
    assert result["guid"] == "OUT"
    request = rpc.calls[0][1]
    assert request["method"] == "send"
    assert request["params"] == {
        "chat_id": 16,
        "text": "bold",
        "service": "auto",
        "transport": "auto",
        "formatting": [{"start": 0, "length": 4, "styles": ["bold"]}],
    }
    assert cli_runner.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "expected_target"),
    [
        ("16", {"chat_id": 16}),
        ("chat_guid:iMessage;+;group", {"chat_guid": "iMessage;+;group"}),
        ("chat_identifier:group-chat", {"chat_identifier": "group-chat"}),
        ("imessage:+12025550100", {"to": "+12025550100"}),
        ("+12025550101", {"to": "+12025550101"}),
    ],
)
async def test_client_plain_imessage_send_uses_advertised_bridge_rpc(
    target, expected_target
):
    cli_runner = FakeRunner([(0, action_status("send"), "")])
    rpc_runner = FakeRpcRunner(
        [
            (
                0,
                json.dumps(
                    {"jsonrpc": "2.0", "id": "hermes-send", "result": {"guid": "OUT"}}
                ),
                "",
            )
        ]
    )
    client = ImsgClient("imsg", runner=cli_runner, rpc_runner=rpc_runner)

    result = await client.send(target, "hello")

    assert result == {"guid": "OUT"}
    assert [call[1] for call in cli_runner.calls] == [["status", "--json"]]
    request = rpc_runner.calls[0][1]
    assert request["method"] == "send"
    assert request["params"] == {
        **expected_target,
        "text": "hello",
        "service": "imessage" if target.startswith("imessage:") else "auto",
        "transport": "bridge",
    }


@pytest.mark.asyncio
async def test_client_plain_send_falls_back_only_when_bridge_send_is_unavailable():
    cli_runner = FakeRunner(
        [
            (0, action_status(), ""),
            (0, json.dumps({"guid": "LEGACY"}), ""),
        ]
    )
    rpc_runner = FakeRpcRunner([])
    client = ImsgClient("imsg", runner=cli_runner, rpc_runner=rpc_runner)

    result = await client.send("16", "hello")

    assert result == {"guid": "LEGACY"}
    assert [call[1] for call in cli_runner.calls] == [
        ["status", "--json"],
        ["send", "--chat-id", "16", "--text", "hello", "--json"],
    ]
    assert rpc_runner.calls == []


@pytest.mark.asyncio
async def test_client_plain_send_fails_closed_when_capability_discovery_fails():
    cli_runner = FakeRunner([(7, "", "status unavailable")])
    rpc_runner = FakeRpcRunner([])
    client = ImsgClient("imsg", runner=cli_runner, rpc_runner=rpc_runner)

    with pytest.raises(ImsgCommandError, match="status unavailable"):
        await client.send("16", "hello")

    assert [call[1] for call in cli_runner.calls] == [["status", "--json"]]
    assert rpc_runner.calls == []


@pytest.mark.asyncio
async def test_client_plain_send_never_falls_back_after_rpc_error():
    cli_runner = FakeRunner([(0, action_status("send"), "")])
    rpc_runner = FakeRpcRunner(
        [
            (
                0,
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "hermes-send",
                        "error": {"code": -32000, "message": "bridge refused"},
                    }
                ),
                "",
            )
        ]
    )
    client = ImsgClient("imsg", runner=cli_runner, rpc_runner=rpc_runner)

    with pytest.raises(ImsgCommandError, match="bridge refused"):
        await client.send("16", "hello")

    assert [call[1] for call in cli_runner.calls] == [["status", "--json"]]
    assert len(rpc_runner.calls) == 1


@pytest.mark.asyncio
async def test_client_plain_send_never_falls_back_after_rpc_timeout():
    cli_runner = FakeRunner([(0, action_status("send"), "")])
    rpc_calls = []

    async def slow_rpc(binary, request):
        rpc_calls.append((binary, request))
        await asyncio.sleep(1)
        return 0, "", ""

    client = ImsgClient(
        "imsg", runner=cli_runner, rpc_runner=slow_rpc, timeout=0.001
    )

    with pytest.raises(ImsgCommandError, match="timed out"):
        await client.send("16", "hello")

    assert [call[1] for call in cli_runner.calls] == [["status", "--json"]]
    assert len(rpc_calls) == 1


@pytest.mark.asyncio
async def test_client_plain_send_preserves_explicit_target_kinds():
    runner = FakeRunner(
        [
            (0, action_status(), ""),
            (0, json.dumps({"guid": "GUID-SEND"}), ""),
            (0, action_status(), ""),
            (0, json.dumps({"guid": "IDENTIFIER-SEND"}), ""),
            (0, json.dumps({"guid": "SMS-SEND"}), ""),
        ]
    )
    client = ImsgClient("imsg", runner=runner)

    await client.send("chat_guid:iMessage;+;group", "hello")
    await client.send("chat_identifier:group-chat", "hello")
    await client.send("sms:+12025550100", "hello")

    assert runner.calls[1][1][:3] == [
        "send",
        "--chat-guid",
        "iMessage;+;group",
    ]
    assert runner.calls[3][1][:3] == [
        "send",
        "--chat-identifier",
        "group-chat",
    ]
    assert runner.calls[4][1][:3] == ["send", "--to", "+12025550100"]
    assert runner.calls[4][1][-3:] == ["--service", "sms", "--json"]


@pytest.mark.asyncio
async def test_client_attachment_send_stays_on_legacy_cli(tmp_path):
    attachment = tmp_path / "attachment.txt"
    attachment.write_text("synthetic attachment")
    cli_runner = FakeRunner([(0, json.dumps({"guid": "FILE"}), "")])
    rpc_runner = FakeRpcRunner([])
    client = ImsgClient("imsg", runner=cli_runner, rpc_runner=rpc_runner)

    result = await client.send("16", "**caption**", file=str(attachment))

    assert result == {"guid": "FILE"}
    assert cli_runner.calls[0][1] == [
        "send",
        "--chat-id",
        "16",
        "--text",
        "**caption**",
        "--file",
        str(attachment),
        "--json",
    ]
    assert rpc_runner.calls == []


@pytest.mark.asyncio
async def test_client_explicit_sms_with_markdown_stays_on_legacy_cli():
    cli_runner = FakeRunner([(0, json.dumps({"guid": "SMS"}), "")])
    rpc_runner = FakeRpcRunner([])
    client = ImsgClient("imsg", runner=cli_runner, rpc_runner=rpc_runner)

    result = await client.send("sms:+12025550100", "**hello**")

    assert result == {"guid": "SMS"}
    assert cli_runner.calls[0][1] == [
        "send",
        "--to",
        "+12025550100",
        "--text",
        "**hello**",
        "--service",
        "sms",
        "--json",
    ]
    assert rpc_runner.calls == []


@pytest.mark.asyncio
async def test_client_native_methods_use_their_advertised_commands(tmp_path):
    attachment = tmp_path / "attachment.bin"
    attachment.write_bytes(b"synthetic")
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"synthetic-icon")
    statuses = [
        ("tapback", {}),
        ("message.unsend", {"retractMessagePart": True}),
        ("send.rich", {}),
        ("send.attachment", {}),
        ("group.rename", {}),
        ("group.setIcon", {}),
        ("group.addParticipant", {}),
        ("group.removeParticipant", {}),
        ("group.leave", {}),
    ]
    responses = []
    for method, selectors in statuses:
        responses.extend(
            [
                (0, action_status(method, selectors=selectors), ""),
                (0, json.dumps({"success": True, "guid": method}), ""),
            ]
        )
    runner = FakeRunner(responses)
    client = ImsgClient("imsg", runner=runner)

    await client.tapback("CHAT", "MSG", "like", remove=True, part_index=2)
    await client.unsend("CHAT", "MSG", part_index=1)
    await client.send_rich("CHAT", "**reply**", reply_to="MSG")
    await client.send_attachment("CHAT", str(attachment), as_voice=True)
    await client.rename_group("CHAT", "Family")
    await client.set_group_icon("CHAT", str(icon))
    await client.add_participant("CHAT", "+12025550101")
    await client.remove_participant("CHAT", "+12025550101")
    await client.leave_group("CHAT")

    mutations = [call[1] for call in runner.calls if call[1][0] != "status"]
    assert [args[0] for args in mutations] == [
        "tapback",
        "unsend",
        "send-rich",
        "send-attachment",
        "chat-name",
        "chat-photo",
        "chat-add-member",
        "chat-remove-member",
        "chat-leave",
    ]
    assert "--remove" in mutations[0]
    assert mutations[2][mutations[2].index("--format") + 1] == (
        '[{"start":0,"length":5,"styles":["bold"]}]'
    )
    assert "--audio" in mutations[3]


@pytest.mark.asyncio
async def test_client_unsafe_rich_attachment_never_invokes_runner(tmp_path):
    runner = FakeRunner([])
    client = ImsgClient("imsg", runner=runner)
    with pytest.raises(ImsgCommandError, match="regular file"):
        await client.send_rich("CHAT", "caption", file=str(tmp_path))
    assert runner.calls == []


@pytest.mark.asyncio
async def test_client_native_capability_failure_prevents_mutation():
    runner = FakeRunner(
        [
            (
                0,
                json.dumps(
                    {
                        "advanced_features": True,
                        "v2_ready": True,
                        "rpc_methods": [],
                        "selectors": {},
                    }
                ),
                "",
            )
        ]
    )
    client = ImsgClient("imsg", runner=runner)
    with pytest.raises(ImsgCommandError, match="group.rename"):
        await client.rename_group("CHAT", "Family")
    assert [call[1] for call in runner.calls] == [["status", "--json"]]


@pytest.mark.asyncio
async def test_client_explicit_failure_object_is_not_success():
    runner = FakeRunner(
        [
            (0, action_status("group.leave"), ""),
            (0, json.dumps({"success": False, "error": "bridge refused"}), ""),
        ]
    )
    client = ImsgClient("imsg", runner=runner)
    with pytest.raises(ImsgCommandError, match="bridge refused"):
        await client.leave_group("CHAT")
