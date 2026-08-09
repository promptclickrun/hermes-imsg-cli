"""Native iMessage command and JSON-RPC contract tests."""

import json

import pytest

from hermes_imsg_cli import cli


def test_build_tapback_remove_includes_part_and_remove():
    assert cli.build_tapback_args(
        "iMessage;+;chat", "MSG", "like", remove=True, part_index=2
    ) == [
        "tapback",
        "--chat",
        "iMessage;+;chat",
        "--message",
        "MSG",
        "--kind",
        "like",
        "--part",
        "2",
        "--remove",
        "--json",
    ]


def test_build_edit_uses_text_for_backwards_compatibility_by_default():
    assert cli.build_edit_args("CHAT", "MSG", "updated", part_index=1) == [
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
    ]


def test_build_unsend_includes_part():
    assert cli.build_unsend_args("CHAT", "MSG", part_index=3) == [
        "unsend",
        "--chat",
        "CHAT",
        "--message",
        "MSG",
        "--part",
        "3",
        "--json",
    ]


def test_build_rich_send_combines_reply_effect_formatting_and_file():
    ranges = [{"start": 0, "length": 5, "styles": ["bold"]}]
    assert cli.build_rich_send_args(
        "CHAT",
        "hello",
        reply_to="MSG",
        effect="com.apple.MobileSMS.expressivesend.loud",
        part_index=1,
        file_path="/tmp/a.jpg",
        formatting=ranges,
    ) == [
        "send-rich",
        "--chat",
        "CHAT",
        "--text",
        "hello",
        "--part",
        "1",
        "--effect",
        "com.apple.MobileSMS.expressivesend.loud",
        "--reply-to",
        "MSG",
        "--format",
        json.dumps(ranges, separators=(",", ":")),
        "--file",
        "/tmp/a.jpg",
        "--json",
    ]


def test_build_send_attachment_supports_voice_mode():
    assert cli.build_send_attachment_args("CHAT", "/tmp/a.m4a", as_voice=True) == [
        "send-attachment",
        "--chat",
        "CHAT",
        "--file",
        "/tmp/a.m4a",
        "--audio",
        "--json",
    ]


def test_build_group_mutation_commands():
    assert cli.build_chat_name_args("CHAT", "Family") == [
        "chat-name", "--chat", "CHAT", "--name", "Family", "--json"
    ]
    assert cli.build_chat_photo_args("CHAT", "/tmp/icon.png") == [
        "chat-photo", "--chat", "CHAT", "--file", "/tmp/icon.png", "--json"
    ]
    assert cli.build_chat_member_args("add", "CHAT", "+12025550101") == [
        "chat-add-member", "--chat", "CHAT", "--address", "+12025550101", "--json"
    ]
    assert cli.build_chat_member_args("remove", "CHAT", "+12025550101") == [
        "chat-remove-member", "--chat", "CHAT", "--address", "+12025550101", "--json"
    ]
    assert cli.build_chat_leave_args("CHAT") == [
        "chat-leave", "--chat", "CHAT", "--json"
    ]


def test_build_send_rpc_request_preserves_exact_rowid_and_formatting():
    ranges = [{"start": 0, "length": 4, "styles": ["bold"]}]
    assert cli.build_send_rpc_request(
        "req-1", chat_id="16", text="bold", formatting=ranges
    ) == {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "send",
        "params": {
            "chat_id": 16,
            "text": "bold",
            "service": "auto",
            "transport": "auto",
            "formatting": ranges,
        },
    }


@pytest.mark.parametrize(
    ("target", "prefix", "service"),
    [
        ("chat_id:16", ["send", "--chat-id", "16"], None),
        ("chat_guid:iMessage;+;chat", ["send", "--chat-guid", "iMessage;+;chat"], None),
        (
            "chat_identifier:iMessage;-;+12025550101",
            ["send", "--chat-identifier", "iMessage;-;+12025550101"],
            None,
        ),
        ("imessage:+12025550101", ["send", "--to", "+12025550101"], "imessage"),
        ("sms:+12025550101", ["send", "--to", "+12025550101"], "sms"),
    ],
)
def test_build_plain_send_preserves_explicit_target_kind(target, prefix, service):
    args = cli.build_plain_send_args(target, "hello")
    assert args[:3] == prefix
    if service:
        assert args[args.index("--service") + 1] == service


def test_build_plain_send_rejects_conflicting_service_prefix():
    with pytest.raises(ValueError, match="conflicts"):
        cli.build_plain_send_args("sms:+12025550101", "hello", service="imessage")


@pytest.mark.parametrize("part_index", [-1, True, "not-a-number"])
def test_native_builders_reject_invalid_part_index(part_index):
    with pytest.raises(ValueError, match="part_index"):
        cli.build_unsend_args("CHAT", "MSG", part_index=part_index)


def test_poll_options_enforce_messages_limit_and_distinctness():
    with pytest.raises(ValueError, match="at most 12"):
        cli.build_poll_send_args(
            chat_id="16", question="Q", options=[str(index) for index in range(13)]
        )
    with pytest.raises(ValueError, match="distinct"):
        cli.build_poll_send_args(chat_id="16", question="Q", options=["Yes", "Yes"])


def test_edit_requires_method_and_one_edit_selector():
    from hermes_imsg_cli.capabilities import ImsgCapabilities, ImsgCapabilityError

    caps = ImsgCapabilities.from_status(
        {
            "advanced_features": True,
            "v2_ready": True,
            "rpc_methods": ["message.edit"],
            "selectors": {"editMessage": False, "editMessageItem": False},
        }
    )
    with pytest.raises(ImsgCapabilityError, match="editMessage"):
        caps.require_action("edit")


def test_poll_vote_accepts_rpc_alias_and_selector():
    from hermes_imsg_cli.capabilities import ImsgCapabilities

    caps = ImsgCapabilities.from_status(
        {
            "advanced_features": True,
            "v2_ready": True,
            "rpc_methods": ["messages.poll.vote"],
            "selectors": {"pollVoteMessage": True},
        }
    )
    assert caps.supports_action("poll-vote") is True


def test_every_native_action_has_a_capability_contract():
    from hermes_imsg_cli.capabilities import ImsgCapabilities

    caps = ImsgCapabilities.from_status(
        {
            "advanced_features": True,
            "v2_ready": True,
            "rpc_methods": [
                "tapback",
                "message.edit",
                "message.unsend",
                "send.rich",
                "send.attachment",
                "group.rename",
                "group.setIcon",
                "group.addParticipant",
                "group.removeParticipant",
                "group.leave",
                "poll.send",
                "poll.vote",
                "poll.unvote",
            ],
            "selectors": {
                "editMessage": True,
                "retractMessagePart": True,
                "pollPayloadMessage": True,
                "pollVoteMessage": True,
            },
        }
    )
    assert caps.available_actions() == (
        "react",
        "edit",
        "unsend",
        "reply",
        "sendWithEffect",
        "upload-file",
        "renameGroup",
        "setGroupIcon",
        "addParticipant",
        "removeParticipant",
        "leaveGroup",
        "poll",
        "poll-vote",
        "poll-unvote",
    )


def test_advanced_actions_require_a_ready_private_bridge():
    from hermes_imsg_cli.capabilities import ImsgCapabilities, ImsgCapabilityError

    caps = ImsgCapabilities.from_status(
        {
            "advanced_features": False,
            "v2_ready": True,
            "rpc_methods": ["tapback"],
            "selectors": {},
            "message": "System Integrity Protection is enabled.",
        }
    )
    with pytest.raises(ImsgCapabilityError, match="System Integrity Protection"):
        caps.require_action("react")


def test_capability_parser_ignores_malformed_entries():
    from hermes_imsg_cli.capabilities import ImsgCapabilities

    caps = ImsgCapabilities.from_status(
        {
            "advanced_features": True,
            "v2_ready": True,
            "rpc_methods": ["tapback", 42, None],
            "selectors": {"pollPayloadMessage": "yes", "pollVoteMessage": True},
        }
    )
    assert caps.rpc_methods == frozenset({"tapback"})
    assert caps.selectors == {"pollVoteMessage": True}


def test_missing_method_error_names_the_advertised_contract():
    from hermes_imsg_cli.capabilities import ImsgCapabilities, ImsgCapabilityError

    caps = ImsgCapabilities.from_status(
        {"advanced_features": True, "v2_ready": True, "rpc_methods": [], "selectors": {}}
    )
    with pytest.raises(ImsgCapabilityError, match="group.rename"):
        caps.require_action("renameGroup")
