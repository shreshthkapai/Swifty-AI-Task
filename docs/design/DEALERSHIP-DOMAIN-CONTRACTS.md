# Dealership Domain Contracts

This document defines the dealer-independent types and port implemented by the Northstar adapter.

Previous: [Northstar Adapter Specification](./NORTHSTAR-ADAPTER-SPEC.md) · Next: [Northstar Adapter Implementation](./NORTHSTAR-ADAPTER-IMPLEMENTATION.md)

## Purpose and Boundary

The harness must not pass Northstar JSON, HTTP details, or vendor error strings through its workflows. It depends on an immutable dealership vocabulary and an asynchronous `DealerAdapter` protocol. The Northstar adapter translates between that vocabulary and the supplied platform.

The domain owns dealership facts and business requests. Calling applications own conversational state, confirmations, execution lifecycle, persistence, and model orchestration. Consequently, `TestDriveBookingRequest` belongs in `domain/`, while confirmation and execution metadata remain outside this package.

```text
webchat/
├── domain/
│   ├── common.py
│   ├── vehicles.py
│   ├── sales.py
│   ├── workshop.py
│   ├── dealerships.py
│   ├── errors.py
│   └── dealer.py
└── adapters/
    └── northstar/
```

## Modelling Rules

- Use frozen, slotted standard-library dataclasses and small enums; add no validation framework.
- Keep request/query types distinct from returned records. Do not create models whose meaning depends on many optional fields.
- Use `Money(amount_minor, currency)` with an ISO 4217 currency string. Formatting belongs at the UI boundary.
- Represent dates and timezone-aware datetimes as typed values, never display strings.
- Preserve dealer IDs and customer-facing references as separate fields.
- Use tuples for immutable collections. Optional values must represent genuine business absence, such as price on request.
- Enforce universal structural invariants in constructors. Dealer-specific limits and business eligibility remain adapter decisions.

## Model Families

| Module | Principal contracts |
| --- | --- |
| `common` | `Money`, `CustomerIdentity`, `Address`, `Department`, shared record statuses, `Page[T]` |
| `vehicles` | `VehicleSearch`, `Vehicle`, `VehicleDetails`, `VehicleAvailability`, `OfferSearch`, `VehicleOffer` |
| `sales` | `SalesEnquiryRequest`, `SalesEnquiry`, `TestDriveSlotSearch`, `TestDriveSlot`, `TestDriveBookingRequest`, `TestDriveBooking`, `VehicleInterestRequest`, `VehicleInterest`, `CallbackRequest`, `Callback`, `PartExchangeVehicle`, `PartExchangeRequest`, `PartExchangeValuation` |
| `workshop` | `WorkshopService`, `WorkshopSlotSearch`, `WorkshopSlot`, `WorkshopBookingRequest`, `WorkshopBookingLookup`, `WorkshopBookingAmendment`, `WorkshopCancellationRequest`, `WorkshopBooking`, `WorkshopBookingDetails` |
| `dealerships` | `DealerLocation`, `OpeningPeriod`, `HolidayOpening`, `OpeningHours`, `DealershipMessageRequest`, `DealershipMessage`, `BusinessInformation` |

Enums capture stable concepts such as vehicle availability, departments, enquiry types, booking status, callback status, contact method, part-exchange condition, and sort order. They express harness decisions, not Northstar naming conventions.

`WorkshopBookingLookup` contains the identity combination used to verify access. A successful lookup grants access in harness state; API-key possession alone never does. Returned records may contain customer data but must not enter model context or logs unless required and redacted.

Response-specific enrichment uses composition rather than partially populated records: `VehicleDetails` adds highlights to a complete `Vehicle`, and `WorkshopBookingDetails` adds appointment, service, and dealership display data to a complete `WorkshopBooking`. Workshop-capable locations use the same `DealerLocation` type as the dealership catalogue.

## Dealer Port

`DealerAdapter` is an async `typing.Protocol`. It groups no implementation state and exposes dealership capabilities rather than endpoints. Its Python methods are:

```text
search_vehicles; get_vehicle; get_vehicle_availability; list_offers; get_offer
create_sales_enquiry; list_test_drive_slots; book_test_drive
register_vehicle_interest; request_callback; value_part_exchange
list_service_types; list_workshop_locations; list_workshop_slots
book_workshop; lookup_workshop_booking; get_workshop_booking
amend_workshop_booking; cancel_workshop_booking
list_dealerships; get_dealership; get_opening_hours
send_dealership_message; get_business_information
```

Reads accept typed query objects and return typed records or pages. Creates accept a typed business request plus a keyword-only `idempotency_key`. The calling application retains that execution identity separately from customer intent. Amendment is reconciled with `get_workshop_booking` after ambiguous failure, while cancellation remains repeat-safe.

The protocol is intentionally structural. Tests and evaluations can supply a small `FakeDealer` without inheriting from framework classes, and a future dealer can use REST, GraphQL, or another system without changing harness workflows.

## Failures and Recovery

Adapters raise one structured `DealerError` carrying an immutable `DealerFailure`. The harness branches only on `DealerErrorKind`, never message text. Kinds represent distinct recovery policy—not HTTP status or a copy of every vendor code.

| Kind | Harness policy |
| --- | --- |
| `VALIDATION` | Retain valid state and request corrections from typed field violations |
| `NOT_FOUND` | Refresh the relevant catalogue or return to selection |
| `VERIFICATION_FAILED` | Return one generic lookup failure without identity leakage |
| `SLOT_UNAVAILABLE` | Refresh slots; retain details; require reselection and reconfirmation |
| `VEHICLE_RESERVED` | Stop test-drive execution; offer interest or enquiry |
| `VEHICLE_UNAVAILABLE` | Refresh vehicle truth; offer an enquiry where useful |
| `VEHICLE_NOT_RESERVED` | Stop interest execution and refresh vehicle truth |
| `BOOKING_CANCELLED` | Stop amendment and do not retry |
| `IDEMPOTENCY_CONFLICT` | Stop retries, reconcile, and log at high severity |
| `AUTHENTICATION_FAILED` | Treat as server configuration failure; expose no credentials |
| `INVALID_REQUEST` | Treat as a harness/adapter defect, not customer validation |
| `TEMPORARY_FAILURE` | Apply the operation-specific safe retry policy |
| `INVALID_RESPONSE` | Fail safely and record a redacted adapter-protocol fault |

`DealerFailure` includes `kind`, `retryable`, immutable field violations, and optional safe resource context. Northstar codes such as `BOOKING_NOT_FOUND` map to the stable semantic kind `VERIFICATION_FAILED`; raw response bodies remain inside the adapter.

## Action Flow

```text
typed customer intent → business request → explicit confirmation
→ adapter call with stable idempotency key
→ typed record or DealerError → deterministic workflow transition
```

Changing a confirmed request creates a new pending action and execution identity. Retrying the same action reuses its exact request and key. Successful or ambiguous mutations are recorded before generating customer-facing prose, preventing the model from controlling side effects.

## Verification

Standard-library `unittest` coverage checks constructor invariants, enum/status semantics, immutable records, all 24 adapter signatures, and error semantics. A fake adapter proves structural protocol conformance. Northstar mapping tests belong beside its adapter and must cover null prices, relative asset paths, UTC timestamps, field errors, all recovery-relevant error codes, and idempotent replays.

## Non-goals

This layer does not implement HTTP, Northstar authentication, JSON parsing, persistence, prompts, UI response formatting, or complete workflows. Those concerns belong to adapters and calling applications.
