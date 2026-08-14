"""Runtime ownership of grounded conversational responses."""

from dataclasses import replace
import unittest

from webchat.domain.common import Page
from webchat.harness.contracts import (
    ReadCommand,
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.grounded_response import (
    GroundedClaim,
    GroundedClaimKind,
    GroundedEvidenceBinding,
)
from webchat.harness.grounded_validation import GroundedValidationCode
from webchat.harness.evidence import EvidenceReference
from webchat.harness.planning import PlanningEngine, TurnRequest
from webchat.harness.runtime import HarnessRuntime
from webchat.harness.state import ConversationState
from webchat.providers.base import (
    GroundedResponseProviderError,
    GroundedResponseResult,
    ProviderErrorKind,
    ProviderUsage,
)

from tests.webchat.harness.test_runtime import NOW, FakeProvider, dealer, vehicle


class CapturingGroundedResponder:
    def __init__(self, *, fail: bool = False, focused_entity_id: str | None = None) -> None:
        self.fail = fail
        self.focused_entity_id = focused_entity_id
        self.requests = []

    async def respond(self, request):
        self.requests.append(request)
        if self.fail:
            raise GroundedResponseProviderError(
                ProviderErrorKind.TIMEOUT,
                retryable=True,
            )
        item = (
            next(
                item for item in request.evidence.items
                if item.entity_id == self.focused_entity_id
            )
            if self.focused_entity_id is not None
            else request.evidence.items[0]
        )
        reference = EvidenceReference(item.evidence_id)
        limitations = request.missing_facts
        return GroundedResponseResult(
            claims=(
                GroundedClaim(
                    "I found one electric BMW priced at £26,750.",
                    GroundedClaimKind.SUPPORTED_FACT,
                    (reference,),
                    (GroundedEvidenceBinding(reference, item.value),),
                ),
                GroundedClaim(
                    "Some requested details are not confirmed in the dealer data.",
                    GroundedClaimKind.LIMITATION_UNKNOWN,
                    limitations,
                ),
            ),
            usage=ProviderUsage(30, 12, 42),
            latency_ms=4.0,
            provider="fixture",
            model="grounder",
            focused_entity_id=self.focused_entity_id,
        )


def grounded_runtime(*, fail: bool = False, responder=None):
    fake_dealer = dealer()
    fake_dealer.search_vehicles.return_value = Page((vehicle(),), 1, 10, 1, 1)
    plan = TurnPlan(
        TurnScope.IN_DOMAIN,
        (ReadCommand.from_mapping(
            ReadCommandName.SEARCH_VEHICLES,
            {"make": "BMW", "fuel_type": "Electric"},
        ),),
        ResponseStrategy.SEARCH_RESULTS,
    )
    planner = FakeProvider(plan)
    responder = responder or CapturingGroundedResponder(fail=fail)
    identifiers = (f"answer-id-{index}" for index in range(1, 100))
    harness = HarnessRuntime(
        dealer=fake_dealer,
        planning=PlanningEngine(planner),
        grounded_response=responder,
        id_factory=lambda: next(identifiers),
    )
    return harness, fake_dealer, planner, responder


class GroundedRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_turn_has_one_conversational_owner_before_support_blocks(self) -> None:
        harness, _, _, responder = grounded_runtime()

        result = await harness.handle(TurnRequest(
            current_input="Show me electric BMWs.",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual(len(responder.requests), 1)
        grounded = responder.requests[0]
        self.assertEqual(grounded.question, "Show me electric BMWs.")
        self.assertTrue(grounded.evidence.items)
        self.assertEqual(grounded.allowed_actions[0].action_type, "select_vehicle")
        self.assertEqual([block.kind for block in result.blocks], ["text", "vehicle_cards"])
        self.assertIn("£26,750", result.blocks[0].to_dict()["payload"]["text"])
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.input_tokens, 130)
        self.assertEqual(result.output_tokens, 32)
        self.assertEqual(result.provider_latency_ms, 14.0)

    async def test_evidence_bound_vehicle_recommendation_becomes_selected_and_marks_card(self) -> None:
        responder = CapturingGroundedResponder(focused_entity_id="veh-002")
        harness, fake_dealer, _, _ = grounded_runtime(responder=responder)
        fake_dealer.search_vehicles.return_value = Page(
            (
                vehicle("veh-001"),
                replace(vehicle("veh-002"), year=2025),
                replace(vehicle("veh-003"), year=2021),
            ),
            1,
            10,
            3,
            1,
        )

        result = await harness.handle(TurnRequest(
            current_input="Choose one for me.",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual(result.state.entities.selected_vehicle_id, "veh-002")
        cards = next(block for block in result.blocks if block.kind == "vehicle_cards")
        payload = cards.to_dict()["payload"]
        self.assertEqual(payload["recommended_entity_id"], "veh-002")
        self.assertEqual(
            [item["id"] for item in payload["vehicles"]],
            ["veh-002", "veh-001", "veh-003"],
        )
        group = result.state.presentation_groups[-1]
        self.assertEqual(
            [(item.entity_id, item.ordinal) for item in group.entities],
            [("veh-002", 1), ("veh-001", 2), ("veh-003", 3)],
        )

    async def test_obvious_scope_redirect_does_not_call_grounded_responder(self) -> None:
        harness, fake_dealer, planner, responder = grounded_runtime()

        result = await harness.handle(TurnRequest(
            current_input="What size is the moon?",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual(responder.requests, [])
        self.assertEqual(planner.requests, [])
        self.assertEqual(fake_dealer.method_calls, [])
        self.assertEqual(result.model_calls, 0)

    async def test_responder_failure_keeps_verified_support_and_adds_honest_notice(self) -> None:
        harness, _, _, responder = grounded_runtime(fail=True)

        result = await harness.handle(TurnRequest(
            current_input="Show me electric BMWs.",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual(len(responder.requests), 1)
        self.assertEqual([block.kind for block in result.blocks], ["notice", "vehicle_cards"])
        self.assertEqual(
            result.blocks[0].to_dict()["payload"]["code"],
            "grounded_response_unavailable",
        )
        self.assertEqual(result.model_calls, 2)
        self.assertEqual(result.provider_failure, "grounded_response:timeout")

    async def test_wrong_provider_result_type_degrades_to_safe_supporting_blocks(self) -> None:
        class MalformedResponder:
            async def respond(self, request):
                return object()

        harness, _, _, _ = grounded_runtime(responder=MalformedResponder())

        result = await harness.handle(TurnRequest(
            current_input="Show me electric BMWs.",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual([block.kind for block in result.blocks], ["notice", "vehicle_cards"])
        self.assertEqual(result.model_calls, 3)
        self.assertEqual(
            result.provider_failure,
            "grounded_response:repair_failed:invalid_output",
        )

    async def test_invalid_evidence_value_gets_one_controlled_repair(self) -> None:
        class RepairingResponder:
            def __init__(self) -> None:
                self.requests = []

            async def respond(self, request):
                self.requests.append(request)
                item = request.evidence.items[0]
                reference = EvidenceReference(item.evidence_id)
                value = "invented" if len(self.requests) == 1 else item.value
                limitations = tuple(
                    EvidenceReference(item.evidence_id)
                    for item in request.evidence.gaps
                )
                return GroundedResponseResult(
                    claims=(GroundedClaim(
                        "I found verified dealership information.",
                        GroundedClaimKind.SUPPORTED_FACT,
                        (reference,),
                        (GroundedEvidenceBinding(reference, value),),
                    ), GroundedClaim(
                        "Some requested details are not confirmed in the dealer data.",
                        GroundedClaimKind.LIMITATION_UNKNOWN,
                        limitations,
                    )),
                    usage=ProviderUsage(10, 4, 14),
                    latency_ms=2.0,
                    provider="fixture",
                    model="repairing-grounder",
                )

        responder = RepairingResponder()
        harness, _, _, _ = grounded_runtime(responder=responder)

        result = await harness.handle(TurnRequest(
            current_input="Show me electric BMWs.",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual(len(responder.requests), 2)
        self.assertIsNone(responder.requests[0].repair)
        self.assertEqual(
            responder.requests[1].repair.violations,
            (GroundedValidationCode.EVIDENCE_VALUE_MISMATCH,),
        )
        self.assertEqual(result.blocks[0].kind, "text")
        self.assertEqual(result.model_calls, 3)
        self.assertEqual(result.input_tokens, 120)
        self.assertEqual(result.output_tokens, 28)
        self.assertEqual(result.provider_latency_ms, 14.0)
        self.assertIsNone(result.provider_failure)

    async def test_failed_repair_uses_verified_fallback_and_stops(self) -> None:
        class InvalidResponder:
            def __init__(self) -> None:
                self.requests = []

            async def respond(self, request):
                self.requests.append(request)
                item = request.evidence.items[0]
                reference = EvidenceReference(item.evidence_id)
                limitations = tuple(
                    EvidenceReference(item.evidence_id)
                    for item in request.evidence.gaps
                )
                return GroundedResponseResult(
                    claims=(GroundedClaim(
                        "It has an unsupported exact specification.",
                        GroundedClaimKind.SUPPORTED_FACT,
                        (reference,),
                        (GroundedEvidenceBinding(reference, "invented"),),
                    ), GroundedClaim(
                        "Some requested details are not confirmed in the dealer data.",
                        GroundedClaimKind.LIMITATION_UNKNOWN,
                        limitations,
                    )),
                    usage=ProviderUsage(10, 4, 14),
                    latency_ms=2.0,
                    provider="fixture",
                    model="invalid-grounder",
                )

        responder = InvalidResponder()
        harness, _, _, _ = grounded_runtime(responder=responder)

        result = await harness.handle(TurnRequest(
            current_input="Show me electric BMWs.",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual(len(responder.requests), 2)
        self.assertEqual(result.model_calls, 3)
        self.assertEqual([block.kind for block in result.blocks], ["notice", "vehicle_cards"])
        fallback = result.blocks[0].to_dict()["payload"]
        self.assertEqual(fallback["code"], "grounded_response_fallback")
        self.assertIn("confirmed dealership information", fallback["text"].casefold())
        self.assertEqual(
            result.provider_failure,
            "grounded_response:repair_failed:evidence_value_mismatch",
        )


if __name__ == "__main__":
    unittest.main()
