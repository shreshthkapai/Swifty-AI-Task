"""Conversation drivers that execute corpus turns through the thin runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from evals.fixtures import FixtureRegistry, RecordingDealer
from evals.observe import observe_turn
from evals.run import ObservedTurn
from evals.schema import CorpusTurn, ResponseStrategy, Scenario, ScriptedDecision
from webchat.harness.contracts import (
    HarnessCommand,
    PreparationCommand,
    ReadCommand,
)
from webchat.harness.conversation import (
    ConversationRequest,
    ConversationResult,
    ConversationToolCall,
    ConversationUsage,
)
from webchat.harness.conversation_runtime import ConversationRuntime
from webchat.harness.conversation_tools import conversation_command
from webchat.harness.state import ActionReference, ConversationState, PageContext
from webchat.harness.turn import TurnRequest, TurnResult
from webchat.providers.base import ConversationProvider, ConversationProviderError


_SCRIPTED_USAGE = ConversationUsage(100, 20, 120)


class ScriptedConversationProvider:
    """Turn valid corpus plans into model-shaped tool calls without faking dealer results."""

    def __init__(self) -> None:
        self.plan_value: ScriptedDecision | None = None
        self.requests: list[ConversationRequest] = []

    async def converse(self, request: ConversationRequest, *, on_text_delta=None):
        self.requests.append(request)
        if request.exchange is not None:
            if "family" in request.current_input.casefold():
                return self._answer(
                    "I found current stock below. For a family car, compare passenger space, "
                    "child-seat access, boot capacity, and everyday running costs."
                )
            return self._answer("I found current dealership information. The results are below.")
        plan = self.plan_value
        if plan is None:
            if "second" in request.current_input.casefold():
                return ConversationResult(
                    tool_calls=(ConversationToolCall(
                        "scripted-select",
                        "select_presented_entity",
                        {"ordinal": 2},
                    ),),
                    continuation="scripted-output-items",
                    usage=_SCRIPTED_USAGE,
                    latency_ms=1,
                    provider="scripted",
                    model="corpus-conversation",
                )
            return self._answer(
                "I can help with vehicles, test drives, sales, servicing, and dealership information."
            )
        if not plan.commands:
            if plan.response_strategy is ResponseStrategy.DOMAIN_REDIRECT:
                text = "I can help with choosing, buying, or servicing a vehicle."
            elif plan.response_strategy is ResponseStrategy.MISSING_INFORMATION:
                text = plan.clarification_question or "What would you like me to narrow down?"
            elif plan.response_strategy is ResponseStrategy.GENERAL_GUIDANCE:
                text = (
                    "For a family of five, compare passenger space, child-seat access, boot "
                    "capacity, and running costs. I can show suitable current stock."
                )
            else:
                text = "I can help with that and use dealership data when current facts are needed."
            return self._answer(text)
        calls = tuple(
            ConversationToolCall(
                f"scripted-{index}",
                command.name.value,
                command.to_dict()["arguments"],
            )
            for index, command in enumerate(plan.commands, start=1)
        )
        return ConversationResult(
            tool_calls=calls,
            continuation="scripted-output-items",
            usage=_SCRIPTED_USAGE,
            latency_ms=1,
            provider="scripted",
            model="corpus-conversation",
        )

    @staticmethod
    def _answer(text: str) -> ConversationResult:
        return ConversationResult(
            text=text,
            usage=_SCRIPTED_USAGE,
            latency_ms=1,
            time_to_first_token_ms=1,
            provider="scripted",
            model="corpus-conversation",
        )


class CapturingConversationProvider:
    def __init__(self, delegate: ConversationProvider) -> None:
        self.delegate = delegate
        self.calls: list[ConversationToolCall] = []

    async def converse(self, request, *, on_text_delta=None):
        result = await self.delegate.converse(
            request,
            on_text_delta=on_text_delta,
        )
        self.calls.extend(result.tool_calls)
        return result


@dataclass(slots=True)
class _ScenarioSession:
    state: ConversationState
    dealer: RecordingDealer
    provider: CapturingConversationProvider
    runtime: ConversationRuntime
    page: PageContext | None


class ProviderConversationDriver:
    """Run isolated scenarios against one conversation provider and hermetic dealer."""

    def __init__(
        self,
        registry: FixtureRegistry,
        provider_factory: Callable[[], ConversationProvider],
        *,
        scripted: bool = False,
    ) -> None:
        self._registry = registry
        self._provider_factory = provider_factory
        self._scripted = scripted
        self._sessions: dict[str, _ScenarioSession] = {}

    def _new_session(self, scenario: Scenario) -> _ScenarioSession:
        dealer = self._registry.build_dealer(scenario.dealer_fixture)
        provider = CapturingConversationProvider(self._provider_factory())
        counter = iter(range(1, 10_000))
        runtime = ConversationRuntime(
            dealer=dealer,
            conversation=provider,
            id_factory=lambda: f"eval-{next(counter)}",
        )
        raw_page = scenario.initial_page
        page = None
        if raw_page:
            page = PageContext(
                current_url=raw_page.get("path", "/"),
                page_vehicle_id=raw_page.get("vehicle_id"),
                observed_at=self._registry.now,
            )
        return _ScenarioSession(
            self._registry.build_state(scenario.initial_state_fixture),
            dealer,
            provider,
            runtime,
            page,
        )

    def _session(self, scenario: Scenario) -> _ScenarioSession:
        session = self._sessions.get(scenario.id)
        if session is None:
            session = self._new_session(scenario)
            self._sessions[scenario.id] = session
        return session

    async def execute_turn(self, scenario: Scenario, turn: CorpusTurn) -> ObservedTurn:
        session = self._session(scenario)
        if self._scripted:
            scripted = session.provider.delegate
            if not isinstance(scripted, ScriptedConversationProvider):
                raise TypeError("scripted driver requires ScriptedConversationProvider")
            scripted.plan_value = turn.scripted_plan
        session.provider.calls.clear()
        before_state = session.state
        before_dealer = session.dealer.snapshot()
        action = None
        current_input: str | None = turn.input.value
        if turn.input.kind == "action":
            action = self._resolve_action(turn, session.state)
            current_input = None
        try:
            result = await session.runtime.handle(TurnRequest(
                current_input=current_input,
                action_reference=action,
                state=session.state,
                now=self._registry.now,
                page_observation=session.page,
            ))
        except ConversationProviderError as exc:
            return ObservedTurn(provider_failure=exc.kind.value)
        if result.provider_failure is not None:
            return ObservedTurn(provider_failure=result.provider_failure)
        session.state = result.state
        commands = _captured_commands(session.provider.calls)
        planned = (
            turn.scripted_plan.response_strategy
            if turn.scripted_plan
            else (
                ResponseStrategy.DOMAIN_REDIRECT
                if turn.input.kind == "message" and "second" not in turn.input.value.casefold()
                else None
            )
        )
        return observe_turn(
            result,
            before_state=before_state,
            before_dealer=before_dealer,
            after_dealer=session.dealer.snapshot(),
            commands=commands,
            response_strategy=_observed_strategy(result, planned=planned),
            current_input=turn.input.value,
        )

    @staticmethod
    def _resolve_action(turn: CorpusTurn, state: ConversationState) -> ActionReference:
        value = turn.input.value
        reference = turn.input.reference or value
        if reference == "pending-action":
            action_id = state.pending_action.action_id if state.pending_action else reference
            return ActionReference(action_id, value)
        if value == "show_more":
            return ActionReference(reference, "show_more")
        if value == "select":
            target = reference.rsplit(":", 1)[-1]
            for group in reversed(state.presentation_groups):
                entity = (
                    group.resolve_ordinal(int(target))
                    if target.isdigit()
                    else next(
                        (item for item in group.entities if item.entity_id == target),
                        None,
                    )
                )
                if entity is None:
                    continue
                action_type = {
                    "vehicle": "select_vehicle",
                    "test_drive_slot": "select_test_drive_slot",
                    "workshop_slot": "select_workshop_slot",
                }[group.entity_type]
                action_id = entity.to_dict()["snapshot"].get("action_id", reference)
                return ActionReference(action_id, action_type)
        return ActionReference(reference, value)


class ScriptedConversationDriver(ProviderConversationDriver):
    def __init__(self, registry: FixtureRegistry) -> None:
        super().__init__(registry, ScriptedConversationProvider, scripted=True)


def _captured_commands(calls: list[ConversationToolCall]) -> tuple[HarnessCommand, ...]:
    commands = []
    for call in calls:
        command = conversation_command(call)
        if isinstance(command, (ReadCommand, PreparationCommand)):
            commands.append(command)
    return tuple(commands)


def _observed_strategy(
    result: TurnResult,
    *,
    planned: ResponseStrategy | None,
) -> ResponseStrategy:
    kinds = {block.kind for block in result.blocks}
    codes = {
        block.to_dict()["payload"].get("code")
        for block in result.blocks
        if block.kind == "notice"
    }
    if "missing_information" in codes:
        return ResponseStrategy.MISSING_INFORMATION
    if codes & {
        "booking_cancelled",
        "idempotency_conflict",
        "no_vehicle_results",
        "no_workshop_slots",
        "retry_not_allowed",
        "slot_unavailable",
        "temporary_failure",
        "verification_failed",
        "verification_required",
    }:
        return ResponseStrategy.RECOVERY
    if "confirmation" in kinds:
        return ResponseStrategy.ACTION_PREPARED
    if "vehicle_details" in kinds:
        return ResponseStrategy.VEHICLE_DETAILS
    if "vehicle_cards" in kinds:
        return planned or ResponseStrategy.SEARCH_RESULTS
    if "slot_choices" in kinds:
        return planned or ResponseStrategy.SLOT_RESULTS
    return planned or ResponseStrategy.ACKNOWLEDGEMENT
