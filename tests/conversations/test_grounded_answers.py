"""Behaviour contract for conversational answers grounded in dealer evidence."""

from dataclasses import replace
import re
import unittest

from webchat.domain.common import Money, Page
from webchat.domain.vehicles import VehicleDetails
from webchat.harness.contracts import (
    ReadCommand,
    ReadCommandName,
    ResponseStrategy,
    TurnPlan,
    TurnScope,
)
from webchat.harness.planning import PlanningEngine, TurnRequest
from webchat.harness.evidence import EvidenceReference
from webchat.harness.grounded_response import (
    GroundedClaim,
    GroundedClaimKind,
    GroundedEvidenceBinding,
)
from webchat.harness.runtime import HarnessRuntime, TurnResult
from webchat.harness.state import ConversationState, EntityContext
from webchat.providers.base import (
    PlanningProviderError,
    ProviderErrorKind,
)

from tests.webchat.harness.test_runtime import (
    NOW,
    FakeGroundedProvider,
    dealer,
    runtime,
    vehicle,
)


def _grounded_answer(*parts):
    """Create a strict provider fixture whose factual claims cite returned evidence."""

    def claims(request):
        result = []
        for text, kind, field_names in parts:
            if kind is GroundedClaimKind.LIMITATION_UNKNOWN:
                mandatory_gap_ids = {
                    item.evidence_id for item in request.missing_facts
                }
                records = tuple(
                    item for item in request.evidence.gaps
                    if field_names or item.evidence_id in mandatory_gap_ids
                )
            else:
                records = request.evidence.items
            references = tuple(
                EvidenceReference(item.evidence_id)
                for item in records
                if not field_names or item.field_name in field_names
            )
            if field_names and not references:
                raise AssertionError(f"missing expected evidence fields: {field_names}")
            values_by_id = {
                item.evidence_id: item.value for item in request.evidence.items
            }
            bindings = tuple(
                GroundedEvidenceBinding(reference, values_by_id[reference.evidence_id])
                for reference in references
                if reference.evidence_id in values_by_id
                and kind in {
                    GroundedClaimKind.SUPPORTED_FACT,
                    GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
                }
            )
            result.append(GroundedClaim(text, kind, references, bindings))
        return tuple(result)

    return FakeGroundedProvider(claims)


def _answer_text(result: TurnResult) -> str:
    first = result.blocks[0]
    if first.kind not in {"text", "notice"}:
        raise AssertionError("the conversational answer must be the first block")
    text = first.to_dict()["payload"].get("text")
    if not isinstance(text, str) or not text.strip():
        raise AssertionError("the first block must contain a direct answer")
    return text


def _assert_support_follows(
    case: unittest.TestCase,
    result: TurnResult,
    *support_kinds: str,
) -> None:
    kinds = [block.kind for block in result.blocks]
    for kind in support_kinds:
        case.assertIn(kind, kinds)
        case.assertGreater(kinds.index(kind), 0)


def _assert_explicit_unknown(case: unittest.TestCase, answer: str) -> None:
    case.assertRegex(
        answer.casefold(),
        re.compile(
            r"(?:cannot|can't|unable to|not able to) (?:confirm|verify|determine)"
            r"|not (?:listed|published|provided|available|included)"
            r"|(?:insufficient|not enough) (?:data|detail|information)"
        ),
    )


class GroundedConversationContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_suitability_answer_uses_known_practicality_evidence_and_names_gaps(self) -> None:
        fake = dealer()
        fake.get_vehicle.return_value = VehicleDetails(vehicle(), ("Large boot",))
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(
                ReadCommandName.GET_VEHICLE_DETAILS,
                {"vehicle_id": "veh-003"},
            ),),
            ResponseStrategy.VEHICLE_DETAILS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer(
                (
                    "The published large boot is useful for family luggage.",
                    GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
                    ("highlights[0]",),
                ),
                (
                    "I cannot confirm passenger space or child-seat fit because those details are not published.",
                    GroundedClaimKind.LIMITATION_UNKNOWN,
                    (),
                ),
            ),
        )

        result = await harness.handle(TurnRequest(
            current_input="Would this be practical for two adults and three children?",
            state=ConversationState(
                entities=EntityContext(selected_vehicle_id="veh-003")
            ),
            now=NOW,
        ))

        answer = _answer_text(result)
        self.assertIn("large boot", answer.casefold())
        _assert_explicit_unknown(self, answer)
        _assert_support_follows(self, result, "vehicle_cards", "vehicle_details")

    async def test_single_fact_question_answers_requested_value_before_details(self) -> None:
        fake = dealer()
        selected = replace(vehicle(), mileage=22_250)
        fake.get_vehicle.return_value = VehicleDetails(selected, ())
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(
                ReadCommandName.GET_VEHICLE_DETAILS,
                {"vehicle_id": selected.id},
            ),),
            ResponseStrategy.VEHICLE_DETAILS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer((
                "Its recorded mileage is 22,250 miles.",
                GroundedClaimKind.SUPPORTED_FACT,
                ("mileage",),
            )),
        )

        result = await harness.handle(TurnRequest(
            current_input="What's the mileage?",
            state=ConversationState(
                entities=EntityContext(selected_vehicle_id=selected.id)
            ),
            now=NOW,
        ))

        self.assertEqual(
            _answer_text(result),
            "Its recorded mileage is 22,250 miles.",
        )
        _assert_support_follows(self, result, "vehicle_cards", "vehicle_details")

    async def test_comparison_recommends_from_returned_price_and_mileage(self) -> None:
        fake = dealer()
        x3 = vehicle("veh-003", price=Money(4_299_500, "GBP"))
        xc60 = replace(
            vehicle("veh-004", price=Money(3_500_000, "GBP")),
            make="Volvo",
            model="XC60",
            mileage=12_000,
        )
        fake.get_vehicle.side_effect = (
            VehicleDetails(x3, ()),
            VehicleDetails(xc60, ()),
        )
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(
                ReadCommandName.COMPARE_VEHICLES,
                {"vehicle_ids": ["veh-003", "veh-004"]},
            ),),
            ResponseStrategy.COMPARISON,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer((
                "The Volvo XC60 is the cheaper choice at £35,000 and has 12,000 miles; "
                "the BMW has 8,000 miles, so the trade-off is the Volvo's higher mileage.",
                GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
                ("make", "model", "price.amount_minor", "price.currency", "mileage"),
            )),
        )

        result = await harness.handle(TurnRequest(
            current_input="Which is the cheaper choice, and what trade-off am I making?",
            state=ConversationState(),
            now=NOW,
        ))

        answer = _answer_text(result).casefold()
        self.assertIn("volvo xc60", answer)
        self.assertTrue("£35,000" in answer or "35000" in answer.replace(",", ""))
        self.assertIn("12,000", answer)
        self.assertIn("8,000", answer)
        _assert_support_follows(self, result, "comparison")

    async def test_follow_up_reference_answers_about_the_selected_vehicle(self) -> None:
        fake = dealer()
        fake.get_vehicle.return_value = VehicleDetails(vehicle(), ())
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.GET_VEHICLE_DETAILS, {}),),
            ResponseStrategy.VEHICLE_DETAILS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer((
                "Yes. This vehicle is listed as diesel and automatic.",
                GroundedClaimKind.SUPPORTED_FACT,
                ("fuel_type", "transmission"),
            )),
        )

        result = await harness.handle(TurnRequest(
            current_input="Is this one diesel and automatic?",
            state=ConversationState(
                entities=EntityContext(selected_vehicle_id="veh-003")
            ),
            now=NOW,
        ))

        answer = _answer_text(result).casefold()
        self.assertIn("diesel", answer)
        self.assertIn("automatic", answer)
        fake.get_vehicle.assert_awaited_once_with("veh-003")
        _assert_support_follows(self, result, "vehicle_cards")

    async def test_unpublished_specification_is_not_invented(self) -> None:
        fake = dealer()
        fake.get_vehicle.return_value = VehicleDetails(vehicle(), ())
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.GET_VEHICLE_DETAILS, {}),),
            ResponseStrategy.VEHICLE_DETAILS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer((
                "I cannot confirm its towing capacity because that specification is not published.",
                GroundedClaimKind.LIMITATION_UNKNOWN,
                (),
            )),
        )

        result = await harness.handle(TurnRequest(
            current_input="What is its towing capacity?",
            state=ConversationState(
                entities=EntityContext(selected_vehicle_id="veh-003")
            ),
            now=NOW,
        ))

        answer = _answer_text(result)
        self.assertIn("towing", answer.casefold())
        _assert_explicit_unknown(self, answer)
        self.assertIsNone(re.search(r"\b\d[\d,.]*\s*(?:kg|tonnes?)\b", answer.casefold()))
        _assert_support_follows(self, result, "vehicle_cards")

    async def test_search_answer_summarises_constraints_before_cards(self) -> None:
        fake = dealer()
        electric = replace(
            vehicle("veh-ev", price=Money(2_675_000, "GBP")),
            model="i4",
            fuel_type="Electric",
            body_style="Saloon",
        )
        fake.search_vehicles.return_value = Page((electric,), 1, 10, 1, 1)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {
                "fuel_type": "Electric",
                "max_price_minor": 3_000_000,
                "currency": "GBP",
            }),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer((
                "I found one electric car within your £30,000 limit, priced at £26,750.",
                GroundedClaimKind.SUPPORTED_FACT,
                ("result_count", "fuel_type", "max_price_minor", "price.amount_minor", "price.currency"),
            )),
        )

        result = await harness.handle(TurnRequest(
            current_input="Show me electric cars under £30,000.",
            state=ConversationState(),
            now=NOW,
        ))

        answer = _answer_text(result).casefold()
        self.assertIn("electric", answer)
        self.assertTrue("£30,000" in answer or "30000" in answer.replace(",", ""))
        self.assertIn("£26,750", answer)
        _assert_support_follows(self, result, "vehicle_cards")

    async def test_zero_result_search_has_one_conversational_response_owner(self) -> None:
        fake = dealer()
        fake.search_vehicles.return_value = Page((), 1, 10, 0, 0)
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.SEARCH_VEHICLES, {
                "fuel_type": "Electric",
                "body_style": "SUV",
                "max_price_minor": 5_000_000,
                "currency": "GBP",
                "exclude_models": ["XC40"],
            }),),
            ResponseStrategy.SEARCH_RESULTS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer((
                "There are no other matching electric SUVs within your £50,000 budget.",
                GroundedClaimKind.SUPPORTED_FACT,
                ("result_count", "fuel_type", "body_style", "max_price.amount_minor"),
            )),
        )

        result = await harness.handle(TurnRequest(
            current_input="Anything other than that model?",
            state=ConversationState(),
            now=NOW,
        ))

        self.assertEqual([block.kind for block in result.blocks], ["text"])
        self.assertIn("no other matching", _answer_text(result).casefold())

    async def test_mixed_request_answers_only_the_groundable_dealership_part(self) -> None:
        fake = dealer()
        fake.get_vehicle.return_value = VehicleDetails(vehicle(), ("Large boot",))
        plan = TurnPlan(
            TurnScope.MIXED,
            (ReadCommand.from_mapping(ReadCommandName.GET_VEHICLE_DETAILS, {}),),
            ResponseStrategy.VEHICLE_DETAILS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer(
                (
                    "It has a published large boot, which may help with carrying a telescope.",
                    GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
                    ("highlights[0]",),
                ),
                (
                    "I cannot confirm it will fit without the telescope and boot dimensions, which are not published.",
                    GroundedClaimKind.LIMITATION_UNKNOWN,
                    (),
                ),
            ),
        )

        result = await harness.handle(TurnRequest(
            current_input="How large is the moon, and will my telescope fit in this one?",
            state=ConversationState(
                entities=EntityContext(selected_vehicle_id="veh-003")
            ),
            now=NOW,
        ))

        answer = _answer_text(result).casefold()
        self.assertNotRegex(answer, r"\bmoon (?:is|has|measures)\b")
        self.assertIn("large boot", answer)
        _assert_explicit_unknown(self, answer)
        _assert_support_follows(self, result, "vehicle_cards", "vehicle_details")

    async def test_customer_claim_is_not_repeated_as_an_unsupported_dealer_fact(self) -> None:
        fake = dealer()
        fake.get_vehicle.return_value = VehicleDetails(vehicle(), ())
        plan = TurnPlan(
            TurnScope.IN_DOMAIN,
            (ReadCommand.from_mapping(ReadCommandName.GET_VEHICLE_DETAILS, {}),),
            ResponseStrategy.VEHICLE_DETAILS,
        )
        harness, _ = runtime(
            fake,
            plan,
            grounded_response=_grounded_answer((
                "I cannot confirm seven seats or a 600-litre boot because those specifications are not published.",
                GroundedClaimKind.LIMITATION_UNKNOWN,
                (),
            )),
        )

        result = await harness.handle(TurnRequest(
            current_input="This has seven seats and a 600-litre boot, right?",
            state=ConversationState(
                entities=EntityContext(selected_vehicle_id="veh-003")
            ),
            now=NOW,
        ))

        answer = _answer_text(result).casefold()
        _assert_explicit_unknown(self, answer)
        self.assertNotRegex(
            answer,
            r"\b(?:it|this (?:vehicle|car)) (?:has|is|comes with) "
            r"(?:seven seats|a 600[ -]litre boot)\b",
        )
        _assert_support_follows(self, result, "vehicle_cards", "vehicle_details")

    async def test_provider_failure_returns_honest_recovery_without_dealer_calls(self) -> None:
        class FailingProvider:
            async def plan(self, request):
                del request
                raise PlanningProviderError(
                    ProviderErrorKind.TIMEOUT,
                    retryable=True,
                )

        fake = dealer()
        harness = HarnessRuntime(
            dealer=fake,
            planning=PlanningEngine(FailingProvider()),
            grounded_response=FakeGroundedProvider(),
        )

        try:
            result = await harness.handle(TurnRequest(
                current_input="Which of those is best for motorway driving?",
                state=ConversationState(),
                now=NOW,
            ))
        except PlanningProviderError as exc:
            self.fail(f"provider failure escaped the conversational boundary: {exc.kind.value}")

        answer = _answer_text(result).casefold()
        self.assertRegex(answer, r"try again|temporar")
        self.assertEqual(result.executed_commands, ())
        self.assertEqual(fake.method_calls, [])


if __name__ == "__main__":
    unittest.main()
