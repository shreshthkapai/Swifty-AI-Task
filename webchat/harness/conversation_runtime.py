"""Thin conversational orchestration with at most one semantic tool round."""

from __future__ import annotations

import asyncio
from dataclasses import fields, is_dataclass, replace
import json
import secrets
from typing import Any

from webchat.domain.dealer import DealerAdapter
from webchat.domain.errors import DealerError
from webchat.providers.base import (
    ConversationProvider,
    ConversationProviderError,
    TextDeltaCallback,
)

from .conversation import (
    ConversationRequest,
    ConversationResult,
    ConversationToolResult,
    ToolExchange,
)
from .conversation_tools import (
    ConversationCommand,
    ConversationControl,
    ConversationControlName,
    conversation_command,
    conversation_tool_catalogue,
)
from .contracts import (
    PreparationCommand,
    ReadCommand,
)
from .execution import CommandExecutor
from .policy import PolicyEngine
from .render import DeclarativeRenderer
from .turn import TurnRequest, TurnResult, state_for_turn
from .state import (
    ConversationState,
    EntityContext,
    MessageBlock,
    StructuredMessage,
    WorkflowState,
)
from .workflows.common import CommandOutcome, dealer_recovery


MAX_RECENT_MESSAGES = 8
MAX_PRESENTATION_GROUPS_IN_CONTEXT = 3


def _random_id() -> str:
    return secrets.token_urlsafe(18)


class ConversationRuntime(CommandExecutor):
    """Let one model own language while deterministic code owns execution safety."""

    def __init__(
        self,
        *,
        dealer: DealerAdapter,
        conversation: ConversationProvider,
        id_factory=_random_id,
        policy: PolicyEngine | None = None,
        renderer: DeclarativeRenderer | None = None,
    ) -> None:
        if not isinstance(conversation, ConversationProvider):
            raise ValueError("conversation must implement ConversationProvider")
        self._conversation = conversation
        selected_policy = policy or PolicyEngine()
        selected_renderer = renderer or DeclarativeRenderer(id_factory=id_factory)
        super().__init__(
            dealer=dealer,
            id_factory=id_factory,
            policy=selected_policy,
            renderer=selected_renderer,
        )

    async def handle(
        self,
        request: TurnRequest,
        *,
        on_text_delta: TextDeltaCallback | None = None,
    ) -> TurnResult:
        turn_state = state_for_turn(request)
        turn_request = replace(request, state=turn_state)
        if request.action_reference is not None:
            outcome = await self.execute_ui_action(
                request.action_reference,
                request=turn_request,
            )
            return TurnResult(state=outcome.state, blocks=outcome.blocks)

        context = _compile_context(turn_request)
        tools = conversation_tool_catalogue()
        initial_request = ConversationRequest(context=context, tools=tools)
        try:
            first = await self._conversation.converse(
                initial_request,
                on_text_delta=on_text_delta,
            )
        except ConversationProviderError as exc:
            return self._provider_failure(turn_state, exc.kind.value, calls=1)

        if first.text is not None:
            return TurnResult(
                state=turn_state,
                blocks=(self._renderer.text(first.text),),
                model_calls=1,
                input_tokens=first.usage.input_tokens,
                output_tokens=first.usage.output_tokens,
                provider_latency_ms=first.latency_ms,
            )

        try:
            commands = tuple(conversation_command(call) for call in first.tool_calls)
            _validate_batch(commands)
        except ValueError:
            return self._provider_failure(
                turn_state,
                "invalid_tool_call",
                calls=1,
                first=first,
            )

        dealer_commands = tuple(
            command for command in commands if not isinstance(command, ConversationControl)
        )
        execution_base = (
            self.supersede_for_switch(turn_state, dealer_commands)
            if dealer_commands
            else turn_state
        )
        state = execution_base
        blocks: list[MessageBlock] = []
        evidence = []
        results: list[ConversationToolResult] = []
        executed: list[str] = []
        terminal_execution = False
        if len(commands) > 1 and all(isinstance(item, ReadCommand) for item in commands):
            executed_outcomes = await asyncio.gather(
                *(
                    self._safe_execute(command, state=state, now=request.now)
                    for command in commands
                )
            )
            state = _merge_read_states(
                execution_base,
                tuple(outcome.state for outcome, _ in executed_outcomes),
            )
        else:
            executed_outcomes = []
            current_state = state
            for command in commands:
                outcome, is_error = await self._safe_execute(
                    command,
                    state=current_state,
                    now=request.now,
                )
                current_state = outcome.state
                executed_outcomes.append((outcome, is_error))
            state = current_state

        for call, command, (outcome, is_error) in zip(
            first.tool_calls,
            commands,
            executed_outcomes,
            strict=True,
        ):
            executed.append(call.name)
            blocks.extend(outcome.blocks)
            evidence.extend(outcome.evidence)
            results.append(
                ConversationToolResult(
                    call.call_id,
                    call.name,
                    _tool_output(outcome, is_error=is_error),
                    is_error=is_error,
                )
            )
            terminal_execution = terminal_execution or _is_terminal(command)

        if terminal_execution:
            return TurnResult(
                state=state,
                blocks=tuple(blocks),
                evidence=tuple(evidence),
                executed_commands=tuple(executed),
                model_calls=1,
                input_tokens=first.usage.input_tokens,
                output_tokens=first.usage.output_tokens,
                provider_latency_ms=first.latency_ms,
            )

        continuation_request = ConversationRequest(
            context=context,
            tools=tools,
            exchange=ToolExchange(
                first.tool_calls,
                tuple(results),
                continuation=first.continuation,
            ),
        )
        try:
            final = await self._conversation.converse(
                continuation_request,
                on_text_delta=on_text_delta,
            )
        except ConversationProviderError as exc:
            failure = self._provider_failure(
                state,
                exc.kind.value,
                calls=2,
                first=first,
                blocks=tuple(blocks),
            )
            return replace(
                failure,
                evidence=tuple(evidence),
                executed_commands=tuple(executed),
            )
        if final.tool_calls:
            failure = self._provider_failure(
                state,
                "tool_round_limit",
                calls=2,
                first=first,
                second=final,
                blocks=tuple(blocks),
            )
            return replace(
                failure,
                evidence=tuple(evidence),
                executed_commands=tuple(executed),
            )
        return TurnResult(
            state=state,
            blocks=(self._renderer.text(final.text),) + tuple(blocks),
            evidence=tuple(evidence),
            executed_commands=tuple(executed),
            model_calls=2,
            input_tokens=first.usage.input_tokens + final.usage.input_tokens,
            output_tokens=first.usage.output_tokens + final.usage.output_tokens,
            provider_latency_ms=first.latency_ms + final.latency_ms,
        )

    def _provider_failure(
        self,
        state: ConversationState,
        kind: str,
        *,
        calls: int,
        first: ConversationResult | None = None,
        second: ConversationResult | None = None,
        blocks: tuple[MessageBlock, ...] = (),
    ) -> TurnResult:
        results = tuple(item for item in (first, second) if item is not None)
        return TurnResult(
            state=state,
            blocks=(self.provider_recovery(),) + blocks,
            model_calls=calls,
            input_tokens=sum(item.usage.input_tokens for item in results),
            output_tokens=sum(item.usage.output_tokens for item in results),
            provider_latency_ms=sum(item.latency_ms for item in results),
            provider_failure=kind,
        )

    async def _safe_execute(
        self,
        command: ConversationCommand,
        *,
        state: ConversationState,
        now,
    ) -> tuple[CommandOutcome, bool]:
        try:
            if isinstance(command, ConversationControl):
                return await self._execute_control(command, state=state, now=now), False
            return await self.execute_command(command, state=state, now=now), False
        except DealerError as exc:
            return (
                dealer_recovery(state, exc.failure, renderer=self._renderer),
                True,
            )
        except ValueError:
            return (
                CommandOutcome(
                    state,
                    (
                        self._renderer.notice(
                            "Those details cannot be used for this dealership request.",
                            code="invalid_request",
                        ),
                    ),
                ),
                True,
            )

    async def _execute_control(
        self,
        control: ConversationControl,
        *,
        state: ConversationState,
        now,
    ) -> CommandOutcome:
        if control.name is ConversationControlName.CONFIRM_PENDING_ACTION:
            return await self.confirm(state, now=now)
        if control.name is ConversationControlName.CANCEL_PENDING_ACTION:
            return self.cancel(state)
        if control.name is ConversationControlName.RETRY_PENDING_ACTION:
            return await self.retry(state, now=now)
        if control.name is ConversationControlName.SHOW_MORE_RESULTS:
            return await self.show_more(state, now=now)
        if control.name is ConversationControlName.START_OVER:
            return CommandOutcome(
                replace(
                    state,
                    entities=EntityContext(),
                    preferences=type(state.preferences)(),
                    workflow=WorkflowState(),
                    pending_action=None,
                    presentation_groups=(),
                ),
                (self._renderer.notice("Started a new task.", code="workflow_reset"),),
            )
        if control.name is ConversationControlName.SELECT_PRESENTED_ENTITY:
            ordinal = control.arguments_dict().get("ordinal")
            if not state.presentation_groups:
                return CommandOutcome(
                    state,
                    (
                        self._renderer.notice(
                            "There are no current results to select from.",
                            code="invalid_selection",
                        ),
                    ),
                )
            return await self.select_presented(state, ordinal, now=now)
        raise ValueError(f"unsupported conversation control: {control.name.value}")


def _validate_batch(
    commands: tuple[ConversationCommand, ...],
) -> None:
    preparations = sum(isinstance(item, PreparationCommand) for item in commands)
    if preparations > 1:
        raise ValueError("a turn cannot prepare multiple consequential actions")
    if preparations and len(commands) > 1:
        raise ValueError("a preparation cannot be combined with another tool call")
    controls = sum(isinstance(item, ConversationControl) for item in commands)
    if controls and len(commands) > 1:
        raise ValueError("a control cannot be combined with another tool call")


def _is_terminal(command: ConversationCommand) -> bool:
    if isinstance(command, PreparationCommand):
        return True
    if not isinstance(command, ConversationControl):
        return False
    return command.name not in {
        ConversationControlName.SHOW_MORE_RESULTS,
        ConversationControlName.SELECT_PRESENTED_ENTITY,
    }


def _merge_read_states(
    base: ConversationState,
    states: tuple[ConversationState, ...],
) -> ConversationState:
    if not states:
        return base
    merged = states[0]
    for state in states[1:]:
        merged = _merge_value(base, merged, state, prefer_primary={"workflow"})
    return merged


def _merge_value(base, primary, secondary, *, prefer_primary: set[str]):
    if secondary == base or secondary == primary:
        return primary
    if primary == base:
        return secondary
    if is_dataclass(base) and type(base) is type(primary) is type(secondary):
        values = {}
        for item in fields(base):
            if item.name in prefer_primary:
                values[item.name] = getattr(primary, item.name)
                continue
            values[item.name] = _merge_value(
                getattr(base, item.name),
                getattr(primary, item.name),
                getattr(secondary, item.name),
                prefer_primary=prefer_primary,
            )
        return type(base)(**values)
    if isinstance(base, tuple) and isinstance(primary, tuple) and isinstance(secondary, tuple):
        combined = list(primary)
        for item in secondary:
            if item not in combined:
                combined.append(item)
        return tuple(combined)
    return primary


def _tool_output(outcome: CommandOutcome, *, is_error: bool) -> dict[str, Any]:
    entity_ids = tuple(
        dict.fromkeys(
            reference.entity_id
            for block in outcome.blocks
            for reference in block.entity_references
        )
    )
    notices = []
    for block in outcome.blocks:
        if block.kind not in {"text", "notice"}:
            continue
        payload = block.to_dict()["payload"]
        text = payload.get("text")
        if isinstance(text, str):
            notices.append({"code": payload.get("code"), "text": text})
    return {
        "status": "error" if is_error else "ok",
        "facts": [item.to_dict() for item in outcome.evidence],
        "entities": list(entity_ids),
        "notices": notices,
    }


def _compile_context(request: TurnRequest) -> str:
    state = request.state
    pending = state.pending_action
    payload = {
        "current_input": request.current_input,
        "current_time": request.now.isoformat(),
        "state": {
            "customer": state.customer.to_dict(),
            "page": state.context.to_dict(),
            "selected": {
                "vehicle_id": state.entities.selected_vehicle_id,
                "dealership_id": state.entities.selected_dealer_id,
                "test_drive_slot_id": state.entities.selected_test_drive_slot_id,
                "workshop_slot_id": state.entities.selected_workshop_slot_id,
            },
            "preferences": state.preferences.to_dict(),
            "workflow": state.workflow.to_dict(),
            "pending_action": None
            if pending is None
            else {
                "action_id": pending.action_id,
                "action_type": pending.action_type.value,
                "state": pending.state.value,
                "expires_at": pending.expires_at.isoformat(),
            },
            "verification_grants": [
                {
                    "booking_id": grant.booking_id,
                    "expires_at": grant.expires_at.isoformat(),
                }
                for grant in state.verification_grants
                if grant.is_active(request.now)
            ],
            "presented": [
                group.to_dict()
                for group in state.presentation_groups[-MAX_PRESENTATION_GROUPS_IN_CONTEXT:]
            ],
        },
        "recent_messages": [
            _message_context(message, state)
            for message in request.recent_messages[-MAX_RECENT_MESSAGES:]
        ],
        "prior_failures": [_failure_context(failure) for failure in request.prior_failures],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _message_context(
    message: StructuredMessage,
    state: ConversationState,
) -> dict[str, Any]:
    text = message.text
    if text is not None:
        for value, replacement in (
            (state.customer.email, "[known email]"),
            (state.customer.phone, "[known phone]"),
            (state.customer.registration, "[known registration]"),
        ):
            if value:
                text = text.replace(value, replacement)
    return {
        "role": message.role.value,
        "text": text,
        "blocks": [
            {
                "kind": block.kind,
                "entities": [item.to_dict() for item in block.entity_references],
                "actions": [item.action_type for item in block.action_references],
            }
            for block in message.blocks
        ],
    }


def _failure_context(failure) -> dict[str, Any]:
    return {
        "kind": failure.kind.value,
        "retryable": failure.retryable,
        "resource": failure.resource,
        "field_violations": [
            {
                "field": item.field,
                "code": item.code,
                "message": item.message,
            }
            for item in failure.field_violations
        ],
    }
