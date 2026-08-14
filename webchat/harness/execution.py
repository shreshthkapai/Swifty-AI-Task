"""Deterministic execution boundary for semantic dealership commands and trusted UI actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

from webchat.domain.dealer import DealerAdapter

from .actions import PendingActionState
from .contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
)
from .policy import PolicyEngine
from .render import DeclarativeRenderer
from .state import (
    ActionReference,
    ConversationState,
    EntityContext,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)
from .turn import TurnRequest
from .workflows.common import CommandOutcome
from .workflows.dealerships import execute_dealership_preparation, execute_dealership_read
from .workflows.mutations import execute_confirmed_action
from .workflows.sales import execute_sales_preparation, execute_sales_read
from .workflows.vehicles import execute_vehicle_read
from .workflows.workshop import execute_workshop_preparation, execute_workshop_read


SUPPORTED_UI_ACTION_TYPES = frozenset({
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
})


class CommandExecutor:
    """Execute already-interpreted commands; it does not understand customer language."""

    def __init__(
        self,
        *,
        dealer: DealerAdapter,
        id_factory: Callable[[], str],
        policy: PolicyEngine,
        renderer: DeclarativeRenderer,
    ) -> None:
        self._dealer = dealer
        self._id_factory = id_factory
        self._policy = policy
        self._renderer = renderer

    async def execute_command(
        self,
        command: ReadCommand | PreparationCommand,
        *,
        state: ConversationState,
        now: datetime,
    ) -> CommandOutcome:
        arguments = command.to_dict()["arguments"]
        if isinstance(command, ReadCommand):
            handlers = (
                execute_vehicle_read,
                execute_sales_read,
                execute_workshop_read,
                execute_dealership_read,
            )
            for handler in handlers:
                outcome = await handler(
                    self._dealer,
                    command.name,
                    arguments,
                    state=state,
                    now=now,
                    renderer=self._renderer,
                    id_factory=self._id_factory,
                    policy=self._policy,
                )
                if outcome is not None:
                    return outcome
        else:
            pending = state.pending_action
            if pending is not None and not pending.is_expired(now) and pending.state in {
                PendingActionState.AWAITING_CONFIRMATION,
                PendingActionState.CONFIRMED,
                PendingActionState.FAILED,
            }:
                return CommandOutcome(
                    state,
                    (self._renderer.notice(
                        "Confirm or cancel the current action before preparing another.",
                        code="pending_action_exists",
                    ),),
                )
            handlers = (
                execute_sales_preparation,
                execute_workshop_preparation,
                execute_dealership_preparation,
            )
            for handler in handlers:
                outcome = await handler(
                    self._dealer,
                    command.name,
                    arguments,
                    state=state,
                    now=now,
                    renderer=self._renderer,
                    id_factory=self._id_factory,
                    policy=self._policy,
                )
                if outcome is not None:
                    return outcome
        raise ValueError(f"no handler for semantic command: {command.name.value}")

    async def execute_ui_action(
        self,
        reference: ActionReference,
        *,
        request: TurnRequest,
    ) -> CommandOutcome:
        state = request.state
        if reference.action_type == "confirm":
            if state.pending_action is None or reference.action_id != state.pending_action.action_id:
                return self._notice(state, "That confirmation is no longer valid.", "invalid_action_reference")
            return await self.confirm(state, now=request.now)
        if reference.action_type == "cancel":
            if state.pending_action is None or reference.action_id != state.pending_action.action_id:
                return self._notice(state, "That action is no longer available.", "invalid_action_reference")
            return self.cancel(state)
        if reference.action_type == "retry":
            return await self.retry(state, now=request.now)
        if reference.action_type == "show_more":
            return await self.show_more(state, now=request.now)
        if reference.action_type == "start_over":
            return CommandOutcome(
                replace(state, entities=EntityContext(), workflow=WorkflowState(), pending_action=None),
                (self._renderer.notice("Started a new task.", code="workflow_reset"),),
            )

        entity = _resolve_presented_action(state, reference)
        if entity is None:
            entity = _resolve_message_action(request, reference)
        if entity is None:
            return self._notice(state, "That option is no longer available.", "invalid_action_reference")

        if reference.action_type == "switch_workflow":
            try:
                domain = WorkflowDomain(entity)
            except ValueError:
                return self._notice(state, "That workflow is not available.", "invalid_action_reference")
            if domain is WorkflowDomain.NONE:
                return self._notice(state, "That workflow is not available.", "invalid_action_reference")
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
            return self._notice(next_state, "Switched tasks.", "workflow_switched")
        if reference.action_type == "find_test_drive_slots":
            return await self.execute_command(
                ReadCommand.from_mapping(
                    ReadCommandName.FIND_TEST_DRIVE_SLOTS,
                    {"vehicle_id": entity},
                ),
                state=state,
                now=request.now,
            )
        if reference.action_type == "reselect_slot":
            command = (
                ReadCommand(ReadCommandName.FIND_WORKSHOP_SLOTS)
                if state.workflow.domain is WorkflowDomain.WORKSHOP
                else ReadCommand.from_mapping(
                    ReadCommandName.FIND_TEST_DRIVE_SLOTS,
                    {"vehicle_id": entity},
                )
            )
            return await self.execute_command(command, state=state, now=request.now)
        if reference.action_type == "select_vehicle":
            selected = replace(
                state,
                entities=replace(state.entities, selected_vehicle_id=entity),
            )
            return await self.execute_command(
                ReadCommand.from_mapping(
                    ReadCommandName.GET_VEHICLE_DETAILS,
                    {"vehicle_id": entity},
                ),
                state=selected,
                now=request.now,
            )
        if reference.action_type == "select_test_drive_slot":
            selected = replace(
                state,
                entities=replace(state.entities, selected_test_drive_slot_id=entity),
            )
            return await self.execute_command(
                PreparationCommand.from_mapping(
                    PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING,
                    {"slot_id": entity},
                ),
                state=selected,
                now=request.now,
            )
        if reference.action_type == "select_workshop_slot":
            selected = replace(
                state,
                entities=replace(state.entities, selected_workshop_slot_id=entity),
            )
            return await self.execute_command(
                PreparationCommand.from_mapping(
                    PreparationCommandName.PREPARE_WORKSHOP_BOOKING,
                    {"slot_id": entity},
                ),
                state=selected,
                now=request.now,
            )
        if reference.action_type == "select_dealership":
            selected = replace(
                state,
                entities=replace(state.entities, selected_dealer_id=entity),
            )
            return self._notice(selected, "Dealership selected.", "dealership_selected")
        preparations = {
            "register_interest": PreparationCommandName.PREPARE_VEHICLE_INTEREST,
            "sales_enquiry": PreparationCommandName.PREPARE_SALES_ENQUIRY,
        }
        if reference.action_type in preparations:
            return await self.execute_command(
                PreparationCommand.from_mapping(
                    preparations[reference.action_type],
                    {"vehicle_id": entity},
                ),
                state=state,
                now=request.now,
            )
        return self._notice(state, "That action is not supported here.", "invalid_action_reference")

    async def select_presented(
        self,
        state: ConversationState,
        ordinal: int,
        *,
        now: datetime,
    ) -> CommandOutcome:
        group = state.presentation_groups[-1]
        entity = group.resolve_ordinal(ordinal)
        if entity is None:
            return self._notice(state, "That option is no longer available.", "invalid_selection")
        if group.entity_type == "vehicle":
            selected = replace(state, entities=replace(state.entities, selected_vehicle_id=entity.entity_id))
            command = ReadCommand.from_mapping(
                ReadCommandName.GET_VEHICLE_DETAILS,
                {"vehicle_id": entity.entity_id},
            )
        elif group.entity_type == "test_drive_slot":
            selected = replace(state, entities=replace(state.entities, selected_test_drive_slot_id=entity.entity_id))
            command = PreparationCommand.from_mapping(
                PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING,
                {"slot_id": entity.entity_id},
            )
        elif group.entity_type == "workshop_slot":
            selected = replace(state, entities=replace(state.entities, selected_workshop_slot_id=entity.entity_id))
            command = PreparationCommand.from_mapping(
                PreparationCommandName.PREPARE_WORKSHOP_BOOKING,
                {"slot_id": entity.entity_id},
            )
        else:
            return self._notice(
                state,
                "Please use one of the available actions for that result.",
                "unsupported_selection",
            )
        return await self.execute_command(command, state=selected, now=now)

    async def confirm(self, state: ConversationState, *, now: datetime) -> CommandOutcome:
        action = state.pending_action
        if action is None:
            return self._notice(state, "There is no action waiting for confirmation.", "no_pending_action")
        if action.state is PendingActionState.SUCCEEDED:
            return self._notice(state, "That action was already completed.", "already_completed")
        if action.state is PendingActionState.FAILED:
            return self._notice(state, "That action failed. Retry it or choose another option.", "retry_required")
        if action.state is not PendingActionState.AWAITING_CONFIRMATION:
            return self._notice(state, "That action cannot be confirmed now.", "invalid_action_state")
        confirmed = replace(action, state=PendingActionState.CONFIRMED)
        return await execute_confirmed_action(
            self._dealer,
            state=replace(state, pending_action=confirmed),
            now=now,
            renderer=self._renderer,
            id_factory=self._id_factory,
            policy=self._policy,
        )

    def cancel(self, state: ConversationState) -> CommandOutcome:
        if state.pending_action is None:
            return self._notice(state, "There is no pending action to cancel.", "no_pending_action")
        return CommandOutcome(
            replace(
                state,
                pending_action=None,
                workflow=replace(state.workflow, stage=WorkflowStage.IDLE, missing_fields=()),
            ),
            (self._renderer.notice(
                "Cancelled. Nothing was sent to the dealership.",
                code="action_cancelled",
            ),),
        )

    async def retry(self, state: ConversationState, *, now: datetime) -> CommandOutcome:
        action = state.pending_action
        if action is None or action.state is not PendingActionState.FAILED:
            return self._notice(state, "There is no failed action to retry.", "nothing_to_retry")
        if action.last_failure is None or not action.last_failure.retryable:
            return self._notice(state, "That failure cannot be retried safely.", "retry_not_allowed")
        return await execute_confirmed_action(
            self._dealer,
            state=replace(state, pending_action=replace(action, state=PendingActionState.CONFIRMED)),
            now=now,
            renderer=self._renderer,
            id_factory=self._id_factory,
            policy=self._policy,
        )

    async def show_more(self, state: ConversationState, *, now: datetime) -> CommandOutcome:
        search = state.workflow.gathered_fields_dict().get("last_vehicle_search")
        if not isinstance(search, Mapping):
            return self._notice(state, "There are no more saved results to show.", "no_more_results")
        arguments = dict(search)
        arguments["page"] = int(arguments.get("page") or 1) + 1
        return await self.execute_command(
            ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, arguments),
            state=state,
            now=now,
        )

    @staticmethod
    def supersede_for_switch(
        state: ConversationState,
        commands: tuple[ReadCommand | PreparationCommand, ...],
    ) -> ConversationState:
        pending = state.pending_action
        if pending is None or pending.state not in {
            PendingActionState.AWAITING_CONFIRMATION,
            PendingActionState.CONFIRMED,
            PendingActionState.FAILED,
        }:
            return state
        target = command_domain(commands[0].name)
        if target is state.workflow.domain or target is WorkflowDomain.NONE:
            return state
        return replace(state, pending_action=None)

    def provider_recovery(self):
        return self._renderer.notice(
            "I'm temporarily unable to work that out. Please try again.",
            code="provider_unavailable",
        )

    def _notice(self, state: ConversationState, text: str, code: str) -> CommandOutcome:
        return CommandOutcome(state, (self._renderer.notice(text, code=code),))


def _resolve_presented_action(
    state: ConversationState,
    reference: ActionReference,
) -> str | None:
    for group in reversed(state.presentation_groups):
        for item in group.entities:
            snapshot = item.to_dict()["snapshot"]
            if snapshot.get("action_id") != reference.action_id:
                continue
            expected = {
                "vehicle": "select_vehicle",
                "test_drive_slot": "select_test_drive_slot",
                "workshop_slot": "select_workshop_slot",
            }.get(group.entity_type)
            if expected == reference.action_type:
                return item.entity_id
    return None


def _resolve_message_action(
    request: TurnRequest,
    reference: ActionReference,
) -> str | None:
    for message in reversed(request.recent_messages):
        for block in message.blocks:
            for action in block.to_dict()["payload"].get("actions", []):
                if (
                    action.get("action_id") == reference.action_id
                    and action.get("action_type") == reference.action_type
                ):
                    return action.get("entity_id")
    return None


def command_domain(name) -> WorkflowDomain:
    if name in {
        ReadCommandName.SEARCH_VEHICLES,
        ReadCommandName.GET_VEHICLE_DETAILS,
        ReadCommandName.COMPARE_VEHICLES,
        ReadCommandName.LIST_NEW_CAR_OFFERS,
    }:
        return WorkflowDomain.VEHICLES
    if name in {
        ReadCommandName.CHECK_VEHICLE_AVAILABILITY,
        PreparationCommandName.PREPARE_SALES_ENQUIRY,
        PreparationCommandName.PREPARE_VEHICLE_INTEREST,
        PreparationCommandName.PREPARE_CALLBACK,
        PreparationCommandName.PREPARE_PART_EXCHANGE,
    }:
        return WorkflowDomain.SALES
    if name in {
        ReadCommandName.FIND_TEST_DRIVE_SLOTS,
        PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING,
    }:
        return WorkflowDomain.TEST_DRIVE
    if name in {
        ReadCommandName.LIST_WORKSHOP_SERVICES,
        ReadCommandName.LIST_WORKSHOP_LOCATIONS,
        ReadCommandName.FIND_WORKSHOP_SLOTS,
        ReadCommandName.RETRIEVE_WORKSHOP_BOOKING,
        PreparationCommandName.PREPARE_WORKSHOP_BOOKING,
        PreparationCommandName.PREPARE_WORKSHOP_AMENDMENT,
        PreparationCommandName.PREPARE_WORKSHOP_CANCELLATION,
    }:
        return WorkflowDomain.WORKSHOP
    if name in {
        ReadCommandName.LIST_DEALERSHIPS,
        ReadCommandName.GET_DEALERSHIP_DETAILS,
        ReadCommandName.GET_DEALERSHIP_HOURS,
        ReadCommandName.GET_BUSINESS_INFORMATION,
        PreparationCommandName.PREPARE_DEALERSHIP_MESSAGE,
    }:
        return WorkflowDomain.DEALERSHIP
    return WorkflowDomain.NONE
