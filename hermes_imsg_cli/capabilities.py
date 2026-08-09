"""Per-action capability model for the optional ``imsg`` private bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ImsgCapabilityError(RuntimeError):
    """Raised before mutation when the installed bridge lacks an action."""

    def __init__(self, action: str, message: str):
        self.action = action
        self.detail = message
        super().__init__(f"imsg {action}: {message}")


@dataclass(frozen=True)
class ActionRequirement:
    methods: tuple[str, ...]
    selectors_any: tuple[str, ...] = ()


ACTION_REQUIREMENTS: dict[str, ActionRequirement] = {
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
    "poll": ActionRequirement(
        ("poll.send", "messages.poll.send"), ("pollPayloadMessage",)
    ),
    "poll-vote": ActionRequirement(
        ("poll.vote", "messages.poll.vote"), ("pollVoteMessage",)
    ),
    "poll-unvote": ActionRequirement(
        ("poll.unvote", "polls.unvote", "messages.poll.unvote"),
        ("pollVoteMessage",),
    ),
}

ACTION_NAMES = tuple(ACTION_REQUIREMENTS)


@dataclass(frozen=True)
class ImsgCapabilities:
    advanced_features: bool
    v2_ready: bool
    rpc_methods: frozenset[str]
    selectors: dict[str, bool]
    status_message: str = ""

    @classmethod
    def from_status(cls, status: Any) -> "ImsgCapabilities":
        payload = status if isinstance(status, dict) else {}
        raw_methods = payload.get("rpc_methods")
        methods = frozenset(
            entry.strip()
            for entry in (raw_methods if isinstance(raw_methods, list) else [])
            if isinstance(entry, str) and entry.strip()
        )
        raw_selectors = payload.get("selectors")
        selectors = {
            str(name): value
            for name, value in (
                raw_selectors.items() if isinstance(raw_selectors, dict) else []
            )
            if isinstance(value, bool)
        }
        raw_message = payload.get("message")
        status_message = raw_message.strip()[:500] if isinstance(raw_message, str) else ""
        return cls(
            advanced_features=payload.get("advanced_features") is True,
            v2_ready=payload.get("v2_ready") is True,
            rpc_methods=methods,
            selectors=selectors,
            status_message=status_message,
        )

    @property
    def private_bridge_ready(self) -> bool:
        return self.advanced_features and self.v2_ready

    def require_action(self, action: str) -> None:
        requirement = ACTION_REQUIREMENTS.get(str(action))
        if requirement is None:
            raise ImsgCapabilityError(str(action), "unsupported native action")
        if not self.private_bridge_ready:
            reason = self.status_message or (
                "private API bridge is unavailable; run imsg launch after enabling "
                "the required macOS bridge settings"
            )
            raise ImsgCapabilityError(action, reason)
        if not self.rpc_methods.intersection(requirement.methods):
            advertised = " or ".join(requirement.methods)
            raise ImsgCapabilityError(
                action,
                f"installed imsg does not advertise {advertised}; update imsg and refresh status",
            )
        if requirement.selectors_any and not any(
            self.selectors.get(selector) is True for selector in requirement.selectors_any
        ):
            selectors = " or ".join(requirement.selectors_any)
            raise ImsgCapabilityError(
                action,
                f"bridge selector {selectors} is unavailable; action was not attempted",
            )

    def supports_action(self, action: str) -> bool:
        try:
            self.require_action(action)
        except ImsgCapabilityError:
            return False
        return True

    def available_actions(self) -> tuple[str, ...]:
        return tuple(action for action in ACTION_NAMES if self.supports_action(action))
