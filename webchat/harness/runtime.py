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
    ResponseMode,
    ResponseStrategy,
)
from .evidence import EvidenceEnvelope, EvidenceGapReason, EvidenceRecord, EvidenceReference
from .grounded_response import (
    GroundedResponseRequest,
    GroundedResponseState,
    allowed_actions_from_blocks,
    assemble_evidence,
    focusable_entity_ids_from_blocks,
)
from .grounded_validation import (
    GroundedRepairContext,
    GroundedResponseValidationError,
    GroundedResponseValidator,
    GroundedValidationCode,
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
from webchat.providers.base import (
    GroundedResponseOutputError,
    GroundedResponseProvider,
    GroundedResponseProviderError,
    GroundedResponseResult,
    PlanningOutputError,
    PlanningProviderError,
)


def _random_id() -> str:
    return secrets.token_urlsafe(18)


SUPPORTED_UI_ACTION_TYPES = frozenset(
    {
        "cancel",
        "confirm",
        "find_test_drive_slots",
        "register_interest",
        "reselect_slot",
        "retry",
        "sales_enquiry",
        "select_dealership",
        "select_test_drive_slot",
        "select_vehicle",
        "select_workshop_slot",
        "show_more",
        "start_over",
        "switch_workflow",
    }
)


@dataclass(frozen=True, slots=True)
class TurnResult:
    state: ConversationState
    blocks: tuple[MessageBlock, ...]
    evidence: tuple[EvidenceRecord, ...] = ()
    executed_commands: tuple[str, ...] = ()
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    provider_latency_ms: float = 0
    deterministic_route: DeterministicRouteKind | None = None
    planner_failure: str | None = None
    provider_failure: str | None = None


@dataclass(frozen=True, slots=True)
class _GroundedOutcome:
    blocks: tuple[MessageBlock, ...]
    model_calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0
    failure: str | None = None
    focused_entity_id: str | None = None


def _state_for_turn(request: TurnRequest) -> ConversationState:
    observation = request.page_observation
    if observation is None:
        return request.state
    entities = request.state.entities
    page_vehicle_id = observation.page_vehicle_id
    if page_vehicle_id is not None and page_vehicle_id != entities.selected_vehicle_id:
        entities = replace(
            entities,
            selected_vehicle_id=page_vehicle_id,
            selected_dealer_id=None,
            selected_test_drive_slot_id=None,
        )
    return replace(request.state, context=observation, entities=entities)


class HarnessRuntime:
    """Execute one planning decision, bounded command batch, and state reduction."""

    def __init__(
        self,
        *,
        dealer: DealerAdapter,
        planning: PlanningEngine,
        grounded_response: GroundedResponseProvider,
        id_factory: Callable[[], str] = _random_id,
        policy: PolicyEngine | None = None,
        renderer: DeclarativeRenderer | None = None,
        response_validator: GroundedResponseValidator | None = None,
    ) -> None:
        self._dealer = dealer
        self._planning = planning
        self._grounded_response = grounded_response
        self._id_factory = id_factory
        self._policy = policy or PolicyEngine()
        self._renderer = renderer or DeclarativeRenderer(id_factory=id_factory)
        self._response_validator = response_validator or GroundedResponseValidator()

    async def handle(self, request: TurnRequest) -> TurnResult:
        turn_state = _state_for_turn(request)
        turn_request = replace(request, state=turn_state)
        try:
            decision = await self._planning.decide(turn_request)
        except PlanningOutputError as exc:
            return TurnResult(
                state=turn_state,
                blocks=(self._provider_recovery(),),
                model_calls=1,
                planner_failure=exc.kind.value,
            )
        except PlanningProviderError as exc:
            return TurnResult(
                state=turn_state,
                blocks=(self._provider_recovery(),),
                model_calls=1,
                provider_failure=exc.kind.value,
            )
        if decision.deterministic_route is not None:
            outcome = await self._execute_route(
                decision.deterministic_route,
                request=turn_request,
            )
            return TurnResult(
                state=outcome.state,
                blocks=outcome.blocks,
                evidence=outcome.evidence,
                deterministic_route=decision.deterministic_route.kind,
            )

        planning_result = decision.planning_result
        plan = planning_result.plan
        if not plan.commands:
            blocks = self._render_direct(
                plan.response_strategy,
                plan.response_mode,
                plan.clarification_question,
            )
            response = None
            if plan.response_mode is ResponseMode.GROUNDED_ANSWER:
                response = await self._respond(
                    question=request.current_input,
                    state=turn_state,
                    evidence=(),
                    blocks=blocks,
                    now=request.now,
                )
                blocks = response.blocks
            return TurnResult(
                state=turn_state,
                blocks=blocks,
                model_calls=1 + (0 if response is None else response.model_calls),
                input_tokens=planning_result.usage.input_tokens + (
                    0 if response is None else response.input_tokens
                ),
                output_tokens=planning_result.usage.output_tokens + (
                    0 if response is None else response.output_tokens
                ),
                provider_latency_ms=planning_result.latency_ms + (
                    0 if response is None else response.latency_ms
                ),
                provider_failure=None if response is None else response.failure,
            )

        state = self._supersede_for_switch(turn_state, plan.commands)
        blocks: list[MessageBlock] = []
        evidence: list[EvidenceRecord] = []
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
            evidence.extend(outcome.evidence)
        response = None
        final_blocks = tuple(blocks)
        if plan.response_mode is ResponseMode.GROUNDED_ANSWER and evidence:
            response = await self._respond(
                question=request.current_input,
                state=state,
                evidence=tuple(evidence),
                blocks=final_blocks,
                now=request.now,
            )
            final_blocks = response.blocks
            state, final_blocks = self._apply_response_focus(
                state,
                final_blocks,
                response.focused_entity_id,
            )
        return TurnResult(
            state=state,
            blocks=final_blocks,
            evidence=tuple(evidence),
            executed_commands=tuple(executed),
            model_calls=1 + (0 if response is None else response.model_calls),
            input_tokens=planning_result.usage.input_tokens + (
                0 if response is None else response.input_tokens
            ),
            output_tokens=planning_result.usage.output_tokens + (
                0 if response is None else response.output_tokens
            ),
            provider_latency_ms=planning_result.latency_ms + (
                0 if response is None else response.latency_ms
            ),
            provider_failure=None if response is None else response.failure,
        )

    async def _respond(
        self,
        *,
        question: str | None,
        state: ConversationState,
        evidence: tuple[EvidenceRecord, ...],
        blocks: tuple[MessageBlock, ...],
        now: datetime,
    ) -> _GroundedOutcome:
        if question is None:
            raise ValueError("grounded responses require the customer's current question")
        envelope = assemble_evidence(evidence, generated_at=now)
        request = GroundedResponseRequest(
            question=question,
            state=GroundedResponseState.from_conversation(state),
            evidence=envelope,
            missing_facts=tuple(
                EvidenceReference(item.evidence_id)
                for item in envelope.gaps
                if item.reason in {
                    EvidenceGapReason.NOT_PUBLISHED,
                    EvidenceGapReason.REDACTED,
                }
            ),
            allowed_actions=allowed_actions_from_blocks(blocks),
            focusable_entity_ids=focusable_entity_ids_from_blocks(blocks),
        )
        calls = 0
        input_tokens = 0
        output_tokens = 0
        latency_ms = 0.0
        last_violations = (GroundedValidationCode.INVALID_OUTPUT,)

        for attempt in range(2):
            calls += 1
            try:
                result = await self._grounded_response.respond(request)
            except GroundedResponseProviderError as exc:
                kind = exc.kind.value
                if exc.status_code is not None:
                    kind = f"{kind}:{exc.status_code}"
                prefix = "grounded_response" if attempt == 0 else "grounded_response:repair_failed"
                return _GroundedOutcome(
                    self._grounded_fallback(
                        envelope,
                        blocks,
                        repaired=attempt > 0,
                    ),
                    calls,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    f"{prefix}:{kind}",
                )
            except GroundedResponseOutputError:
                violations = (GroundedValidationCode.INVALID_OUTPUT,)
            else:
                if not isinstance(result, GroundedResponseResult):
                    violations = (GroundedValidationCode.INVALID_OUTPUT,)
                else:
                    input_tokens += result.usage.input_tokens
                    output_tokens += result.usage.output_tokens
                    latency_ms += result.latency_ms
                    try:
                        self._response_validator.validate(
                            request,
                            result.claims,
                            result.focused_entity_id,
                        )
                    except GroundedResponseValidationError as exc:
                        violations = tuple(
                            sorted({item.code for item in exc.issues})
                        )
                    else:
                        text = " ".join(claim.text.strip() for claim in result.claims)
                        return _GroundedOutcome(
                            (self._renderer.text(text),) + blocks,
                            calls,
                            input_tokens,
                            output_tokens,
                            latency_ms,
                            focused_entity_id=result.focused_entity_id,
                        )

            last_violations = violations
            if attempt == 0:
                request = replace(
                    request,
                    repair=GroundedRepairContext(violations),
                )
                continue

        failure = ",".join(item.value for item in last_violations)
        return _GroundedOutcome(
            self._grounded_fallback(envelope, blocks, repaired=True),
            calls,
            input_tokens,
            output_tokens,
            latency_ms,
            f"grounded_response:repair_failed:{failure}",
        )

    @staticmethod
    def _apply_response_focus(
        state: ConversationState,
        blocks: tuple[MessageBlock, ...],
        focused_entity_id: str | None,
    ) -> tuple[ConversationState, tuple[MessageBlock, ...]]:
        if focused_entity_id is None:
            return state, blocks
        matching_types = {
            reference.entity_type
            for block in blocks
            for reference in block.entity_references
            if reference.entity_id == focused_entity_id
        }
        if "vehicle" not in matching_types:
            return state, blocks
        focused_state = replace(
            state,
            entities=replace(
                state.entities,
                selected_vehicle_id=focused_entity_id,
            ),
        )
        decorated: list[MessageBlock] = []
        focused_orders: list[tuple[str, ...]] = []
        for block in blocks:
            if block.kind != "vehicle_cards" or not any(
                reference.entity_type == "vehicle"
                and reference.entity_id == focused_entity_id
                for reference in block.entity_references
            ):
                decorated.append(block)
                continue
            value = block.to_dict()
            payload = value["payload"]
            payload["recommended_entity_id"] = focused_entity_id
            vehicles = payload.get("vehicles", [])
            ordered_vehicles = sorted(
                vehicles,
                key=lambda item: item.get("id") != focused_entity_id,
            )
            payload["vehicles"] = ordered_vehicles
            ordered_ids = tuple(item["id"] for item in ordered_vehicles)
            focused_orders.append(ordered_ids)
            positions = {
                entity_id: index for index, entity_id in enumerate(ordered_ids)
            }
            payload["actions"] = sorted(
                payload.get("actions", []),
                key=lambda item: positions.get(item.get("entity_id"), len(positions)),
            )
            value["entity_references"] = sorted(
                value["entity_references"],
                key=lambda item: positions.get(item.get("entity_id"), len(positions)),
            )
            action_positions = {
                item.get("action_id"): index
                for index, item in enumerate(payload["actions"])
            }
            value["action_references"] = sorted(
                value["action_references"],
                key=lambda item: action_positions.get(
                    item.get("action_id"), len(action_positions)
                ),
            )
            decorated.append(MessageBlock.from_dict(value))
        groups = list(focused_state.presentation_groups)
        for ordered_ids in focused_orders:
            for index in range(len(groups) - 1, -1, -1):
                group = groups[index]
                existing = {item.entity_id: item for item in group.entities}
                if group.entity_type != "vehicle" or set(existing) != set(ordered_ids):
                    continue
                groups[index] = replace(
                    group,
                    entities=tuple(
                        replace(existing[entity_id], ordinal=ordinal)
                        for ordinal, entity_id in enumerate(ordered_ids, start=1)
                    ),
                )
                break
        focused_state = replace(focused_state, presentation_groups=tuple(groups))
        return focused_state, tuple(decorated)

    def _grounded_fallback(
        self,
        envelope: EvidenceEnvelope,
        blocks: tuple[MessageBlock, ...],
        *,
        repaired: bool,
    ) -> tuple[MessageBlock, ...]:
        if envelope.items:
            text = (
                "I couldn't verify a complete answer. The confirmed dealership "
                "information is shown below."
            )
            if envelope.gaps:
                field_names = tuple(sorted(
                    {item.field_name.replace("_", " ") for item in envelope.gaps}
                ))
                fields = ", ".join(field_names)
                verb = "is" if len(field_names) == 1 else "are"
                text = (
                    f"I couldn't verify a complete answer because {fields} {verb} not "
                    "confirmed in the dealership data. The confirmed dealership "
                    "information is shown below."
                )
        else:
            text = (
                "I couldn't verify a reliable answer from the available information "
                "just now."
            )
        code = "grounded_response_fallback" if repaired else "grounded_response_unavailable"
        return (self._renderer.notice(text, code=code),) + blocks

    async def _execute_command(self, command, *, state: ConversationState, now: datetime) -> CommandOutcome:
        arguments = command.to_dict()["arguments"]
        if isinstance(command, ReadCommand):
            handlers = (execute_vehicle_read, execute_sales_read, execute_workshop_read, execute_dealership_read)
            for handler in handlers:
                outcome = await handler(
                    self._dealer, command.name, arguments, state=state, now=now,
                    renderer=self._renderer, id_factory=self._id_factory,
                    policy=self._policy,
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
                (
                    self._renderer.text(
                        "I can help with vehicles, test drives, sales, servicing, and "
                        "dealership information."
                    ),
                ),
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

    def _render_direct(self, strategy, mode, clarification) -> tuple[MessageBlock, ...]:
        if mode is ResponseMode.CLARIFICATION:
            return (self._renderer.notice(clarification, code="missing_information"),)
        if strategy is ResponseStrategy.GENERAL_GUIDANCE:
            return (
                self._renderer.actions(
                    ((
                        "switch_workflow",
                        "Search current stock",
                        WorkflowDomain.VEHICLES.value,
                    ),)
                ),
            )
        if strategy is ResponseStrategy.DOMAIN_REDIRECT:
            return (self._renderer.text("I can help with vehicles, test drives, sales, servicing, and dealership information."),)
        return (self._renderer.notice("Understood.", code="acknowledgement"),)

    def _provider_recovery(self) -> MessageBlock:
        return self._renderer.notice(
            "I'm temporarily unable to work that out. Please try again.",
            code="provider_unavailable",
        )


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
