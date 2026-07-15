"""Rule engine tests — pure logic, no Hermes checkout required."""

from hermes_imsg_cli.rules import (
    ImsgRule,
    _as_bool,
    _csv_set,
    _redact,
    parse_imsg_rules,
)


# ---------------------------------------------------------------------------
# ImsgRule dataclass + parse_imsg_rules()
# ---------------------------------------------------------------------------

def test_rule_parses_dm_rule():
    rules = parse_imsg_rules([{
        "name": "owners_dm",
        "context": "dm",
        "senders": ["+111", "+222"],
        "action": "allow",
    }])
    assert len(rules) == 1
    r = rules[0]
    assert r.name == "owners_dm"
    assert r.context == "dm"
    assert r.senders == {"+111", "+222"}
    assert r.action == "allow"
    assert r.chats is None
    assert r.require_participants is None


def test_rule_senders_any_parses_to_none():
    rules = parse_imsg_rules([{"name": "x", "senders": "any", "action": "allow"}])
    assert rules[0].senders is None


def test_rule_defaults():
    rules = parse_imsg_rules([{"name": "y", "action": "deny"}])
    r = rules[0]
    assert r.context == "any"
    assert r.senders is None
    assert r.chats is None
    assert r.require_participants is None
    assert r.require_mention is False


def test_rule_require_participants():
    rules = parse_imsg_rules([{
        "name": "z",
        "context": "group",
        "senders": ["+333"],
        "require_participants": {"any_of": ["+111", "+222"]},
        "action": "allow",
    }])
    rp = rules[0].require_participants
    assert rp is not None
    assert set(rp["any_of"]) == {"+111", "+222"}
    assert "all_of" not in rp


def test_empty_rules_returns_empty_list():
    assert parse_imsg_rules([]) == []
    assert parse_imsg_rules(None) == []


def test_rule_unknown_action_defaults_to_silent_drop():
    rules = parse_imsg_rules([{"name": "bad", "action": "explode"}])
    assert rules[0].action == "silent_drop"


def test_rule_unknown_context_defaults_to_any():
    rules = parse_imsg_rules([{"name": "bad", "context": "universe", "action": "allow"}])
    assert rules[0].context == "any"


def test_rule_non_dict_entry_skipped():
    rules = parse_imsg_rules(["not_a_dict", {"name": "ok", "action": "allow"}])
    assert len(rules) == 1
    assert rules[0].name == "ok"


def test_rule_chats_parsed():
    rules = parse_imsg_rules([{"name": "r", "chats": ["16", "8"], "action": "allow"}])
    assert rules[0].chats == {"16", "8"}


def test_rule_require_participants_all_of():
    rules = parse_imsg_rules([{
        "name": "r",
        "require_participants": {"all_of": ["+aaa", "+bbb"]},
        "action": "allow",
    }])
    rp = rules[0].require_participants
    assert rp is not None
    assert set(rp["all_of"]) == {"+aaa", "+bbb"}
    assert "any_of" not in rp


def test_rule_require_mention_parsed():
    rules = parse_imsg_rules([{"name": "m", "require_mention": True, "action": "allow"}])
    assert rules[0].require_mention is True


def test_imsg_rule_dataclass_fields():
    r = ImsgRule(name="test", action="allow")
    assert r.name == "test"
    assert r.action == "allow"
    assert r.context == "any"
    assert r.senders is None
    assert r.chats is None
    assert r.require_participants is None
    assert r.require_mention is False


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def test_as_bool():
    assert _as_bool("true") is True
    assert _as_bool("YES") is True
    assert _as_bool("1") is True
    assert _as_bool("off") is False
    assert _as_bool(None, default=True) is True
    assert _as_bool(True) is True


def test_csv_set_from_string_and_list():
    assert _csv_set("+1, +2 ,+3") == {"+1", "+2", "+3"}
    assert _csv_set(["+1", " +2 "]) == {"+1", "+2"}
    assert _csv_set(None) == set()
    assert _csv_set("a\nb,c") == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# _redact() — phone/email/home-path/GUID sentinel tests
# ---------------------------------------------------------------------------

def test_redact_phone_and_email():
    assert _redact("call +12025550100 or alice@example.com") == "call [REDACTED] or [REDACTED]"


def test_redact_home_path_macos():
    assert _redact("state at /Users/exampleuser/.hermes/imsg_state.json") == (
        "state at [REDACTED_PATH]/.hermes/imsg_state.json"
    )


def test_redact_home_path_linux():
    assert _redact("db at /home/exampleuser/chat.db") == "db at [REDACTED_PATH]/chat.db"


def test_redact_standard_uuid_guid():
    assert _redact(
        "message 550e8400-e29b-41d4-a716-446655440000 failed"
    ) == "message [REDACTED_GUID] failed"


def test_redact_poll_option_prefixed_guid():
    assert _redact("poll p:0/550e8400-e29b-41d4-a716-446655440000") == "poll [REDACTED_GUID]"


def test_redact_chat_guid_shape():
    assert _redact("routed to iMessage;+;chat123 unexpectedly") == (
        "routed to [REDACTED_GUID] unexpectedly"
    )


def test_redact_combines_all_patterns_in_one_string():
    text = (
        "sender +12025550100 (alice@example.com) in chat iMessage;-;abc "
        "guid 550e8400-e29b-41d4-a716-446655440000 at /Users/exampleuser/x"
    )
    redacted = _redact(text)
    for leaked in (
        "+12025550100", "alice@example.com", "iMessage;-;abc",
        "550e8400-e29b-41d4-a716-446655440000", "/Users/exampleuser",
    ):
        assert leaked not in redacted


def test_redact_handles_none_and_non_string_input():
    assert _redact(None) == ""
    assert _redact(ValueError("+12025550100")) == "[REDACTED]"
