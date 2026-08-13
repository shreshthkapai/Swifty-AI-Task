"""Bounded dealership-harness orchestration with no recursive agent loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
import secrets
from typing import Any

from webchat.domain.dealer import DealerAdapter
from webchat.domain.errors import DealerError

from .actions import PendingActionState
from .contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
    ResponseStrategy,
)
from .planning import PlanningEngine, TurnRequest
from .policy import PolicyEngine
from .render import DeclarativeRenderer
from .scope import DeterministicRoute, DeterministicRouteKind
from .state import (
    ActionReference,
    ConversationState,
    EntityContext,
    MessageBlock,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)
from .workflows.common import CommandOutcome, dealer_recovery
from .workflows.dealerships import execute_dealership_preparation, execute_dealership_read
from .workflows.mutations import execute_confirmed_action
from .workflows.sales import execute_sales_preparation, execute_sales_read
from .workflows.vehicles import execute_vehicle_read
from .workflows.workshop import execute_workshop_preparation, execute_workshop_read


def _random_id() -> str:
    return secrets.token_urlsafe(18)


@dataclass(frozen=True, slots=True)
class TurnResult:
    state: ConversationState
    blocks: tuple[MessageBlock, ...]
    executed_commands: tuple[str, ...] = ()
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    provider_latency_ms: float = 0
    deterministic_route: DeterministicRouteKind | None = None


class HarnessRuntime:
    """Execute one planning decision, bounded command batch, and state reduction."""

    def __init__(
        self,
        *,
        dealer: DealerAdapter,
        planning: PlanningEngine,
        id_factory: Callable[[], str] = _random_id,
        policy: PolicyEngine | None = None,
        renderer: DeclarativeRenderer | None = None,
    ) -> None:
        self._dealer = dealer
        self._planning = planning
        self._id_factory = id_factory
        self._policy = policy or PolicyEngine()
        self._renderer = renderer or DeclarativeRenderer(id_factory=id_factory)

    async def handle(self, request: TurnRequest) -> TurnResult:
        decision = await self._planning.decide(request)
        turn_state = (
            request.state
            if request.page_observation is None
            else replace(request.state, context=request.page_observation)
        )
        if decision.deterministic_route is not None:
            outcome = await self._execute_route(
                decision.deterministic_route,
                request=replace(request, state=turn_state),
            )
            return TurnResult(
                state=outcome.state,
                blocks=outcome.blocks,
                deterministic_route=decision.deterministic_route.kind,
            )

        planning_result = decision.planning_result
        plan = planning_result.plan
        if not plan.commands:
            blocks = self._render_direct(plan.response_strategy, plan.clarification_question, plan.adjacent_advice)
            return TurnResult(
                state=turn_state,
                blocks=blocks,
                model_calls=1,
                input_tokens=planning_result.usage.input_tokens,
                output_tokens=planning_result.usage.output_tokens,
                provider_latency_ms=planning_result.latency_ms,
            )

        state = self._supersede_for_switch(turn_state, plan.commands)
        blocks: list[MessageBlock] = []
        executed: list[str] = []
        for command in plan.commands:
            executed.append(command.name.value)
            try:
                outcome = await self._execute_command(command, state=state, now=request.now)
            except DealerError as exc:
                outcome = dealer_recovery(state, exc.failure, renderer=self._renderer)
            except ValueError:
                outcome = CommandOutcome(
                    state,
                    (
                        self._renderer.notice(
                            "Those details cannot be used for this dealership request.",
                            code="invalid_request",
                        ),
                    ),
                )
            state = outcome.state
            blocks.extend(outcome.blocks)
        return TurnResult(
            state=state,
            blocks=tuple(blocks),
            executed_commands=tuple(executed),
            model_calls=1,
            input_tokens=planning_result.usage.input_tokens,
            output_tokens=planning_result.usage.output_tokens,
            provider_latency_ms=planning_result.latency_ms,
        )

    async def _execute_command(self, command, *, state: ConversationState, now: datetime) -> CommandOutcome:
        arguments = command.to_dict()["arguments"]
        if isinstance(command, ReadCommand):
            handlers = (execute_vehicle_read, execute_sales_read, execute_workshop_read, execute_dealership_read)
            for handler in handlers:
                outcome = await handler(
                    self._dealer, command.name, arguments, state=state, now=now,
                    renderer=self._renderer, id_factory=self._id_factory,
                )
                if outcome is not None:
                    return outcome
        elif isinstance(command, PreparationCommand):
            pending = state.pending_action
            if (
                pending is not None
                and not pending.is_expired(now)
                and pending.state
                in {
                    PendingActionState.AWAITING_CONFIRMATION,
                    PendingActionState.CONFIRMED,
                    PendingActionState.FAILED,
                }
            ):
                return CommandOutcome(
                    state,
                    (
                        self._renderer.notice(
                            "Confirm or cancel the current action before preparing another.",
                            code="pending_action_exists",
                        ),
                    ),
                )
            handlers = (execute_sales_preparation, execute_workshop_preparation, execute_dealership_preparation)
            for handler in handlers:
                outcome = await handler(
                    self._dealer, command.name, arguments, state=state, now=now,
                    renderer=self._renderer, id_factory=self._id_factory, policy=self._policy,
                )
                if outcome is not None:
                    return outcome
        raise ValueError(f"no handler for semantic command: {command.name.value}")

    async def _execute_route(self, route: DeterministicRoute, *, request: TurnRequest) -> CommandOutcome:
        state = request.state
        if route.kind is DeterministicRouteKind.DOMAIN_REDIRECT:
            return CommandOutcome(
                state,
                (self._renderer.text("I can help with vehicles, test drives, sales, servicing, and dealership information."),),
            )
        if route.kind is DeterministicRouteKind.CONFIRM_PENDING_ACTION:
            return await self._confirm(state, now=request.now)
        if route.kind is DeterministicRouteKind.CANCEL_PENDING_ACTION:
            return self._cancel(state)
        if route.kind is DeterministicRouteKind.RETRY_FAILED_ACTION:
            return await self._retry(state, now=request.now)
        if route.kind is DeterministicRouteKind.SHOW_MORE_RESULTS:
            return await self._show_more(state, now=request.now)
        if route.kind is DeterministicRouteKind.START_OVER:
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
        if route.kind is DeterministicRouteKind.SELECT_PRESENTED_ENTITY:
            return await self._select_presented(state, route.ordinal, now=request.now)
        if route.kind is DeterministicRouteKind.UI_ACTION:
            return await self._execute_ui_action(route.action_reference, request=request)
        raise ValueError(f"unsupported deterministic route: {route.kind.value}")

    async def _select_presented(
        self,
        state: ConversationState,
        ordinal: int,
        *,
        now: datetime,
    ) -> CommandOutcome:
        group = state.presentation_groups[-1]
        entity = group.resolve_ordinal(ordinal)
        if entity is None:
            return CommandOutcome(state, (self._renderer.notice("That option is no longer available.", code="invalid_selection"),))
        if group.entity_type == "vehicle":
            selected = replace(state, entities=replace(state.entities, selected_vehicle_id=entity.entity_id))
            return await self._execute_command(
                ReadCommand.from_mapping(ReadCommandName.GET_VEHICLE_DETAILS, {"vehicle_id": entity.entity_id}),
                state=selected, now=now,
            )
        if group.entity_type == "test_drive_slot":
            selected = replace(state, entities=replace(state.entities, selected_test_drive_slot_id=entity.entity_id))
            return await self._execute_command(
                PreparationCommand.from_mapping(PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING, {"slot_id": entity.entity_id}),
                state=selected, now=now,
            )
        if group.entity_type == "workshop_slot":
            selected = replace(state, entities=replace(state.entities, selected_workshop_slot_id=entity.entity_id))
            return await self._execute_command(
                PreparationCommand.from_mapping(PreparationCommandName.PREPARE_WORKSHOP_BOOKING, {"slot_id": entity.entity_id}),
                state=selected, now=now,
            )
        return CommandOutcome(state, (self._renderer.notice("Please use one of the available actions for that result.", code="unsupported_selection"),))

    async def _execute_ui_action(self, reference: ActionReference, *, request: TurnRequest) -> CommandOutcome:
        state = request.state
        if reference.action_type == "confirm":
            if state.pending_action is None or reference.action_id != state.pending_action.action_id:
                return CommandOutcome(state, (self._renderer.notice("That confirmation is no longer valid.", code="invalid_action_reference"),))
            return await self._confirm(state, now=request.now)
        if reference.action_type == "cancel":
            if state.pending_action is None or reference.action_id != state.pending_action.action_id:
                return CommandOutcome(state, (self._renderer.notice("That action is no longer available.", code="invalid_action_reference"),))
            return self._cancel(state)
        if reference.action_type == "retry":
            return await self._retry(state, now=request.now)
        if reference.action_type == "show_more":
            return await self._show_more(state, now=request.now)
        if reference.action_type == "start_over":
            return CommandOutcome(
                replace(state, entities=EntityContext(), workflow=WorkflowState(), pending_action=None),
                (self._renderer.notice("Started a new task.", code="workflow_reset"),),
            )

        entity = _resolve_presented_action(state, reference)
        if entity is None:
            entity = _resolve_message_action(request, reference)
        if entity is None:
            return CommandOutcome(state, (self._renderer.notice("That option is no longer available.", code="invalid_action_reference"),))

        if reference.action_type == "switch_workflow":
            try:
                domain = WorkflowDomain(entity)
            except ValueError:
                return CommandOutcome(state, (self._renderer.notice("That workflow is not available.", code="invalid_action_reference"),))
            if domain is WorkflowDomain.NONE:
                return CommandOutcome(state, (self._renderer.notice("That workflow is not available.", code="invalid_action_reference"),))
            next_state = replace(
                state,
                pending_action=None,
                entities=replace(
                    state.entities,
                    selected_test_drive_slot_id=None,
                    selected_workshop_slot_id=None,
                ),
                workflow=WorkflowState(domain, WorkflowStage.DISCOVERY),
            )
            return CommandOutcome(next_state, (self._renderer.notice("Switched tasks.", code="workflow_switched"),))

        if reference.action_type == "find_test_drive_slots":
            return await self._execute_command(
                ReadCommand.from_mapping(
                    ReadCommandName.FIND_TEST_DRIVE_SLOTS,
                    {"vehicle_id": entity},
                ),
                state=state,
                now=request.now,
            )
        if reference.action_type == "reselect_slot":
            if state.workflow.domain is WorkflowDomain.WORKSHOP:
                command = ReadCommand(ReadCommandName.FIND_WORKSHOP_SLOTS)
            else:
                command = ReadCommand.from_mapping(
                    ReadCommandName.FIND_TEST_DRIVE_SLOTS,
                    {"vehicle_id": entity},
                )
            return await self._execute_command(command, state=state, now=request.now)

        if reference.action_type == "select_vehicle":
            selected = replace(state, entities=replace(state.entities, selected_vehicle_id=entity))
            return await self._execute_command(
                ReadCommand.from_mapping(ReadCommandName.GET_VEHICLE_DETAILS, {"vehicle_id": entity}),
                state=selected, now=request.now,
            )
        if reference.action_type == "select_test_drive_slot":
            selected = replace(state, entities=replace(state.entities, selected_test_drive_slot_id=entity))
            return await self._execute_command(
                PreparationCommand.from_mapping(PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING, {"slot_id": entity}),
                state=selected, now=request.now,
            )
        if reference.action_type == "select_workshop_slot":
            selected = replace(state, entities=replace(state.entities, selected_workshop_slot_id=entity))
            return await self._execute_command(
                PreparationCommand.from_mapping(PreparationCommandName.PREPARE_WORKSHOP_BOOKING, {"slot_id": entity}),
                state=selected, now=request.now,
            )
        if reference.action_type == "select_dealership":
            selected = replace(state, entities=replace(state.entities, selected_dealer_id=entity))
            return CommandOutcome(selected, (self._renderer.notice("Dealership selected.", code="dealership_selected"),))
        if reference.action_type == "register_interest":
            return await self._execute_command(
                PreparationCommand.from_mapping(PreparationCommandName.PREPARE_VEHICLE_INTEREST, {"vehicle_id": entity}),
                state=state, now=request.now,
            )
        if reference.action_type == "sales_enquiry":
            return await self._execute_command(
                PreparationCommand.from_mapping(PreparationCommandName.PREPARE_SALES_ENQUIRY, {"vehicle_id": entity}),
                state=state, now=request.now,
            )
        return CommandOutcome(state, (self._renderer.notice("That action is not supported here.", code="invalid_action_reference"),))

    async def _confirm(self, state: ConversationState, *, now: datetime) -> CommandOutcome:
        action = state.pending_action
        if action is None:
            return CommandOutcome(state, (self._renderer.notice("There is no action waiting for confirmation.", code="no_pending_action"),))
        if action.state is PendingActionState.SUCCEEDED:
            return CommandOutcome(state, (self._renderer.notice("That action was already completed.", code="already_completed"),))
        if action.state is PendingActionState.FAILED:
            return CommandOutcome(state, (self._renderer.notice("That action failed. Use retry only if it is offered, or choose another option.", code="retry_required"),))
        if action.state is not PendingActionState.AWAITING_CONFIRMATION:
            return CommandOutcome(state, (self._renderer.notice("That action cannot be confirmed in its current state.", code="invalid_action_state"),))
        confirmed = replace(action, state=PendingActionState.CONFIRMED)
        return await execute_confirmed_action(
            self._dealer, state=replace(state, pending_action=confirmed), now=now,
            renderer=self._renderer, id_factory=self._id_factory, policy=self._policy,
        )

    def _cancel(self, state: ConversationState) -> CommandOutcome:
        if state.pending_action is None:
            return CommandOutcome(state, (self._renderer.notice("There is no pending action to cancel.", code="no_pending_action"),))
        return CommandOutcome(
            replace(
                state,
                pending_action=None,
                workflow=replace(state.workflow, stage=WorkflowStage.IDLE, missing_fields=()),
            ),
            (self._renderer.notice("Cancelled. Nothing was sent to the dealership.", code="action_cancelled"),),
        )

    async def _retry(self, state: ConversationState, *, now: datetime) -> CommandOutcome:
        action = state.pending_action
        if action is None or action.state is not PendingActionState.FAILED:
            return CommandOutcome(state, (self._renderer.notice("There is no failed action to retry.", code="nothing_to_retry"),))
        if action.last_failure is None or not action.last_failure.retryable:
            return CommandOutcome(state, (self._renderer.notice("That failure cannot be retried safely.", code="retry_not_allowed"),))
        return await execute_confirmed_action(
            self._dealer,
            state=replace(state, pending_action=replace(action, state=PendingActionState.CONFIRMED)),
            now=now, renderer=self._renderer, id_factory=self._id_factory, policy=self._policy,
        )

    async def _show_more(self, state: ConversationState, *, now: datetime) -> CommandOutcome:
        gathered = state.workflow.gathered_fields_dict()
        search = gathered.get("last_vehicle_search")
        if not isinstance(search, Mapping):
            return CommandOutcome(state, (self._renderer.notice("There are no more saved results to show.", code="no_more_results"),))
        arguments = dict(search)
        arguments["page"] = int(arguments.get("page") or 1) + 1
        return await self._execute_command(
            ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, arguments),
            state=state, now=now,
        )

    def _supersede_for_switch(self, state: ConversationState, commands) -> ConversationState:
        pending = state.pending_action
        if pending is None or pending.state not in {
            PendingActionState.AWAITING_CONFIRMATION,
            PendingActionState.CONFIRMED,
            PendingActionState.FAILED,
        }:
            return state
        target = _command_domain(commands[0].name)
        if target is state.workflow.domain or target is WorkflowDomain.NONE:
            return state
        return replace(state, pending_action=None)

    def _render_direct(self, strategy, clarification, advice) -> tuple[MessageBlock, ...]:
        if strategy is ResponseStrategy.MISSING_INFORMATION:
            return (self._renderer.notice(clarification, code="missing_information"),)
        if strategy is ResponseStrategy.ADJACENT_ADVICE:
            if self._policy.is_safe_adjacent_advice(advice):
                return (self._renderer.text(advice),)
            return (
                self._renderer.notice(
                    "I can offer general vehicle guidance, but current prices and availability must come from a dealership search.",
                    code="dealer_facts_require_lookup",
                ),
            )
        if strategy is ResponseStrategy.DOMAIN_REDIRECT:
            return (self._renderer.text("I can help with vehicles, test drives, sales, servicing, and dealership information."),)
        return (self._renderer.notice("Understood.", code="acknowledgement"),)


def _resolve_presented_action(state: ConversationState, reference: ActionReference) -> str | None:
    for group in reversed(state.presentation_groups):
        for item in group.entities:
            snapshot = item.to_dict()["snapshot"]
            if snapshot.get("action_id") == reference.action_id:
                expected = {
                    "vehicle": "select_vehicle",
                    "test_drive_slot": "select_test_drive_slot",
                    "workshop_slot": "select_workshop_slot",
                }.get(group.entity_type)
                if expected == reference.action_type:
                    return item.entity_id
    return None


def _resolve_message_action(request: TurnRequest, reference: ActionReference) -> str | None:
    for message in reversed(request.recent_messages):
        for block in message.blocks:
            payload = block.to_dict()["payload"]
            for action in payload.get("actions", []):
                if action.get("action_id") == reference.action_id and action.get("action_type") == reference.action_type:
                    return action.get("entity_id")
    return None


def _command_domain(name) -> WorkflowDomain:
    if name in {
        ReadCommandName.SEARCH_VEHICLES, ReadCommandName.GET_VEHICLE_DETAILS,
        ReadCommandName.COMPARE_VEHICLES, ReadCommandName.LIST_NEW_CAR_OFFERS,
    }:
        return WorkflowDomain.VEHICLES
    if name in {ReadCommandName.CHECK_VEHICLE_AVAILABILITY, PreparationCommandName.PREPARE_SALES_ENQUIRY,
                PreparationCommandName.PREPARE_VEHICLE_INTEREST, PreparationCommandName.PREPARE_CALLBACK,
                PreparationCommandName.PREPARE_PART_EXCHANGE}:
        return WorkflowDomain.SALES
    if name in {ReadCommandName.FIND_TEST_DRIVE_SLOTS, PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING}:
        return WorkflowDomain.TEST_DRIVE
    if name in {ReadCommandName.LIST_WORKSHOP_SERVICES, ReadCommandName.LIST_WORKSHOP_LOCATIONS,
                ReadCommandName.FIND_WORKSHOP_SLOTS, ReadCommandName.RETRIEVE_WORKSHOP_BOOKING,
                PreparationCommandName.PREPARE_WORKSHOP_BOOKING,
                PreparationCommandName.PREPARE_WORKSHOP_AMENDMENT,
                PreparationCommandName.PREPARE_WORKSHOP_CANCELLATION}:
        return WorkflowDomain.WORKSHOP
    if name in {ReadCommandName.LIST_DEALERSHIPS, ReadCommandName.GET_DEALERSHIP_DETAILS,
                ReadCommandName.GET_DEALERSHIP_HOURS, ReadCommandName.GET_BUSINESS_INFORMATION,
                PreparationCommandName.PREPARE_DEALERSHIP_MESSAGE}:
        return WorkflowDomain.DEALERSHIP
    return WorkflowDomain.NONE
