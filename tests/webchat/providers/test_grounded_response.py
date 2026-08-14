"""Provider-neutral grounded-response contracts and provider behaviour."""

from dataclasses import replace
from datetime import UTC, datetime
import json
import unittest

import httpx

import webchat.providers.base as providers
import webchat.harness.grounded_response as grounded
import webchat.harness.grounded_validation as validation
from webchat.harness.evidence import (
    EvidenceAuthority,
    EvidenceEnvelope,
    EvidenceFreshness,
    EvidenceGap,
    EvidenceGapReason,
    EvidenceItem,
    EvidenceReference,
)
from webchat.harness.grounded_response import (
    GroundedAllowedAction,
    GroundedClaim,
    GroundedClaimKind,
    GroundedResponseRequest,
    GroundedResponseState,
    allowed_actions_from_blocks,
)
from webchat.harness.state import (
    ActionReference,
    ConversationState,
    CustomerState,
    EntityContext,
    MessageBlock,
)
from webchat.providers.base import GroundedResponseProviderError
from webchat.providers.openai import (
    OpenAIGroundedResponseProvider,
    OpenAIProviderConfig,
)


NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


def fact() -> EvidenceItem:
    return EvidenceItem(
        source_operation="get_vehicle_details",
        entity_id="veh-003",
        field_name="body_style",
        value="SUV",
        authority=EvidenceAuthority.DEALER,
        freshness=EvidenceFreshness.SNAPSHOT,
        observed_at=NOW,
    )


def gap() -> EvidenceGap:
    return EvidenceGap(
        source_operation="get_vehicle_details",
        entity_id="veh-003",
        field_name="towing_capacity_kg",
        reason=EvidenceGapReason.NOT_PUBLISHED,
        authority=EvidenceAuthority.DEALER,
        freshness=EvidenceFreshness.SNAPSHOT,
        observed_at=NOW,
    )


def request() -> GroundedResponseRequest:
    known = fact()
    missing = gap()
    envelope = EvidenceEnvelope((known,), NOW, (missing,))
    return GroundedResponseRequest(
        question="Would this work for my family?",
        state=GroundedResponseState.from_conversation(
            ConversationState(
                customer=CustomerState(
                    first_name="Jamie",
                    email="jamie@example.com",
                    phone="07700900123",
                ),
                entities=EntityContext(selected_vehicle_id="veh-003"),
            )
        ),
        evidence=envelope,
        missing_facts=(EvidenceReference(missing.evidence_id),),
        allowed_actions=(
            GroundedAllowedAction("find_test_drive_slots", "Find a test drive", "veh-003"),
        ),
        focusable_entity_ids=("veh-003",),
    )


class GroundedResponseContractTests(unittest.TestCase):
    def test_claim_serializes_typed_evidence_value_binding(self) -> None:
        item = fact()
        reference = EvidenceReference(item.evidence_id)

        serialized = GroundedClaim(
            "It is listed as an SUV.",
            GroundedClaimKind.SUPPORTED_FACT,
            (reference,),
            (grounded.GroundedEvidenceBinding(reference, "SUV"),),
        ).to_dict()

        self.assertEqual(serialized["evidence_ids"], [item.evidence_id])
        self.assertEqual(serialized["evidence_values"], [{
            "evidence_id": item.evidence_id,
            "value": "SUV",
        }])

    def test_repair_context_is_single_attempt_canonical_request_data(self) -> None:
        repair = validation.GroundedRepairContext((
            validation.GroundedValidationCode.UNKNOWN_NOT_DISCLOSED,
            validation.GroundedValidationCode.EVIDENCE_VALUE_MISMATCH,
        ))

        repaired = replace(request(), repair=repair)

        self.assertEqual(repaired.to_dict()["repair"], {
            "attempt": 1,
            "violations": [
                "evidence_value_mismatch",
                "unknown_not_disclosed",
            ],
        })
        with self.assertRaisesRegex(ValueError, "only one"):
            validation.GroundedRepairContext(
                (validation.GroundedValidationCode.INVALID_OUTPUT,),
                attempt=2,
            )

    def test_focused_entity_must_be_focusable_and_cited_by_a_factual_claim(self) -> None:
        value = replace(request(), focusable_entity_ids=("veh-003",))
        known = EvidenceReference(value.evidence.items[0].evidence_id)
        claim = GroundedClaim(
            "I recommend this SUV.",
            GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
            (known,),
            (grounded.GroundedEvidenceBinding(known, "SUV"),),
        )
        claims = (
            claim,
            GroundedClaim(
                "Its towing capacity is not published.",
                GroundedClaimKind.LIMITATION_UNKNOWN,
                value.missing_facts,
            ),
        )

        validation.GroundedResponseValidator().validate(
            value,
            claims,
            focused_entity_id="veh-003",
        )
        with self.assertRaises(validation.GroundedResponseValidationError):
            validation.GroundedResponseValidator().validate(
                value,
                claims,
                focused_entity_id="veh-invented",
            )

    def test_validator_rejects_copied_value_that_differs_from_evidence(self) -> None:
        value = request()
        known = EvidenceReference(value.evidence.items[0].evidence_id)
        claim = GroundedClaim(
            "It is listed as a crossover.",
            GroundedClaimKind.SUPPORTED_FACT,
            (known,),
            (grounded.GroundedEvidenceBinding(known, "Crossover"),),
        )

        with self.assertRaises(validation.GroundedResponseValidationError) as raised:
            validation.GroundedResponseValidator().validate(value, (claim,))

        self.assertEqual(
            raised.exception.issues[0].code,
            validation.GroundedValidationCode.EVIDENCE_VALUE_MISMATCH,
        )

    def test_validator_rejects_fact_without_bound_evidence_value(self) -> None:
        value = request()
        known = EvidenceReference(value.evidence.items[0].evidence_id)

        with self.assertRaises(validation.GroundedResponseValidationError) as raised:
            validation.GroundedResponseValidator().validate(
                value,
                (GroundedClaim(
                    "It has seven seats.",
                    GroundedClaimKind.SUPPORTED_FACT,
                    (known,),
                ),),
            )

        self.assertEqual(
            raised.exception.issues[0].code,
            validation.GroundedValidationCode.EVIDENCE_VALUE_MISSING,
        )

    def test_validator_requires_declared_unknowns_to_remain_explicit(self) -> None:
        value = request()
        known = EvidenceReference(value.evidence.items[0].evidence_id)

        with self.assertRaises(validation.GroundedResponseValidationError) as raised:
            validation.GroundedResponseValidator().validate(
                value,
                (GroundedClaim(
                    "It is listed as an SUV.",
                    GroundedClaimKind.SUPPORTED_FACT,
                    (known,),
                    (grounded.GroundedEvidenceBinding(known, "SUV"),),
                ),),
            )

        self.assertEqual(
            raised.exception.issues[-1].code,
            validation.GroundedValidationCode.UNKNOWN_NOT_DISCLOSED,
        )

    def test_validator_rejects_exact_specification_without_matching_evidence(self) -> None:
        value = request()
        known = EvidenceReference(value.evidence.items[0].evidence_id)
        invented = EvidenceReference("ev_000000000000000000000000")
        claim = GroundedClaim(
            "It is an SUV with a 600-litre boot.",
            GroundedClaimKind.SUPPORTED_FACT,
            (known,),
            (
                grounded.GroundedEvidenceBinding(known, "SUV"),
                grounded.GroundedEvidenceBinding(invented, 600),
            ),
        )

        with self.assertRaises(validation.GroundedResponseValidationError) as raised:
            validation.GroundedResponseValidator().validate(value, (claim,))

        self.assertIn(
            validation.GroundedValidationCode.SPECIFICATION_WITHOUT_EVIDENCE,
            {item.code for item in raised.exception.issues},
        )

    def test_validator_rejects_evidence_gap_presented_as_known(self) -> None:
        value = request()
        missing = value.missing_facts[0]
        claim = GroundedClaim(
            "Its towing capacity is 2,000kg.",
            GroundedClaimKind.SUPPORTED_FACT,
            (missing,),
            (grounded.GroundedEvidenceBinding(missing, 2_000),),
        )

        with self.assertRaises(validation.GroundedResponseValidationError) as raised:
            validation.GroundedResponseValidator().validate(value, (claim,))

        self.assertIn(
            validation.GroundedValidationCode.UNKNOWN_PRESENTED_AS_KNOWN,
            {item.code for item in raised.exception.issues},
        )

    def test_provider_port_is_part_of_the_provider_neutral_boundary(self) -> None:
        self.assertTrue(hasattr(providers, "GroundedResponseProvider"))

    def test_request_is_canonical_and_does_not_expose_customer_pii(self) -> None:
        value = request()

        serialized = value.to_json()

        self.assertEqual(
            serialized,
            json.dumps(
                value.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        self.assertIn("veh-003", serialized)
        self.assertNotIn("Jamie", serialized)
        self.assertNotIn("jamie@example.com", serialized)
        self.assertNotIn("07700900123", serialized)

    def test_supported_fact_and_inference_require_factual_evidence(self) -> None:
        value = request()
        known = EvidenceReference(value.evidence.items[0].evidence_id)
        missing = value.missing_facts[0]

        validation.GroundedResponseValidator().validate(value, (
            GroundedClaim(
                "It is listed as an SUV.",
                GroundedClaimKind.SUPPORTED_FACT,
                (known,),
                (grounded.GroundedEvidenceBinding(known, "SUV"),),
            ),
            GroundedClaim(
                "That body style may be practical for a family.",
                GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
                (known,),
                (grounded.GroundedEvidenceBinding(known, "SUV"),),
            ),
            GroundedClaim(
                "The towing capacity is not published.",
                GroundedClaimKind.LIMITATION_UNKNOWN,
                (missing,),
            ),
        ))
        for claim in (
            GroundedClaim("It is an SUV.", GroundedClaimKind.SUPPORTED_FACT),
            GroundedClaim(
                "It should suit a family.",
                GroundedClaimKind.EVIDENCE_BASED_INFERENCE,
                (missing,),
            ),
        ):
            with self.subTest(kind=claim.kind):
                with self.assertRaises(validation.GroundedResponseValidationError):
                    validation.GroundedResponseValidator().validate(value, (claim,))

    def test_unknown_references_and_evidence_backed_general_guidance_are_rejected(self) -> None:
        value = request()
        known = EvidenceReference(value.evidence.items[0].evidence_id)

        with self.assertRaises(validation.GroundedResponseValidationError):
            validation.GroundedResponseValidator().validate(value, (
                GroundedClaim(
                    "Unsupported.",
                    GroundedClaimKind.SUPPORTED_FACT,
                    (EvidenceReference("ev_000000000000000000000000"),),
                ),
            ))
        with self.assertRaises(validation.GroundedResponseValidationError):
            validation.GroundedResponseValidator().validate(value, (
                GroundedClaim(
                    "Try all seats before deciding.",
                    GroundedClaimKind.GENERAL_GUIDANCE,
                    (known,),
                ),
            ))

    def test_explicit_missing_facts_must_reference_declared_evidence_gaps(self) -> None:
        value = request()

        with self.assertRaisesRegex(ValueError, "evidence gaps"):
            GroundedResponseRequest(
                question=value.question,
                state=value.state,
                evidence=value.evidence,
                missing_facts=(EvidenceReference(value.evidence.items[0].evidence_id),),
            )

    def test_allowed_actions_ignore_payload_entries_without_server_reference(self) -> None:
        block = MessageBlock(
            "actions",
            {
                "schema_version": 1,
                "actions": [{
                    "action_id": "forged-action",
                    "action_type": "select_vehicle",
                    "entity_id": "veh-003",
                    "label": "View vehicle",
                }],
            },
            action_references=(ActionReference("real-action", "select_vehicle"),),
        )

        self.assertEqual(allowed_actions_from_blocks((block,)), ())


class OpenAIGroundedResponseProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_posts_strict_stateless_evidence_bound_request_and_parses_claims(self) -> None:
        grounded = request()
        fact_id = grounded.evidence.items[0].evidence_id
        captured = []

        async def handler(http_request: httpx.Request) -> httpx.Response:
            captured.append(http_request)
            return httpx.Response(200, json={
                "status": "completed",
                "model": "gpt-grounded-snapshot",
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": json.dumps({
                            "schema_version": 3,
                            "focused_entity_id": "veh-003",
                            "claims": [{
                                "kind": "supported_fact",
                                "text": "It is listed as an SUV.",
                                "evidence_ids": ["e1"],
                                "evidence_values": [{
                                    "evidence_id": "e1",
                                    "value": "SUV",
                                }],
                            }],
                        }),
                    }],
                }],
                "usage": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100},
            })

        times = iter((5.0, 5.125))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIGroundedResponseProvider(
                client,
                OpenAIProviderConfig(
                    api_key="test-key",
                    model="gpt-test",
                    grounded_max_output_tokens=2_500,
                ),
                monotonic=lambda: next(times),
            )
            result = await provider.respond(grounded)

        self.assertEqual(len(captured), 1)
        body = json.loads(captured[0].content)
        self.assertFalse(body["store"])
        self.assertNotIn("previous_response_id", body)
        wire_input = json.loads(body["input"])
        self.assertEqual(wire_input["evidence"]["items"], [{
            "id": "e1",
            "source": "get_vehicle_details",
            "entity": "veh-003",
            "field": "body_style",
            "value": "SUV",
            "authority": "dealer",
            "freshness": "snapshot",
        }])
        self.assertEqual(wire_input["missing_facts"], ["g1"])
        self.assertNotIn(fact_id, body["input"])
        self.assertEqual(body["max_output_tokens"], 2_500)
        self.assertIn(
            "For a single-fact question",
            body["instructions"],
        )
        self.assertIn(
            "Do not replace the answer with an introduction or a general summary",
            body["instructions"],
        )
        self.assertTrue(body["text"]["format"]["strict"])
        focus_schema = body["text"]["format"]["schema"]["properties"]["focused_entity_id"]
        self.assertEqual(focus_schema["anyOf"][0]["enum"], ["veh-003"])
        claim_schema = body["text"]["format"]["schema"]["properties"]["claims"]["items"]
        variants = claim_schema["anyOf"]
        self.assertEqual(len(variants), 3)
        factual = next(
            item for item in variants
            if item["properties"]["kind"].get("enum")
        )
        limitation = next(
            item for item in variants
            if item["properties"]["kind"].get("const") == "limitation_unknown"
        )
        guidance = next(
            item for item in variants
            if item["properties"]["kind"].get("const") == "general_guidance"
        )
        self.assertEqual(
            factual["properties"]["kind"]["enum"],
            ["supported_fact", "evidence_based_inference"],
        )
        self.assertEqual(
            factual["properties"]["evidence_ids"]["items"]["enum"],
            ["e1"],
        )
        self.assertEqual(
            limitation["properties"]["evidence_ids"]["items"]["enum"],
            ["g1"],
        )
        self.assertEqual(
            factual["properties"]["evidence_values"]["items"]
            ["properties"]["evidence_id"]["enum"],
            ["e1"],
        )
        self.assertEqual(factual["properties"]["evidence_ids"]["minItems"], 1)
        self.assertEqual(factual["properties"]["evidence_values"]["minItems"], 1)
        self.assertEqual(limitation["properties"]["evidence_values"]["maxItems"], 0)
        self.assertEqual(guidance["properties"]["evidence_ids"]["maxItems"], 0)
        self.assertEqual(guidance["properties"]["evidence_values"]["maxItems"], 0)
        self.assertNotIn(
            "uniqueItems",
            factual["properties"]["evidence_ids"],
        )
        self.assertEqual(result.claims[0].text, "It is listed as an SUV.")
        self.assertEqual(result.focused_entity_id, "veh-003")
        self.assertEqual(result.claims[0].evidence[0].evidence_id, fact_id)
        self.assertEqual(result.claims[0].bindings[0].reference.evidence_id, fact_id)
        self.assertEqual(result.claims[0].bindings[0].value, "SUV")
        self.assertEqual(result.usage.total_tokens, 100)
        self.assertEqual(result.latency_ms, 125.0)

    async def test_grounded_schema_stays_bounded_for_large_evidence_envelope(self) -> None:
        facts = tuple(
            EvidenceItem(
                source_operation="search_vehicles",
                entity_id=f"veh-{index}",
                field_name="mileage",
                value=index,
                authority=EvidenceAuthority.DEALER,
                freshness=EvidenceFreshness.SNAPSHOT,
                observed_at=NOW,
            )
            for index in range(100)
        )
        heavy_request = replace(
            request(),
            evidence=EvidenceEnvelope(facts, NOW),
            missing_facts=(),
            focusable_entity_ids=(),
        )
        captured = []

        async def handler(http_request: httpx.Request) -> httpx.Response:
            captured.append(http_request)
            return httpx.Response(200, json={
                "status": "completed",
                "model": "gpt-test",
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": json.dumps({
                            "schema_version": 3,
                            "focused_entity_id": None,
                            "claims": [{
                                "kind": "general_guidance",
                                "text": "Compare the options against your needs.",
                                "evidence_ids": [],
                                "evidence_values": [],
                            }],
                        }),
                    }],
                }],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIGroundedResponseProvider(
                client,
                OpenAIProviderConfig(api_key="test-key", model="gpt-test"),
            )
            await provider.respond(heavy_request)

        schema = json.loads(captured[0].content)["text"]["format"]["schema"]
        encoded = json.dumps(schema, separators=(",", ":"))
        self.assertLess(len(encoded), 4_500)
        for item in facts:
            self.assertNotIn(item.evidence_id, encoded)

    async def test_large_evidence_request_uses_materially_smaller_wire_payload(self) -> None:
        facts = tuple(
            EvidenceItem(
                source_operation="search_vehicles",
                entity_id=f"veh-{vehicle}",
                field_name=field,
                value=value,
                authority=EvidenceAuthority.DEALER,
                freshness=EvidenceFreshness.SNAPSHOT,
                observed_at=NOW,
            )
            for vehicle in range(4)
            for field, value in (
                ("make", "Volvo"),
                ("model", "XC40"),
                ("year", 2025 - vehicle),
                ("price.amount_minor", 4_325_000),
                ("mileage", 9_750 + vehicle),
                ("fuel_type", "Electric"),
                ("body_style", "SUV"),
                ("availability", "available"),
            )
        )
        large_request = replace(
            request(),
            evidence=EvidenceEnvelope(facts, NOW),
            missing_facts=(),
            focusable_entity_ids=tuple(f"veh-{index}" for index in range(4)),
        )
        captured = []

        async def handler(http_request: httpx.Request) -> httpx.Response:
            captured.append(http_request)
            return httpx.Response(200, json={
                "status": "completed",
                "model": "gpt-test",
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": json.dumps({
                            "schema_version": 3,
                            "focused_entity_id": None,
                            "claims": [{
                                "kind": "general_guidance",
                                "text": "Compare the matching vehicles.",
                                "evidence_ids": [],
                                "evidence_values": [],
                            }],
                        }),
                    }],
                }],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIGroundedResponseProvider(
                client,
                OpenAIProviderConfig(api_key="test-key", model="gpt-test"),
            )
            await provider.respond(large_request)

        body = json.loads(captured[0].content)
        self.assertLess(len(body["input"]), len(large_request.to_json()) * 0.65)
        for item in facts:
            self.assertNotIn(item.evidence_id, body["input"])

    async def test_unknown_evidence_alias_is_rejected_by_provider_adapter(self) -> None:
        grounded_request = request()

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "status": "completed",
                "model": "gpt-test",
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": json.dumps({
                            "schema_version": 3,
                            "focused_entity_id": None,
                            "claims": [{
                                "kind": "supported_fact",
                                "text": "It has seven seats.",
                                "evidence_ids": ["ev_000000000000000000000000"],
                                "evidence_values": [{
                                    "evidence_id": "ev_000000000000000000000000",
                                    "value": 7,
                                }],
                            }],
                        }),
                    }],
                }],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIGroundedResponseProvider(
                client,
                OpenAIProviderConfig(api_key="test-key", model="gpt-test"),
            )
            with self.assertRaises(providers.GroundedResponseOutputError):
                await provider.respond(grounded_request)

    async def test_http_failure_retains_safe_status_code_for_diagnostics(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="upstream detail must remain private")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAIGroundedResponseProvider(
                client,
                OpenAIProviderConfig(api_key="test-key", model="gpt-test"),
            )
            with self.assertRaises(GroundedResponseProviderError) as raised:
                await provider.respond(request())

        self.assertEqual(raised.exception.status_code, 400)
        self.assertNotIn("upstream detail", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
