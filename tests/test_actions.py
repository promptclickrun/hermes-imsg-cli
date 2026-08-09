"""Unified native action tool tests. All clients are in-memory fakes."""

import pytest

from hermes_imsg_cli import actions


class FakeActionClient:
    def __init__(self, *, group=None, chats=None, history=None):
        self.group_record = group or {
            "id": 16,
            "guid": "CHAT-16",
            "chat_identifier": "group-chat",
            "is_group": True,
        }
        self.chat_records = list(chats or [self.group_record])
        self.history_records = list(history or [])
        self.lookups = []
        self.mutations = []

    async def group(self, chat_id):
        self.lookups.append(("group", chat_id))
        return dict(self.group_record)

    async def chats(self, *, limit=20, unread_only=False):
        self.lookups.append(("chats", limit))
        return [dict(record) for record in self.chat_records]

    async def history(self, chat_id, *, limit=50, attachments=True, **kwargs):
        self.lookups.append(("history", chat_id, limit, attachments))
        return [dict(record) for record in self.history_records]

    async def _mutation(self, name, *args, **kwargs):
        self.mutations.append((name, args, kwargs))
        return {"native": name}

    async def tapback(self, *args, **kwargs):
        return await self._mutation("tapback", *args, **kwargs)

    async def edit(self, *args, **kwargs):
        return await self._mutation("edit", *args, **kwargs)

    async def unsend(self, *args, **kwargs):
        return await self._mutation("unsend", *args, **kwargs)

    async def send_rich(self, *args, **kwargs):
        return await self._mutation("send_rich", *args, **kwargs)

    async def send_attachment(self, *args, **kwargs):
        return await self._mutation("send_attachment", *args, **kwargs)

    async def rename_group(self, *args, **kwargs):
        return await self._mutation("rename_group", *args, **kwargs)

    async def set_group_icon(self, *args, **kwargs):
        return await self._mutation("set_group_icon", *args, **kwargs)

    async def add_participant(self, *args, **kwargs):
        return await self._mutation("add_participant", *args, **kwargs)

    async def remove_participant(self, *args, **kwargs):
        return await self._mutation("remove_participant", *args, **kwargs)

    async def leave_group(self, *args, **kwargs):
        return await self._mutation("leave_group", *args, **kwargs)

    async def send_poll(self, *args, **kwargs):
        return await self._mutation("send_poll", *args, **kwargs)

    async def vote_poll(self, *args, **kwargs):
        return await self._mutation("vote_poll", *args, **kwargs)

    async def unvote_poll(self, *args, **kwargs):
        return await self._mutation("unvote_poll", *args, **kwargs)


@pytest.mark.asyncio
async def test_cross_chat_message_reference_is_rejected_before_edit(monkeypatch):
    client = FakeActionClient(history=[])
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    result = await actions.handle_imsg_action_tool(
        {
            "action": "edit",
            "chat_id": "16",
            "message_id": "OTHER",
            "text": "updated",
        }
    )
    assert result["success"] is False
    assert "current conversation" in result["error"]
    assert client.mutations == []


@pytest.mark.asyncio
async def test_reaction_removal_without_name_uses_current_bot_tapback(monkeypatch):
    client = FakeActionClient(
        history=[
            {
                "guid": "INBOUND",
                "is_from_me": False,
                "reactions": [
                    {
                        "type": "emphasis",
                        "emoji": "!!",
                        "is_from_me": True,
                    }
                ],
            }
        ]
    )
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)

    result = await actions.handle_imsg_action_tool(
        {
            "action": "react",
            "chat_id": "16",
            "message_id": "INBOUND",
            "remove": True,
        }
    )

    assert result["success"] is True
    assert client.mutations[0][1][2] == "emphasis"
    assert client.mutations[0][2]["remove"] is True


@pytest.mark.asyncio
async def test_reaction_removal_without_current_bot_tapback_fails_closed(monkeypatch):
    client = FakeActionClient(
        history=[{"guid": "INBOUND", "is_from_me": False, "reactions": []}]
    )
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)

    result = await actions.handle_imsg_action_tool(
        {
            "action": "react",
            "chat_id": "16",
            "message_id": "INBOUND",
            "remove": True,
        }
    )

    assert result["success"] is False
    assert "bot-authored tapback" in result["error"]
    assert client.mutations == []


@pytest.mark.asyncio
async def test_edit_and_unsend_require_bot_authored_message(monkeypatch):
    client = FakeActionClient(
        history=[{"guid": "INBOUND", "is_from_me": False}]
    )
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    for action in ("edit", "unsend"):
        payload = {
            "action": action,
            "chat_id": "16",
            "message_id": "INBOUND",
        }
        if action == "edit":
            payload["text"] = "updated"
        result = await actions.handle_imsg_action_tool(payload)
        assert result["success"] is False
        assert "bot-authored" in result["error"]
    assert client.mutations == []


@pytest.mark.asyncio
async def test_all_native_action_branches_dispatch_exact_parameters(monkeypatch):
    client = FakeActionClient(
        history=[
            {"guid": "INBOUND", "is_from_me": False},
            {"guid": "OUTBOUND", "is_from_me": True},
            {"guid": "POLL", "is_from_me": False},
        ]
    )
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    payloads = [
        {
            "action": "react",
            "chat_id": "16",
            "message_id": "INBOUND",
            "reaction": "like",
            "remove": True,
            "part_index": 2,
        },
        {
            "action": "edit",
            "chat_id": "16",
            "message_id": "OUTBOUND",
            "text": "updated",
            "backwards_compatibility_message": "Updated: updated",
            "part_index": 1,
        },
        {
            "action": "unsend",
            "chat_id": "16",
            "message_id": "OUTBOUND",
        },
        {
            "action": "reply",
            "chat_id": "16",
            "message_id": "INBOUND",
            "text": "threaded",
            "file_path": "/tmp/synthetic.txt",
        },
        {
            "action": "sendWithEffect",
            "chat_id": "16",
            "text": "boom",
            "effect": "slam",
        },
        {
            "action": "upload-file",
            "chat_id": "16",
            "file_path": "/tmp/synthetic.m4a",
            "as_voice": True,
        },
        {"action": "renameGroup", "chat_id": "16", "display_name": "Family"},
        {"action": "setGroupIcon", "chat_id": "16", "file_path": "/tmp/icon.png"},
        {"action": "addParticipant", "chat_id": "16", "address": "+12025550101"},
        {"action": "removeParticipant", "chat_id": "16", "address": "+12025550101"},
        {"action": "leaveGroup", "chat_id": "16"},
        {
            "action": "poll",
            "chat_id": "16",
            "question": "Dinner?",
            "options": ["Pizza", "Sushi"],
            "reply_to": "INBOUND",
        },
        {
            "action": "poll-vote",
            "chat_id": "16",
            "poll_guid": "POLL",
            "option_index": 2,
        },
        {
            "action": "poll-unvote",
            "chat_id": "16",
            "poll_guid": "POLL",
            "option_id": "OPTION",
        },
    ]
    for payload in payloads:
        result = await actions.handle_imsg_action_tool(payload)
        assert result["success"] is True, result
        assert result["chat_id"] == "16"
        assert result["chat_guid"] == "CHAT-16"

    names = [entry[0] for entry in client.mutations]
    assert names == [
        "tapback",
        "edit",
        "unsend",
        "send_rich",
        "send_rich",
        "send_attachment",
        "rename_group",
        "set_group_icon",
        "add_participant",
        "remove_participant",
        "leave_group",
        "send_poll",
        "vote_poll",
        "unvote_poll",
    ]
    assert client.mutations[0][2] == {"remove": True, "part_index": 2}
    assert client.mutations[3][2]["reply_to"] == "INBOUND"
    assert client.mutations[3][2]["file"] == "/tmp/synthetic.txt"
    assert client.mutations[4][2]["effect"] == (
        "com.apple.MobileSMS.expressivesend.impact"
    )
    assert client.mutations[11][2]["reply_to"] == "INBOUND"
    assert client.mutations[12][2]["option_index"] == 2
    assert client.mutations[13][2]["option_id"] == "OPTION"


@pytest.mark.asyncio
async def test_target_aliases_are_exactly_one_and_group_actions_require_group(monkeypatch):
    client = FakeActionClient(group={"id": 16, "guid": "CHAT-16", "is_group": False})
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    conflict = await actions.handle_imsg_action_tool(
        {
            "action": "sendWithEffect",
            "chat_id": "16",
            "chat_guid": "CHAT-16",
            "text": "boom",
            "effect": "loud",
        }
    )
    assert conflict["success"] is False
    assert "exactly one" in conflict["error"]
    denied = await actions.handle_imsg_action_tool(
        {"action": "leaveGroup", "chat_id": "16"}
    )
    assert denied["success"] is False
    assert "group conversation" in denied["error"]
    assert client.mutations == []


@pytest.mark.asyncio
async def test_group_actions_require_a_configured_messaging_admin(monkeypatch):
    client = FakeActionClient()
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    monkeypatch.setattr(
        actions,
        "_tool_caller_context",
        lambda: ("imsg", "", "+12025550199"),
    )
    monkeypatch.setenv("IMSG_GROUP_ACTION_ADMINS", "+12025550101")

    denied = await actions.handle_imsg_action_tool(
        {"action": "leaveGroup", "chat_id": "16"}
    )

    assert denied["success"] is False
    assert "group action admin" in denied["error"]
    assert client.lookups == []
    assert client.mutations == []


@pytest.mark.asyncio
async def test_group_action_admin_defaults_to_dm_owner_allowlist(monkeypatch):
    client = FakeActionClient()
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    monkeypatch.setattr(
        actions,
        "_tool_caller_context",
        lambda: ("imsg", "", "+12025550101"),
    )
    monkeypatch.delenv("IMSG_GROUP_ACTION_ADMINS", raising=False)
    monkeypatch.setenv("IMSG_ALLOWED_USERS", "+12025550101")

    allowed = await actions.handle_imsg_action_tool(
        {"action": "leaveGroup", "chat_id": "16"}
    )

    assert allowed["success"] is True
    assert [entry[0] for entry in client.mutations] == ["leave_group"]


@pytest.mark.asyncio
async def test_local_operator_can_run_group_actions_without_gateway_identity(monkeypatch):
    client = FakeActionClient()
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    monkeypatch.setattr(actions, "_tool_caller_context", lambda: ("", "cli", ""))
    monkeypatch.delenv("IMSG_GROUP_ACTION_ADMINS", raising=False)
    monkeypatch.delenv("IMSG_ALLOWED_USERS", raising=False)

    allowed = await actions.handle_imsg_action_tool(
        {"action": "leaveGroup", "chat_id": "16"}
    )

    assert allowed["success"] is True
    assert [entry[0] for entry in client.mutations] == ["leave_group"]


@pytest.mark.asyncio
async def test_programmatic_session_without_admin_identity_cannot_mutate_group(monkeypatch):
    client = FakeActionClient()
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    monkeypatch.setattr(
        actions,
        "_tool_caller_context",
        lambda: ("api_server", "", ""),
    )
    monkeypatch.setenv("IMSG_GROUP_ACTION_ADMINS", "+12025550101")

    denied = await actions.handle_imsg_action_tool(
        {"action": "leaveGroup", "chat_id": "16"}
    )

    assert denied["success"] is False
    assert "group action admin" in denied["error"]
    assert client.lookups == []
    assert client.mutations == []


@pytest.mark.asyncio
async def test_explicit_chat_guid_resolves_rowid_before_message_lookup(monkeypatch):
    client = FakeActionClient(
        chats=[{"id": 77, "guid": "CHAT-77", "is_group": False}],
        history=[{"message_guid": "MESSAGE-77", "is_from_me": False}],
    )
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    result = await actions.handle_imsg_action_tool(
        {
            "action": "react",
            "chat_guid": "CHAT-77",
            "message_id": "MESSAGE-77",
            "reaction": "love",
        }
    )
    assert result["success"] is True
    assert ("chats", 1000) in client.lookups
    assert ("history", "77", 1000, False) in client.lookups


@pytest.mark.asyncio
async def test_direct_handle_is_synthesized_only_for_send_like_action(monkeypatch):
    client = FakeActionClient(chats=[])
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    sent = await actions.handle_imsg_action_tool(
        {
            "action": "sendWithEffect",
            "to": "imessage:+12025550100",
            "text": "boom",
            "effect": "shooting-star",
        }
    )
    assert sent["success"] is True
    assert sent["chat_guid"] == "iMessage;-;+12025550100"
    assert client.mutations[0][2]["effect"] == (
        "com.apple.messages.effect.CKShootingStarEffect"
    )

    denied = await actions.handle_imsg_action_tool(
        {
            "action": "react",
            "to": "+12025550100",
            "message_id": "UNKNOWN",
            "reaction": "like",
        }
    )
    assert denied["success"] is False
    assert "existing conversation" in denied["error"]
    assert len(client.mutations) == 1


@pytest.mark.asyncio
async def test_poll_reference_must_belong_to_target_chat(monkeypatch):
    client = FakeActionClient(history=[])
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    result = await actions.handle_imsg_action_tool(
        {
            "action": "poll-vote",
            "chat_id": "16",
            "poll_guid": "OTHER-POLL",
            "option": "Pizza",
        }
    )
    assert result["success"] is False
    assert "current conversation" in result["error"]
    assert client.mutations == []


@pytest.mark.asyncio
async def test_legacy_poll_tool_delegates_to_unified_dispatch(monkeypatch):
    client = FakeActionClient(
        history=[{"guid": "POLL", "is_from_me": False}]
    )
    monkeypatch.setattr(actions, "_action_tool_client", lambda: client)
    created = await actions.handle_imsg_poll_tool(
        {
            "action": "send",
            "chat_id": "16",
            "question": "Dinner?",
            "options": ["Pizza", "Sushi"],
        }
    )
    voted = await actions.handle_imsg_poll_tool(
        {
            "action": "vote",
            "chat_id": "16",
            "poll_guid": "POLL",
            "option": "Pizza",
        }
    )
    assert created["success"] is True and created["action"] == "send"
    assert voted["success"] is True and voted["action"] == "vote"
    assert [entry[0] for entry in client.mutations] == ["send_poll", "vote_poll"]


def test_effect_aliases_are_normalized_and_unknown_short_names_fail():
    assert actions.normalize_effect_id("invisible-ink") == (
        "com.apple.MobileSMS.expressivesend.invisibleink"
    )
    assert actions.normalize_effect_id("balloons") == (
        "com.apple.MobileSMS.expressivesend.balloon"
    )
    assert actions.normalize_effect_id("happy-birthday") == (
        "com.apple.messages.effect.CKHappyBirthdayEffect"
    )
    assert actions.normalize_effect_id(
        "COM.APPLE.MESSAGES.EFFECT.CKECHOEFFECT"
    ) == (
        "com.apple.messages.effect.CKEchoEffect"
    )
    with pytest.raises(ValueError, match="unsupported effect"):
        actions.normalize_effect_id("kaboom")
    with pytest.raises(ValueError, match="unsupported effect"):
        actions.normalize_effect_id("com.apple.messages.effect.Custom")
