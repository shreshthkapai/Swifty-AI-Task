"""Conversation drivers that execute corpus turns through the public harness runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from evals.fixtures import FixtureRegistry, RecordingDealer
from evals.observe import observe_turn
from evals.run import ObservedTurn
from evals.schema import CorpusTurn, Scenario
from webchat.harness.contracts import ResponseStrategy, TurnPlan
from webchat.harness.planning import PlanValidationError, PlanningEngine, TurnRequest
from webchat.harness.runtime import HarnessRuntime, TurnResult
from webchat.harness.state import ActionReference, ConversationState, PageContext
from webchat.providers.base import (
    PlanningOutputError,
    PlanningOutputErrorKind,
    PlanningProvider,
    PlanningProviderError,
    PlanningRequest,
    PlanningResult,
    ProviderUsage,
)


class ScriptedProvider:
    """Supply the current corpus planner decision, never downstream results."""

    def __init__(self) -> None:
        self.plan_value: TurnPlan | None = None
        self.requests: list[PlanningRequest] = []

    async def plan(self, request: PlanningRequest) -> PlanningResult:
        self.requests.append(request)
        if self.plan_value is None:
            raise RuntimeError("a deterministic corpus turn unexpectedly called the provider")
        return PlanningResult(
            self.plan_value, ProviderUsage(100, 20, 120), 1.0,
            "scripted", "corpus-plan",
        )


class CapturingProvider:
    """Record the provider's actual plan while preserving provider behaviour."""

    def __init__(self, delegate: PlanningProvider) -> None:
        self.delegate = delegate
        self.last_result: PlanningResult | None = None

    async def plan(self, request: PlanningRequest) -> PlanningResult:
        self.last_result = await self.delegate.plan(request)
        return self.last_result


@dataclass(slots=True)
class _ScenarioSession:
    state: ConversationState
    dealer: RecordingDealer
    provider: CapturingProvider
    runtime: HarnessRuntime
    page: PageContext | None


class ProviderConversationDriver:
    """Run isolated scenarios with a supplied planner and hermetic dealer fixture."""

    def __init__(
        self,
        registry: FixtureRegistry,
        provider_factory: Callable[[], PlanningProvider],
        *,
        scripted: bool = False,
    ) -> None:
        self._registry = registry
        self._provider_factory = provider_factory
        self._scripted = scripted
        self._sessions: dict[str, _ScenarioSession] = {}

    def _new_session(self, scenario: Scenario) -> _ScenarioSession:
        dealer = self._registry.build_dealer(scenario.dealer_fixture)
        provider = CapturingProvider(self._provider_factory())
        counter = iter(range(1, 10_000))
        runtime = HarnessRuntime(
            dealer=dealer,
            planning=PlanningEngine(provider),
            id_factory=lambda: f"eval-{next(counter)}",
        )
        raw_page = scenario.initial_page
        page = None
        if raw_page:
            page = PageContext(
                current_url=f"http://localhost:4173{raw_page.get('path', '/')}",
                page_vehicle_id=raw_page.get("vehicle_id"),
                observed_at=self._registry.now,
            )
        return _ScenarioSession(
            self._registry.build_state(scenario.initial_state_fixture),
            dealer, provider, runtime, page,
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
            scripted_provider = session.provider.delegate
            if not isinstance(scripted_provider, ScriptedProvider):
                raise TypeError("scripted driver requires ScriptedProvider")
            scripted_provider.plan_value = turn.scripted_plan
        session.provider.last_result = None
        before_state = session.state
        before_dealer = session.dealer.snapshot()
        action = None
        current_input: str | None = turn.input.value
        if turn.input.kind == "action":
            action = self._resolve_action(turn, session.state)
            current_input = None
        request = TurnRequest(
            current_input=current_input,
            action_reference=action,
            state=session.state,
            now=self._registry.now,
            page_observation=session.page,
        )
        try:
            result = await session.runtime.handle(request)
        except PlanningOutputError as exc:
            return ObservedTurn(planner_failure=exc.kind.value)
        except PlanValidationError:
            return ObservedTurn(
                planner_failure=PlanningOutputErrorKind.INVALID_PLAN.value
            )
        except PlanningProviderError as exc:
            failure = getattr(exc, "kind", type(exc).__name__)
            return ObservedTurn(provider_failure=str(failure))
        session.state = result.state
        actual_plan = (
            session.provider.last_result.plan
            if session.provider.last_result is not None
            else None
        )
        commands = actual_plan.commands if actual_plan is not None else ()
        strategy = _observed_strategy(
            result,
            planned=actual_plan.response_strategy if actual_plan is not None else None,
        )
        return observe_turn(
            result,
            before_state=before_state,
            before_dealer=before_dealer,
            after_dealer=session.dealer.snapshot(),
            commands=commands,
            planning_strategy=strategy,
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
                entity = None
                if target.isdigit():
                    entity = group.resolve_ordinal(int(target))
                else:
                    entity = next((item for item in group.entities if item.entity_id == target), None)
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
        super().__init__(registry, ScriptedProvider, scripted=True)


def _observed_strategy(
    result: TurnResult,
    *,
    planned: ResponseStrategy | None,
) -> ResponseStrategy:
    kinds = {block.kind for block in result.blocks}
    codes = {
        block.to_dict()["payload"].get("code")
        for block in result.blocks if block.kind == "notice"
    }
    route = result.deterministic_route.value if result.deterministic_route else ""
    if route == "domain_redirect":
        return ResponseStrategy.DOMAIN_REDIRECT
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
