from datetime import timedelta
import asyncio
import json
import unittest

from webchat.domain.common import Page
from webchat.domain.errors import DealerErrorKind, DealerFailure
from webchat.harness.actions import (
    PendingAction,
    PendingActionState,
    PendingActionType,
    PendingRequestType,
)
from webchat.harness.conversation import (
    ConversationResult,
    ConversationToolCall,
    ConversationUsage,
)
from webchat.harness.conversation_runtime import ConversationRuntime
from webchat.harness.planning import TurnRequest
from webchat.harness.state import (
    ActionReference,
    ConversationState,
    CustomerState,
    MessageRole,
    PageContext,
    StructuredMessage,
)
from webchat.providers.base import ConversationProviderError, ProviderErrorKind

from tests.webchat.harness.test_runtime import (
    NOW,
    availability,
    dealer,
    drive_booking,
    drive_slot,
    vehicle,
)


USAGE = ConversationUsage(12, 6, 18)


def answer(text: str, *, latency: float = 20) -> ConversationResult:
    return ConversationResult(
        text=text,
        usage=USAGE,
        latency_ms=latency,
        time_to_first_token_ms=latency / 2,
        provider="fixture",
        model="conversation-fixture",
    )


def calls(*items: ConversationToolCall, latency: float = 10) -> ConversationResult:
    return ConversationResult(
        tool_calls=tuple(items),
        usage=USAGE,
        latency_ms=latency,
        provider="fixture",
        model="conversation-fixture",
    )


class QueueProvider:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.requests = []

    async def converse(self, request, *, on_text_delta=None):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        if result.text is not None and on_text_delta is not None:
            await on_text_delta(result.text)
        return result


def pending_action() -> PendingAction:
    return PendingAction.from_mapping(
        action_id="pending-1",
        action_type=PendingActionType.TEST_DRIVE_BOOKING,
        request_type=PendingRequestType.TEST_DRIVE_BOOKING,
        request_payload={
            "slot_id": "td-slot-1",
            "vehicle_id": "veh-003",
            "customer": {
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
                "phone": "07700111222",
            },
        },
        state=PendingActionState.AWAITING_CONFIRMATION,
        idempotency_key="idem-1",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


class ConversationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_conversation_has_one_response_owner_and_one_model_call(self) -> None:
        provider = QueueProvider(answer("An SUV can be practical for a family of five."))
        runtime = ConversationRuntime(dealer=dealer(), conversation=provider)

        result = await runtime.handle(
            TurnRequest(
                current_input="Would an SUV suit a family of five?",
                state=ConversationState(),
                now=NOW,
            )
        )

        self.assertEqual(result.model_calls, 1)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual([block.kind for block in result.blocks], ["text"])
        self.assertEqual(
            result.blocks[0].to_dict()["payload"]["text"],
            "An SUV can be practical for a family of five.",
        )

    async def test_read_tool_gets_one_continuation_with_facts_then_text_before_cards(self) -> None:
        fake_dealer = dealer()
        fake_dealer.search_vehicles.return_value = Page(
            (vehicle("veh-ev"),),
            page=1,
            page_size=10,
            total_items=1,
            total_pages=1,
        )
        tool = ConversationToolCall(
            "call-search",
            "search_vehicles",
            {"fuel_type": "Electric", "max_price_minor": 5_000_000},
        )
        provider = QueueProvider(
            calls(tool),
            answer("I found one matching electric vehicle under £50,000.", latency=15),
        )
        runtime = ConversationRuntime(dealer=fake_dealer, conversation=provider)

        result = await runtime.handle(
            TurnRequest(
                current_input="Show me electric cars under £50,000",
                state=ConversationState(),
                now=NOW,
            )
        )

        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.executed_commands, ("search_vehicles",))
        self.assertEqual([block.kind for block in result.blocks], ["text", "vehicle_cards"])
        continuation = provider.requests[1]
        self.assertIsNotNone(continuation.exchange)
        output = continuation.exchange.results[0].output_dict()
        self.assertEqual(output["status"], "ok")
        self.assertTrue(output["facts"])
        self.assertEqual(output["entities"], ["veh-ev"])

    async def test_second_tool_round_is_rejected_without_executing_it(self) -> None:
        fake_dealer = dealer()
        fake_dealer.search_vehicles.return_value = Page(
            (vehicle(),), 1, 10, 1, 1
        )
        first = ConversationToolCall("call-1", "search_vehicles", {"make": "BMW"})
        forbidden = ConversationToolCall("call-2", "get_vehicle_details", {"vehicle_id": "veh-003"})
        provider = QueueProvider(calls(first), calls(forbidden))
        runtime = ConversationRuntime(dealer=fake_dealer, conversation=provider)

        result = await runtime.handle(
            TurnRequest(current_input="Show me a BMW", state=ConversationState(), now=NOW)
        )

        fake_dealer.get_vehicle.assert_not_awaited()
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.provider_failure, "tool_round_limit")
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "provider_unavailable")

    async def test_free_text_cancel_is_not_keyword_routed(self) -> None:
        provider = QueueProvider(answer("Do you want to cancel the pending test drive?"))
        state = ConversationState(pending_action=pending_action())
        runtime = ConversationRuntime(dealer=dealer(), conversation=provider)

        result = await runtime.handle(
            TurnRequest(current_input="cancel", state=state, now=NOW)
        )

        self.assertEqual(result.model_calls, 1)
        self.assertEqual(len(provider.requests), 1)
        self.assertIs(result.state.pending_action, state.pending_action)

    async def test_trusted_cancel_button_bypasses_model(self) -> None:
        provider = QueueProvider()
        runtime = ConversationRuntime(dealer=dealer(), conversation=provider)

        result = await runtime.handle(
            TurnRequest(
                current_input=None,
                action_reference=ActionReference("pending-1", "cancel"),
                state=ConversationState(pending_action=pending_action()),
                now=NOW,
            )
        )

        self.assertEqual(provider.requests, [])
        self.assertEqual(result.model_calls, 0)
        self.assertIsNone(result.state.pending_action)
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "action_cancelled")

    async def test_typed_confirmation_is_interpreted_once_then_executed_safely(self) -> None:
        fake_dealer = dealer()
        fake_dealer.get_vehicle_availability.return_value = availability()
        fake_dealer.list_test_drive_slots.return_value = (drive_slot(),)
        fake_dealer.book_test_drive.return_value = drive_booking()
        provider = QueueProvider(
            calls(ConversationToolCall("control-1", "confirm_pending_action", {}))
        )
        runtime = ConversationRuntime(dealer=fake_dealer, conversation=provider)

        result = await runtime.handle(
            TurnRequest(
                current_input="yes, please book it",
                state=ConversationState(pending_action=pending_action()),
                now=NOW,
            )
        )

        self.assertEqual(result.model_calls, 1)
        self.assertEqual(result.executed_commands, ("confirm_pending_action",))
        fake_dealer.book_test_drive.assert_awaited_once()
        self.assertEqual(result.state.pending_action.state, PendingActionState.SUCCEEDED)

    async def test_independent_read_tools_execute_concurrently(self) -> None:
        fake_dealer = dealer()
        locations = fake_dealer.list_dealerships.return_value
        information = fake_dealer.get_business_information.return_value
        both_started = asyncio.Event()
        started = 0

        async def overlap(value):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.2)
            return value

        async def list_locations():
            return await overlap(locations)

        async def business_information():
            return await overlap(information)

        fake_dealer.list_dealerships.side_effect = list_locations
        fake_dealer.get_business_information.side_effect = business_information
        provider = QueueProvider(
            calls(
                ConversationToolCall("read-1", "list_dealerships", {}),
                ConversationToolCall("read-2", "get_business_information", {}),
            ),
            answer("Here are the dealership locations and current business information."),
        )
        runtime = ConversationRuntime(dealer=fake_dealer, conversation=provider)

        result = await asyncio.wait_for(
            runtime.handle(
                TurnRequest(
                    current_input="Where are you based and how does finance work?",
                    state=ConversationState(),
                    now=NOW,
                )
            ),
            timeout=0.5,
        )

        self.assertEqual(started, 2)
        self.assertEqual(
            result.executed_commands,
            ("list_dealerships", "get_business_information"),
        )
        self.assertEqual(result.model_calls, 2)

    async def test_live_page_observation_replaces_stale_selected_vehicle_in_context(self) -> None:
        provider = QueueProvider(answer("You are viewing the current page vehicle."))
        runtime = ConversationRuntime(dealer=dealer(), conversation=provider)

        result = await runtime.handle(
            TurnRequest(
                current_input="Which car am I viewing?",
                state=ConversationState(),
                page_observation=PageContext(
                    current_url="/?vehicle=veh-page#vehicles",
                    page_vehicle_id="veh-page",
                    observed_at=NOW,
                ),
                now=NOW,
            )
        )

        context = json.loads(provider.requests[0].context)
        self.assertEqual(context["state"]["selected"]["vehicle_id"], "veh-page")
        self.assertEqual(result.state.entities.selected_vehicle_id, "veh-page")

    async def test_provider_failure_returns_one_concise_recovery_without_retry_call(self) -> None:
        provider = QueueProvider(
            ConversationProviderError(ProviderErrorKind.TIMEOUT, retryable=True)
        )
        runtime = ConversationRuntime(dealer=dealer(), conversation=provider)

        result = await runtime.handle(
            TurnRequest(current_input="Show me cars", state=ConversationState(), now=NOW)
        )

        self.assertEqual(result.model_calls, 1)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(result.provider_failure, "timeout")
        self.assertEqual(result.blocks[0].to_dict()["payload"]["code"], "provider_unavailable")

    async def test_context_keeps_known_customer_once_and_redacts_historical_pii(self) -> None:
        provider = QueueProvider(answer("I can continue with those saved details."))
        runtime = ConversationRuntime(dealer=dealer(), conversation=provider)
        state = ConversationState(
            customer=CustomerState(
                first_name="Jane",
                last_name="Doe",
                email="jane@example.com",
                phone="07700111222",
                registration="AB12 CDE",
            )
        )
        history = StructuredMessage(
            message_id="message-1",
            client_turn_id="turn-1",
            role=MessageRole.USER,
            created_at=NOW,
            text="Use jane@example.com, 07700111222 and AB12 CDE",
        )

        await runtime.handle(
            TurnRequest(
                current_input="Use the same details",
                state=state,
                recent_messages=(history,),
                prior_failures=(
                    DealerFailure(DealerErrorKind.TEMPORARY_FAILURE, retryable=True),
                ),
                now=NOW,
            )
        )

        context = json.loads(provider.requests[0].context)
        self.assertEqual(context["state"]["customer"]["email"], "jane@example.com")
        self.assertEqual(
            context["recent_messages"][0]["text"],
            "Use [known email], [known phone] and [known registration]",
        )
        self.assertEqual(context["prior_failures"][0]["kind"], "temporary_failure")


if __name__ == "__main__":
    unittest.main()
