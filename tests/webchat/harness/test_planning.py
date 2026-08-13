import unittest
from datetime import UTC, datetime, timedelta

from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.contracts import (
    PreparationCommand,
    PreparationCommandName,
    ReadCommand,
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
    freeze_json_object,
)
from webchat.harness.planning import (
    PlanValidationError,
    PlanningEngine,
    TurnRequest,
    parse_planning_output,
)
from webchat.harness.scope import DeterministicRouteKind
from webchat.harness.state import (
    ActionReference,
    ConversationState,
    PageContext,
    WorkflowDomain,
    WorkflowStage,
    WorkflowState,
)
from webchat.providers.base import (
    PlanningRequest,
    PlanningProvider,
    PlanningResult,
    ProviderUsage,
)


NOW = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


class FakeProvider:
    def __init__(self, plan: TurnPlan) -> None:
        self.planned = plan
        self.requests = []

    async def plan(self, request):
        self.requests.append(request)
        return PlanningResult(
            plan=self.planned,
            usage=ProviderUsage(input_tokens=100, output_tokens=20, total_tokens=120),
            latency_ms=12.5,
            provider="fake",
            model="fixture-planner",
        )


def pending_action() -> PendingAction:
    return PendingAction(
        action_id="action-1",
        action_type=PendingActionType.TEST_DRIVE_BOOKING,
        request_type=PendingRequestType.TEST_DRIVE_BOOKING,
        request_payload=freeze_json_object(
            {"slot_id": "slot-1"},
            field="request_payload",
        ),
        state=PendingActionState.AWAITING_CONFIRMATION,
        idempotency_key="idem-1",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


class PlanningEngineTests(unittest.IsolatedAsyncioTestCase):
    def test_turn_input_is_either_text_or_action_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "either text or an action reference"):
            TurnRequest(
                current_input="also do this",
                action_reference=ActionReference(
                    action_id="vehicle-1",
                    action_type="select_vehicle",
                ),
                state=ConversationState(),
                now=NOW,
            )

    async def test_deterministic_redirect_makes_zero_provider_calls(self) -> None:
        provider = FakeProvider(
            TurnPlan(
                scope=TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy=ResponseStrategy.ACKNOWLEDGEMENT,
            )
        )

        decision = await PlanningEngine(provider).decide(
            TurnRequest(
                current_input="What size is the moon?",
                state=ConversationState(),
                now=NOW,
            )
        )

        self.assertEqual(
            decision.deterministic_route.kind,
            DeterministicRouteKind.DOMAIN_REDIRECT,
        )
        self.assertIsNone(decision.planning_result)
        self.assertEqual(provider.requests, [])

    async def test_pending_confirmation_makes_zero_provider_calls(self) -> None:
        provider = FakeProvider(
            TurnPlan(
                scope=TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy=ResponseStrategy.ACKNOWLEDGEMENT,
            )
        )
        state = ConversationState(pending_action=pending_action())

        decision = await PlanningEngine(provider).decide(
            TurnRequest(current_input="confirm", state=state, now=NOW)
        )

        self.assertEqual(
            decision.deterministic_route.kind,
            DeterministicRouteKind.CONFIRM_PENDING_ACTION,
        )
        self.assertEqual(provider.requests, [])

    async def test_planner_receives_canonical_context_and_visible_semantic_commands(self) -> None:
        provider = FakeProvider(
            TurnPlan(
                scope=TurnScope.IN_DOMAIN,
                commands=(
                    ReadCommand.from_mapping(
                        ReadCommandName.SEARCH_VEHICLES,
                        {"make": "BMW", "max_price_minor": 3_000_000},
                    ),
                ),
                response_strategy=ResponseStrategy.SEARCH_RESULTS,
            )
        )

        decision = await PlanningEngine(provider).decide(
            TurnRequest(
                current_input="Find a BMW under £30,000",
                state=ConversationState(),
                now=NOW,
            )
        )

        self.assertEqual(decision.planning_result.plan, provider.planned)
        self.assertEqual(len(provider.requests), 1)
        request = provider.requests[0]
        self.assertEqual(request.context, decision.compiled_context.serialized)
        self.assertEqual(request.context, request.to_dict()["context"])
        self.assertIn("search_vehicles", {item.name for item in request.commands})
        self.assertNotIn("response_id", request.to_dict())

    async def test_live_page_observation_overrides_persisted_snapshot_for_the_turn(self) -> None:
        provider = FakeProvider(
            TurnPlan(
                scope=TurnScope.IN_DOMAIN,
                commands=(ReadCommand(ReadCommandName.GET_VEHICLE_DETAILS),),
                response_strategy=ResponseStrategy.VEHICLE_DETAILS,
            )
        )
        state = ConversationState(
            context=PageContext(
                current_url="http://localhost:4173/?vehicle=old",
                page_vehicle_id="old",
                observed_at=NOW - timedelta(minutes=2),
            )
        )

        decision = await PlanningEngine(provider).decide(
            TurnRequest(
                current_input="Tell me about this car",
                state=state,
                now=NOW,
                page_observation=PageContext(
                    current_url="http://localhost:4173/?vehicle=new",
                    page_vehicle_id="new",
                    observed_at=NOW,
                ),
            )
        )

        self.assertIn('"page_vehicle_id":"new"', decision.compiled_context.serialized)
        self.assertNotIn('"page_vehicle_id":"old"', decision.compiled_context.serialized)

    async def test_provider_can_be_replaced_without_changing_harness_inputs(self) -> None:
        plan = TurnPlan(
            scope=TurnScope.DEALERSHIP_ADJACENT,
            commands=(),
            response_strategy=ResponseStrategy.ADJACENT_ADVICE,
            adjacent_advice="An SUV can be practical for five; compare rear and boot space.",
        )
        request = TurnRequest(
            current_input="Is an SUV practical for a family of five?",
            state=ConversationState(),
            now=NOW,
        )

        first = await PlanningEngine(FakeProvider(plan)).decide(request)
        second = await PlanningEngine(FakeProvider(plan)).decide(request)

        self.assertEqual(first.planning_result.plan, second.planning_result.plan)
        self.assertEqual(first.compiled_context, second.compiled_context)

    async def test_hidden_cross_domain_preparation_is_rejected(self) -> None:
        provider = FakeProvider(
            TurnPlan(
                scope=TurnScope.IN_DOMAIN,
                commands=(
                    PreparationCommand(
                        PreparationCommandName.PREPARE_TEST_DRIVE_BOOKING
                    ),
                ),
                response_strategy=ResponseStrategy.ACTION_PREPARED,
            )
        )
        state = ConversationState(
            workflow=WorkflowState(
                domain=WorkflowDomain.WORKSHOP,
                stage=WorkflowStage.SELECTING_SLOT,
            )
        )

        with self.assertRaisesRegex(PlanValidationError, "not visible"):
            await PlanningEngine(provider).decide(
                TurnRequest(
                    current_input="Saturday morning please",
                    state=state,
                    now=NOW,
                )
            )

    async def test_command_strategy_mismatch_fails_closed(self) -> None:
        provider = FakeProvider(
            TurnPlan(
                scope=TurnScope.IN_DOMAIN,
                commands=(ReadCommand(ReadCommandName.SEARCH_VEHICLES),),
                response_strategy=ResponseStrategy.BOOKING_DETAILS,
            )
        )

        with self.assertRaisesRegex(PlanValidationError, "response strategy"):
            await PlanningEngine(provider).decide(
                TurnRequest(
                    current_input="Show me BMWs",
                    state=ConversationState(),
                    now=NOW,
                )
            )

    async def test_result_strategy_without_a_command_fails_closed(self) -> None:
        provider = FakeProvider(
            TurnPlan(
                scope=TurnScope.IN_DOMAIN,
                commands=(),
                response_strategy=ResponseStrategy.SEARCH_RESULTS,
            )
        )

        with self.assertRaisesRegex(PlanValidationError, "requires a dealer command"):
            await PlanningEngine(provider).decide(
                TurnRequest(
                    current_input="Find me a car",
                    state=ConversationState(),
                    now=NOW,
                )
            )

    async def test_argument_schema_rejects_unknown_and_wrong_typed_arguments(self) -> None:
        for arguments in (
            {"invented_filter": "value"},
            {"max_price_minor": "thirty thousand"},
        ):
            with self.subTest(arguments=arguments):
                provider = FakeProvider(
                    TurnPlan(
                        scope=TurnScope.IN_DOMAIN,
                        commands=(
                            ReadCommand.from_mapping(
                                ReadCommandName.SEARCH_VEHICLES,
                                arguments,
                            ),
                        ),
                        response_strategy=ResponseStrategy.SEARCH_RESULTS,
                    )
                )
                with self.assertRaises(PlanValidationError):
                    await PlanningEngine(provider).decide(
                        TurnRequest(
                            current_input="Find a car",
                            state=ConversationState(),
                            now=NOW,
                        )
                    )

    def test_raw_provider_output_is_strictly_parsed(self) -> None:
        valid = {
            "schema_version": 1,
            "scope": "in_domain",
            "commands": [
                {"name": "search_vehicles", "arguments": {"make": "BMW"}}
            ],
            "response_strategy": "search_results",
        }

        parsed = parse_planning_output(valid, allowed_commands={"search_vehicles"})

        self.assertEqual(parsed.commands[0].name, ReadCommandName.SEARCH_VEHICLES)
        invalid = dict(valid, execute_now=True)
        with self.assertRaisesRegex(PlanValidationError, "unknown TurnPlan fields"):
            parse_planning_output(invalid, allowed_commands={"search_vehicles"})
        invalid["commands"] = [
            {"name": "book_test_drive", "arguments": {"slot_id": "slot-1"}}
        ]
        invalid.pop("execute_now")
        with self.assertRaisesRegex(PlanValidationError, "unknown command"):
            parse_planning_output(invalid, allowed_commands={"search_vehicles"})

    def test_planning_provider_is_a_structural_async_protocol(self) -> None:
        plan = TurnPlan(
            scope=TurnScope.IN_DOMAIN,
            commands=(),
            response_strategy=ResponseStrategy.ACKNOWLEDGEMENT,
        )

        self.assertIsInstance(FakeProvider(plan), PlanningProvider)

    def test_provider_request_requires_at_least_one_visible_command(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one"):
            PlanningRequest(context='{"sections":[]}', commands=())


if __name__ == "__main__":
    unittest.main()
