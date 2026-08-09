"""Hermes-native async facade for the local OpenClaw ``imsg`` executable.

This is deliberately a thin CLI client, not an OpenClaw adapter: OpenClaw owns
``imsg``'s stable command/NDJSON contract while Hermes owns routing, policy,
state, and gateway integration in :mod:`hermes_imsg_cli.adapter`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Optional

from . import cli
from .attachments import validate_outbound_attachment_path
from .capabilities import ImsgCapabilities, ImsgCapabilityError
from .config import normalize_imsg_target
from .formatting import extract_markdown_format_runs
from .rules import _redact

Runner = Callable[[str, list[str]], Awaitable[tuple[int, str, str]]]
RpcRunner = Callable[[str, dict[str, Any]], Awaitable[tuple[int, str, str]]]


class ImsgCommandError(RuntimeError):
    """Structured, PII-redacted failure from an ``imsg`` subprocess."""

    def __init__(self, command: str, message: str, *, exit_code: Optional[int] = None):
        self.command = command
        self.exit_code = exit_code
        super().__init__(f"imsg {command}: {message}")


class ImsgClient:
    """Small async API for compatible public ``imsg`` command surfaces."""

    def __init__(
        self,
        binary: str = "imsg",
        *,
        runner: Runner = cli.run_imsg,
        rpc_runner: RpcRunner = cli.run_imsg_rpc,
        timeout: float = 30.0,
    ):
        self.binary = str(binary)
        self._runner = runner
        self._rpc_runner = rpc_runner
        self.timeout = float(timeout)

    async def _json(self, args: list[str]) -> list[Any]:
        command = args[0] if args else "command"
        try:
            code, stdout, stderr = await asyncio.wait_for(
                self._runner(self.binary, args), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            raise ImsgCommandError(command, f"timed out after {self.timeout:g}s") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ImsgCommandError(command, _redact(exc)) from exc
        if code != 0:
            detail = _redact(stderr or stdout or f"exited with status {code}")
            raise ImsgCommandError(command, detail, exit_code=code)
        try:
            return cli.parse_json_output(stdout)
        except cli.ImsgJSONError as exc:
            raise ImsgCommandError(command, str(exc), exit_code=code) from exc

    async def _one(self, args: list[str], *, required: bool = True) -> dict[str, Any]:
        values = await self._json(args)
        if not values:
            if required:
                raise ImsgCommandError(args[0], "returned no JSON object", exit_code=0)
            return {}
        value = values[0]
        if not isinstance(value, dict):
            raise ImsgCommandError(args[0], "returned a non-object JSON value", exit_code=0)
        return value

    async def _rpc_one(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "rpc")
        try:
            code, stdout, stderr = await asyncio.wait_for(
                self._rpc_runner(self.binary, request), timeout=self.timeout
            )
        except asyncio.TimeoutError as exc:
            raise ImsgCommandError(method, f"timed out after {self.timeout:g}s") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ImsgCommandError(method, _redact(exc)) from exc
        if code != 0:
            raise ImsgCommandError(method, _redact(stderr or stdout or f"exited with status {code}"), exit_code=code)
        try:
            values = cli.parse_json_output(stdout)
        except cli.ImsgJSONError as exc:
            raise ImsgCommandError(method, str(exc), exit_code=code) from exc
        if not values or not isinstance(values[0], dict):
            raise ImsgCommandError(method, "returned no JSON object", exit_code=code)
        response = values[0]
        if response.get("error"):
            error = response["error"]
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise ImsgCommandError(method, _redact(detail or "RPC request failed"), exit_code=0)
        result = response.get("result")
        if not isinstance(result, dict):
            raise ImsgCommandError(method, "returned a non-object result", exit_code=0)
        return result

    @staticmethod
    def _raise_if_mutation_failed(command: str, result: dict[str, Any]) -> None:
        if (
            result.get("ok") is False
            or result.get("success") is False
            or "error" in result
        ):
            detail = _redact(result.get("error") or "unspecified mutation error")
            raise ImsgCommandError(
                command, f"reported failure: {detail}", exit_code=0
            )

    async def _mutation_one(self, args: list[str]) -> dict[str, Any]:
        result = await self._one(args)
        self._raise_if_mutation_failed(args[0], result)
        return result

    async def _rpc_mutation_one(self, request: dict[str, Any]) -> dict[str, Any]:
        result = await self._rpc_one(request)
        self._raise_if_mutation_failed(str(request.get("method") or "rpc"), result)
        return result

    async def capabilities(self) -> ImsgCapabilities:
        return ImsgCapabilities.from_status(await self.status())

    async def _require_action(self, action: str) -> None:
        capabilities = await self.capabilities()
        try:
            capabilities.require_action(action)
        except ImsgCapabilityError as exc:
            raise ImsgCommandError(action, exc.detail) from exc

    @staticmethod
    def _resolved_send_target(
        target: str, service: Optional[str]
    ) -> tuple[str, str, Optional[str]]:
        raw = str(target or "").strip()
        lowered = raw.lower()
        prefixes = (
            "chat_id:",
            "chatid:",
            "chat:",
            "chat_guid:",
            "chatguid:",
            "guid:",
            "chat_identifier:",
            "chatidentifier:",
            "chatident:",
            "imessage:",
            "sms:",
            "auto:",
        )
        if lowered.startswith(prefixes):
            return cli.parse_send_target(raw, service=service)
        normalized, is_phone = normalize_imsg_target(raw)
        return cli.parse_send_target(
            normalized, is_phone=is_phone, service=service
        )

    async def chats(self, *, limit: int = 20, unread_only: bool = False) -> list[dict[str, Any]]:
        return await self._objects(cli.build_chats_args(limit, unread_only))

    async def group(self, chat_id: str) -> dict[str, Any]:
        return await self._one(cli.build_group_args(chat_id))

    async def history(
        self,
        chat_id: str,
        *,
        limit: int = 50,
        attachments: bool = True,
        convert_attachments: bool = False,
        participants: Optional[Iterable[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        return await self._objects(cli.build_history_args(
            chat_id,
            limit=limit,
            attachments=attachments,
            convert_attachments=convert_attachments,
            participants=participants,
            start=start,
            end=end,
        ))

    async def search(
        self, query: str, *, match: str = "contains", limit: int = 50
    ) -> list[dict[str, Any]]:
        return await self._objects(cli.build_search_args(query, match=match, limit=limit))

    async def status(self) -> dict[str, Any]:
        return await self._one(cli.build_status_args())

    async def send(
        self,
        target: str,
        text: str = "",
        *,
        file: Optional[str] = None,
        service: Optional[str] = None,
        region: Optional[str] = None,
        no_sms_fallback: bool = False,
    ) -> dict[str, Any]:
        if file:
            attachment_error = validate_outbound_attachment_path(file)
            if attachment_error:
                raise ImsgCommandError("send", _redact(attachment_error))
        target_flag, target_value, effective_service = self._resolved_send_target(
            target, service
        )
        formatted = extract_markdown_format_runs(text)
        formatting = formatted["ranges"]
        target_names = {
            "--chat-id": "chat_id",
            "--chat-guid": "chat_guid",
            "--chat-identifier": "chat_identifier",
            "--to": "to",
        }
        target_kwargs = {target_names[target_flag]: target_value}
        media = [(file, False)] if file else None
        plain_target = {
            "--chat-id": target_value,
            "--chat-guid": f"chat_guid:{target_value}",
            "--chat-identifier": f"chat_identifier:{target_value}",
            "--to": target_value,
        }[target_flag]
        legacy_args = cli.build_plain_send_args(
            plain_target,
            text,
            is_phone=target_flag == "--to",
            media_files=media,
            service=effective_service,
            region=region,
            no_sms_fallback=no_sms_fallback,
        )
        if file is not None or effective_service == "sms":
            return await self._mutation_one(legacy_args)
        if formatting:
            request = cli.build_send_rpc_request(
                "hermes-send",
                text=formatted["text"],
                service=effective_service or "auto",
                region=region,
                formatting=formatting,
                **target_kwargs,
            )
            return await self._rpc_mutation_one(request)
        capabilities = await self.capabilities()
        if capabilities.supports_bridge_rpc_method("send"):
            request = cli.build_send_rpc_request(
                "hermes-send",
                text=text,
                service=effective_service or "auto",
                region=region,
                transport="bridge",
                **target_kwargs,
            )
            return await self._rpc_mutation_one(request)
        return await self._mutation_one(legacy_args)

    async def mark_read(self, chat_id: str) -> bool:
        await self._one(cli.build_read_args(chat_id), required=False)
        return True

    async def typing(
        self, chat_id: str, *, duration: Optional[float] = None, stop: bool = False
    ) -> bool:
        await self._one(cli.build_typing_args(chat_id, duration, stop=stop), required=False)
        return True

    async def react(self, chat_id: str, reaction: str) -> dict[str, Any]:
        return await self._one(cli.build_react_args(chat_id, reaction), required=False)

    async def tapback(
        self,
        chat_guid: str,
        message_guid: str,
        kind: str,
        *,
        remove: bool = False,
        part_index: int = 0,
    ) -> dict[str, Any]:
        await self._require_action("react")
        return await self._mutation_one(
            cli.build_tapback_args(
                chat_guid,
                message_guid,
                kind,
                remove=remove,
                part_index=part_index,
            )
        )

    async def edit(
        self,
        chat_guid: str,
        message_guid: str,
        text: str,
        *,
        backwards_compatibility_message: Optional[str] = None,
        part_index: int = 0,
    ) -> dict[str, Any]:
        await self._require_action("edit")
        return await self._mutation_one(
            cli.build_edit_args(
                chat_guid,
                message_guid,
                text,
                backwards_compatibility_message=backwards_compatibility_message,
                part_index=part_index,
            )
        )

    async def unsend(
        self, chat_guid: str, message_guid: str, *, part_index: int = 0
    ) -> dict[str, Any]:
        await self._require_action("unsend")
        return await self._mutation_one(
            cli.build_unsend_args(
                chat_guid, message_guid, part_index=part_index
            )
        )

    async def send_rich(
        self,
        chat_guid: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
        effect: Optional[str] = None,
        part_index: int = 0,
        file: Optional[str] = None,
    ) -> dict[str, Any]:
        if file:
            attachment_error = validate_outbound_attachment_path(file)
            if attachment_error:
                raise ImsgCommandError("send-rich", _redact(attachment_error))
        action = "sendWithEffect" if effect else "reply"
        await self._require_action(action)
        formatted = extract_markdown_format_runs(text)
        return await self._mutation_one(
            cli.build_rich_send_args(
                chat_guid,
                formatted["text"],
                reply_to=reply_to,
                effect=effect,
                part_index=part_index,
                file_path=file,
                formatting=formatted["ranges"],
            )
        )

    async def send_attachment(
        self, chat_guid: str, file: str, *, as_voice: bool = False
    ) -> dict[str, Any]:
        attachment_error = validate_outbound_attachment_path(file)
        if attachment_error:
            raise ImsgCommandError("send-attachment", _redact(attachment_error))
        await self._require_action("upload-file")
        return await self._mutation_one(
            cli.build_send_attachment_args(chat_guid, file, as_voice=as_voice)
        )

    async def rename_group(
        self, chat_guid: str, display_name: str
    ) -> dict[str, Any]:
        await self._require_action("renameGroup")
        return await self._mutation_one(
            cli.build_chat_name_args(chat_guid, display_name)
        )

    async def set_group_icon(
        self, chat_guid: str, file: str
    ) -> dict[str, Any]:
        attachment_error = validate_outbound_attachment_path(file)
        if attachment_error:
            raise ImsgCommandError("chat-photo", _redact(attachment_error))
        await self._require_action("setGroupIcon")
        return await self._mutation_one(cli.build_chat_photo_args(chat_guid, file))

    async def add_participant(
        self, chat_guid: str, address: str
    ) -> dict[str, Any]:
        await self._require_action("addParticipant")
        return await self._mutation_one(
            cli.build_chat_member_args("add", chat_guid, address)
        )

    async def remove_participant(
        self, chat_guid: str, address: str
    ) -> dict[str, Any]:
        await self._require_action("removeParticipant")
        return await self._mutation_one(
            cli.build_chat_member_args("remove", chat_guid, address)
        )

    async def leave_group(self, chat_guid: str) -> dict[str, Any]:
        await self._require_action("leaveGroup")
        return await self._mutation_one(cli.build_chat_leave_args(chat_guid))

    async def _require_poll_capability(self, operation: str) -> None:
        """Fail before mutation unless the installed binary/bridge advertises polls."""
        action = {
            "send": "poll",
            "vote": "poll-vote",
            "unvote": "poll-unvote",
        }[operation]
        await self._require_action(action)

    async def _poll_one(self, args: list[str]) -> dict[str, Any]:
        return await self._mutation_one(args)

    async def send_poll(
        self,
        chat_id: str,
        question: str,
        options: Iterable[str],
        *,
        comment: Optional[str] = None,
        reply_to: Optional[str] = None,
        chat_guid: Optional[str] = None,
    ) -> dict[str, Any]:
        await self._require_poll_capability("send")
        target = {"chat_guid": chat_guid} if chat_guid else {"chat_id": chat_id}
        request = cli.build_poll_send_rpc_request(
            "hermes-poll-send",
            **target,
            question=question,
            options=options,
            comment=comment,
            reply_to=reply_to,
        )
        return await self._rpc_mutation_one(request)

    async def vote_poll(
        self,
        chat_id: str,
        poll_guid: str,
        *,
        option_id: Optional[str] = None,
        option_index: Optional[int] = None,
        option: Optional[str] = None,
        chat_guid: Optional[str] = None,
    ) -> dict[str, Any]:
        await self._require_poll_capability("vote")
        target = {"chat_guid": chat_guid} if chat_guid else {"chat_id": chat_id}
        return await self._poll_one(cli.build_poll_vote_args(
            "vote", **target, poll_guid=poll_guid, option_id=option_id,
            option_index=option_index, option=option,
        ))

    async def unvote_poll(
        self,
        chat_id: str,
        poll_guid: str,
        *,
        option_id: Optional[str] = None,
        option_index: Optional[int] = None,
        option: Optional[str] = None,
        chat_guid: Optional[str] = None,
    ) -> dict[str, Any]:
        await self._require_poll_capability("unvote")
        target = {"chat_guid": chat_guid} if chat_guid else {"chat_id": chat_id}
        return await self._poll_one(cli.build_poll_vote_args(
            "unvote", **target, poll_guid=poll_guid, option_id=option_id,
            option_index=option_index, option=option,
        ))

    async def _objects(self, args: list[str]) -> list[dict[str, Any]]:
        values = await self._json(args)
        objects: list[dict[str, Any]] = []
        for value in values:
            if not isinstance(value, dict):
                raise ImsgCommandError(args[0], "returned a non-object JSON value", exit_code=0)
            objects.append(value)
        return objects
