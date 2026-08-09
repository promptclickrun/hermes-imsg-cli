"""Native poll parity tests. Every subprocess is fake; no Messages mutation occurs."""

import json

import pytest

from hermes_imsg_cli import cli
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


def capable_status(*, send=True, vote=True, unvote=True):
    methods = ["poll.send", "poll.vote", "polls.unvote"]
    return json.dumps({
        "advanced_features": True,
        "v2_ready": True,
        "selectors": {
            "pollPayloadMessage": send,
            "pollVoteMessage": vote or unvote,
        },
        "rpc_methods": methods,
    })


def test_poll_send_cli_builder_matches_upstream_flags():
    assert cli.build_poll_send_args(
        chat_id="42",
        question="Dinner?",
        options=[" Pizza ", "Sushi"],
        comment="Vote by 5pm",
        reply_to="PARENT",
    ) == [
        "poll", "send", "--chat-id", "42", "--question", "Dinner?",
        "--comment", "Vote by 5pm", "--reply-to", "PARENT",
        "--option", "Pizza", "--option", "Sushi", "--json",
    ]
    assert cli.build_poll_send_args(
        chat_guid="iMessage;+;chat123", question="Dinner?", options=["Pizza", "Sushi"]
    )[:4] == ["poll", "send", "--chat", "iMessage;+;chat123"]


@pytest.mark.parametrize("options", [[], ["only"], [" ", "two"]])
def test_poll_send_requires_question_and_two_nonempty_options(options):
    with pytest.raises(ValueError, match="at least two"):
        cli.build_poll_send_args(chat_id="42", question="Dinner?", options=options)
    with pytest.raises(ValueError, match="question"):
        cli.build_poll_send_args(chat_id="42", question="", options=["a", "b"])


def test_poll_target_requires_exactly_one_rowid_or_guid_and_positive_rowid():
    with pytest.raises(ValueError, match="exactly one"):
        cli.build_poll_send_args(question="Q", options=["a", "b"])
    with pytest.raises(ValueError, match="exactly one"):
        cli.build_poll_send_args(chat_id="42", chat_guid="G", question="Q", options=["a", "b"])
    with pytest.raises(ValueError, match="positive integer"):
        cli.build_poll_send_args(chat_id="0", question="Q", options=["a", "b"])


@pytest.mark.parametrize(
    ("kwargs", "tail"),
    [
        ({"option_id": "OPT"}, ["--option-id", "OPT", "--json"]),
        ({"option_index": 2}, ["--option-index", "2", "--json"]),
        ({"option": "Sushi"}, ["--option", "Sushi", "--json"]),
    ],
)
def test_poll_vote_builder_supports_each_upstream_selector(kwargs, tail):
    args = cli.build_poll_vote_args("vote", chat_id="42", poll_guid="POLL", **kwargs)
    assert args[:6] == ["poll", "vote", "--chat-id", "42", "--poll", "POLL"]
    assert args[-3:] == tail


def test_poll_unvote_is_selective_and_uses_same_selector_contract():
    assert cli.build_poll_vote_args(
        "unvote", chat_guid="iMessage;+;g", poll_guid="p:0/POLL", option_index=1
    ) == [
        "poll", "unvote", "--chat", "iMessage;+;g", "--poll", "p:0/POLL",
        "--option-index", "1", "--json",
    ]


def test_poll_vote_rejects_missing_multiple_or_invalid_selectors():
    base = {"chat_id": "42", "poll_guid": "POLL"}
    with pytest.raises(ValueError, match="exactly one"):
        cli.build_poll_vote_args("vote", **base)
    with pytest.raises(ValueError, match="exactly one"):
        cli.build_poll_vote_args("vote", option_id="A", option="a", **base)
    with pytest.raises(ValueError, match="1-based"):
        cli.build_poll_vote_args("vote", option_index=0, **base)
    with pytest.raises(ValueError, match="vote or unvote"):
        cli.build_poll_vote_args("remove", option_id="A", **base)


def test_poll_rpc_request_builders_match_pinned_upstream_methods():
    send = cli.build_poll_send_rpc_request(
        "req-1", chat_id=42, question="Dinner?", options=["Pizza", "Sushi"],
        comment="Vote by 5", reply_to="PARENT",
    )
    assert send == {
        "jsonrpc": "2.0", "id": "req-1", "method": "poll.send",
        "params": {"chat_id": 42, "question": "Dinner?", "options": ["Pizza", "Sushi"],
                   "comment": "Vote by 5", "reply_to": "PARENT"},
    }
    assert cli.build_poll_vote_rpc_request(
        "req-2", "unvote", chat_guid="iMessage;+;g", poll_guid="POLL", option_id="OPT"
    ) == {
        "jsonrpc": "2.0", "id": "req-2", "method": "polls.unvote",
        "params": {"chat_guid": "iMessage;+;g", "poll_guid": "POLL", "option_id": "OPT"},
    }
    with pytest.raises(ValueError, match="option_id"):
        cli.build_poll_vote_rpc_request(
            "req", "vote", chat_id=42, poll_guid="POLL", option_index=1
        )


@pytest.mark.asyncio
async def test_client_poll_send_checks_capability_then_returns_result():
    runner = FakeRunner([(0, capable_status(), "")])
    rpc_runner = FakeRpcRunner([(
        0,
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
            "guid": "POLL", "poll": {"kind": "created"}
        }}),
        "",
    )])
    client = ImsgClient("/opt/imsg", runner=runner, rpc_runner=rpc_runner)
    result = await client.send_poll("42", "Dinner?", ["Pizza", "Sushi"])
    assert result["poll"]["kind"] == "created"
    assert [call[1][0] for call in runner.calls] == ["status"]
    assert rpc_runner.calls[0][1]["method"] == "poll.send"


@pytest.mark.asyncio
async def test_client_poll_send_uses_rpc_mutation_path_not_cli_poll_command():
    status_runner = FakeRunner([(0, capable_status(), "")])
    rpc_runner = FakeRpcRunner([(
        0,
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "ok": True,
                "event": "imessage.poll.created",
                "guid": "POLL-RPC",
            },
        }),
        "",
    )])
    client = ImsgClient("/opt/imsg", runner=status_runner, rpc_runner=rpc_runner)
    result = await client.send_poll("42", "Dinner?", ["Pizza", "Sushi"])
    assert result["guid"] == "POLL-RPC"
    assert status_runner.calls[0][1][0] == "status"
    assert rpc_runner.calls[0][1]["method"] == "poll.send"
    assert rpc_runner.calls[0][1]["params"] == {
        "chat_id": 42,
        "question": "Dinner?",
        "options": ["Pizza", "Sushi"],
    }


@pytest.mark.asyncio
async def test_client_poll_vote_and_unvote_use_cli_selectors():
    runner = FakeRunner([
        (0, capable_status(), ""), (0, json.dumps({"messageGuid": "VOTE"}), ""),
        (0, capable_status(), ""), (0, json.dumps({"messageGuid": "UNVOTE"}), ""),
    ])
    client = ImsgClient("imsg", runner=runner)
    assert (await client.vote_poll("42", "POLL", option="Sushi"))["messageGuid"] == "VOTE"
    assert (await client.unvote_poll("42", "POLL", option_id="OPT"))["messageGuid"] == "UNVOTE"
    assert runner.calls[1][1][1] == "vote"
    assert runner.calls[3][1][1] == "unvote"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "status", "needle"),
    [
        ("send", capable_status(send=False), "pollPayloadMessage"),
        ("vote", capable_status(vote=False, unvote=False), "pollVoteMessage"),
        ("unvote", capable_status(vote=False, unvote=False), "pollVoteMessage"),
    ],
)
async def test_client_fails_before_mutation_when_bridge_capability_missing(operation, status, needle):
    runner = FakeRunner([(0, status, "")])
    client = ImsgClient("imsg", runner=runner)
    with pytest.raises(ImsgCommandError, match=needle):
        if operation == "send":
            await client.send_poll("42", "Q", ["a", "b"])
        elif operation == "vote":
            await client.vote_poll("42", "P", option_id="O")
        else:
            await client.unvote_poll("42", "P", option_id="O")
    assert len(runner.calls) == 1
    assert runner.calls[0][1] == ["status", "--json"]


@pytest.mark.asyncio
async def test_client_reports_installed_binary_without_poll_method():
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
    with pytest.raises(ImsgCommandError, match="installed imsg does not advertise poll.send"):
        await client.send_poll("42", "Q", ["a", "b"])


@pytest.mark.asyncio
async def test_poll_nonzero_and_timeout_remain_structured_and_redacted():
    runner = FakeRunner([
        (0, capable_status(), ""),
    ])
    rpc_runner = FakeRpcRunner([(9, "", "bridge failed for +141****1212 alice@example.com")])
    client = ImsgClient("imsg", runner=runner, rpc_runner=rpc_runner)
    with pytest.raises(ImsgCommandError) as exc:
        await client.send_poll("42", "Q", ["a", "b"])
    assert exc.value.exit_code == 9
    assert "+12025550100" not in str(exc.value)
    assert "alice@example.com" not in str(exc.value)


@pytest.mark.asyncio
async def test_poll_success_requires_json_object_not_empty_stdout():
    runner = FakeRunner([(0, capable_status(), "")])
    rpc_runner = FakeRpcRunner([(0, "", "")])
    client = ImsgClient("imsg", runner=runner, rpc_runner=rpc_runner)
    with pytest.raises(ImsgCommandError, match="no JSON object"):
        await client.send_poll("42", "Q", ["a", "b"])


@pytest.mark.asyncio
async def test_poll_explicit_error_object_is_not_reported_as_success():
    runner = FakeRunner([(0, capable_status(), "")])
    rpc_runner = FakeRpcRunner([(
        0,
        json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "bridge selector unavailable"}}),
        "",
    )])
    client = ImsgClient("imsg", runner=runner, rpc_runner=rpc_runner)
    with pytest.raises(ImsgCommandError, match="bridge selector unavailable"):
        await client.send_poll("42", "Q", ["a", "b"])
