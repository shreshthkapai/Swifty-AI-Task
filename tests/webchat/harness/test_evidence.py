from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
import json
import unittest

from webchat.harness.evidence import (
    EvidenceAuthority,
    EvidenceEnvelope,
    EvidenceFreshness,
    EvidenceGap,
    EvidenceGapReason,
    EvidenceItem,
    EvidenceReference,
)


OBSERVED_AT = datetime(2026, 8, 14, 9, 30, tzinfo=UTC)


def fact(
    field_name: str = "fuel_type",
    value: str | int = "Electric",
) -> EvidenceItem:
    return EvidenceItem(
        source_operation="get_vehicle_details",
        entity_id="veh-019",
        field_name=field_name,
        value=value,
        authority=EvidenceAuthority.DEALER,
        freshness=EvidenceFreshness.LIVE,
        observed_at=OBSERVED_AT,
    )


class EvidenceContractTests(unittest.TestCase):
    def test_fact_id_is_stable_and_changes_with_the_fact_value(self) -> None:
        first = fact()
        repeated = fact()
        changed = fact(value="Diesel")

        self.assertEqual(first.evidence_id, repeated.evidence_id)
        self.assertNotEqual(first.evidence_id, changed.evidence_id)
        self.assertRegex(first.evidence_id, r"^ev_[0-9a-f]{24}$")

    def test_fact_carries_provenance_authority_and_freshness(self) -> None:
        item = fact("price.amount_minor", 2_675_000)

        self.assertEqual(item.to_dict(), {
            "evidence_id": item.evidence_id,
            "source_operation": "get_vehicle_details",
            "entity_id": "veh-019",
            "field_name": "price.amount_minor",
            "value": 2_675_000,
            "authority": "dealer",
            "freshness": "live",
            "observed_at": "2026-08-14T09:30:00+00:00",
        })

    def test_missing_fact_is_explicit_and_referenceable(self) -> None:
        gap = EvidenceGap(
            source_operation="get_vehicle_details",
            entity_id="veh-019",
            field_name="boot_capacity_litres",
            reason=EvidenceGapReason.NOT_PUBLISHED,
            authority=EvidenceAuthority.DEALER,
            freshness=EvidenceFreshness.LIVE,
            observed_at=OBSERVED_AT,
        )
        envelope = EvidenceEnvelope(
            items=(fact(),),
            gaps=(gap,),
            generated_at=OBSERVED_AT,
        )
        reference = EvidenceReference(gap.evidence_id)

        self.assertEqual(envelope.resolve(reference), gap)
        self.assertEqual(gap.to_dict()["reason"], "not_published")
        self.assertRegex(gap.evidence_id, r"^gap_[0-9a-f]{24}$")

    def test_envelope_serialization_is_canonical_independent_of_input_order(self) -> None:
        fuel = fact()
        mileage = fact("mileage", 22_250)
        first = EvidenceEnvelope(
            items=(fuel, mileage),
            generated_at=OBSERVED_AT,
        )
        second = EvidenceEnvelope(
            items=(mileage, fuel),
            generated_at=OBSERVED_AT,
        )

        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(
            first.to_json(),
            json.dumps(
                first.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def test_envelope_rejects_duplicate_evidence_ids(self) -> None:
        duplicate = fact()

        with self.assertRaisesRegex(ValueError, "unique"):
            EvidenceEnvelope(
                items=(duplicate, duplicate),
                generated_at=OBSERVED_AT,
            )

    def test_reference_must_resolve_inside_the_envelope(self) -> None:
        envelope = EvidenceEnvelope(
            items=(fact(),),
            generated_at=OBSERVED_AT,
        )

        with self.assertRaisesRegex(ValueError, "unknown evidence reference"):
            envelope.resolve(EvidenceReference("ev_000000000000000000000000"))

    def test_contracts_are_immutable(self) -> None:
        item = fact()

        with self.assertRaises(FrozenInstanceError):
            item.value = "Diesel"

    def test_invalid_or_ambiguous_fact_values_are_rejected(self) -> None:
        invalid_values = ("", float("nan"), ["Electric"])

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    EvidenceItem(
                        source_operation="get_vehicle_details",
                        entity_id="veh-019",
                        field_name="fuel_type",
                        value=value,
                        authority=EvidenceAuthority.DEALER,
                        freshness=EvidenceFreshness.LIVE,
                        observed_at=OBSERVED_AT,
                    )

    def test_observation_times_must_be_timezone_aware(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            EvidenceItem(
                source_operation="get_vehicle_details",
                entity_id="veh-019",
                field_name="fuel_type",
                value="Electric",
                authority=EvidenceAuthority.DEALER,
                freshness=EvidenceFreshness.LIVE,
                observed_at=datetime(2026, 8, 14, 9, 30),
            )


if __name__ == "__main__":
    unittest.main()
