# Northstar Dealer Adapter Specification

> **Solution design 3 of 6 — Applied result.** This is the Northstar-specific contract produced by applying the [Dealer Integration Process](./DEALER-INTEGRATION-PROCESS.md).
>
> Previous: [Dealer Integration Process](./DEALER-INTEGRATION-PROCESS.md) · Next: [Evaluation Strategy](./EVALUATION-STRATEGY.md)

## Purpose and source of truth

This document defines the Northstar-specific boundary used by the dealership chatbot harness. The adapter translates typed dealership operations into Northstar REST requests; it owns endpoint paths, authentication, payload shapes, response mapping, and Northstar business quirks. Conversation policy, confirmations, workflow state, and user-facing wording remain in the harness.

This specification was derived from the [OpenAPI contract](../../dealership-platform/openapi.json), server and application implementation, contract tests, supplied [Integration Guide](../INTEGRATION-GUIDE.md), [Business Semantics](../BUSINESS-SEMANTICS.md), and [Seed Data Catalogue](../SEEDED-SCENARIOS.md). If these disagree, executable platform behaviour is noted here and the OpenAPI contract should be corrected before relying on a new behaviour.

## Adapter boundary

The harness should depend on dealership-domain methods, not raw HTTP:

```text
searchVehicles / getVehicle / getVehicleAvailability / listOffers
createSalesEnquiry / listTestDriveSlots / bookTestDrive / registerInterest
requestCallback / valuePartExchange
listServiceTypes / listWorkshopLocations / listWorkshopSlots
bookWorkshop / lookupWorkshopBooking / amendWorkshopBooking / cancelWorkshopBooking
listDealerships / getDealership / getOpeningHours / sendDealershipMessage
getBusinessInformation
```

The Northstar adapter must build URLs and payloads, attach credentials, resolve relative asset URLs, convert API errors into stable domain errors, and preserve Northstar IDs/references. The model must never receive the API key or construct arbitrary platform requests.

## Common protocol

- **Base URL:** configurable server-side; local default `http://localhost:4010`.
- **Formats:** JSON uses camelCase. Money is integer GBP pence. Dates are `YYYY-MM-DD`; timestamps are UTC ISO 8601. Vehicle/offer image paths are relative to the platform base URL.
- **Public reads:** catalogue, location, hours, availability, service-type, offer, and business-information endpoints need no key.
- **Protected operations:** every write, workshop lookup, and saved-record read requires `X-API-Key`. Keep `NORTHSTAR_API_KEY` server-side.
- **Bodies:** must be JSON objects and at most 100,000 bytes. Malformed JSON returns `400 INVALID_JSON`; oversized bodies return `413 REQUEST_TOO_LARGE`.
- **Errors:** `{ error: { code, message, fieldErrors, retryable } }`. Branch on `code`, retain `fieldErrors` for correction prompts, and use `message` only as fallback copy. Unexpected failures return `500 INTERNAL_ERROR` with `retryable: true`.
- **Create idempotency:** send a caller-generated `Idempotency-Key` of at most 100 characters. It is mandatory for test-drive and workshop booking and recommended for all other creates. A same-key/same-body replay returns the original record plus `idempotentReplay: true`; a changed body returns `409 IDEMPOTENCY_CONFLICT`. Keys are scoped by resource type.
- **Retry rule:** retry reads after transient transport/5xx failures. Retry creates only with the identical body and key. Reconcile a timed-out amendment with a protected read before retrying. Cancellation is repeat-safe.
- **Common contact validation:** `firstName` and `lastName` must each be at least two characters and contain no digits; `email` must be complete; `phone` must be a UK `01` landline or `07` mobile. Spaces, punctuation, and `+44` are normalized.
- **Common identifiers:** unknown dealership, vehicle, offer, slot, booking, or saved-record IDs return `404 NOT_FOUND`. Missing/incorrect API credentials return `401 UNAUTHORISED`.

## Capability map

| Area | Reads | Consequential operations |
| --- | --- | --- |
| Vehicles and offers | Inventory search, vehicle detail, live availability, offers | Sales enquiry, test drive, reserved-vehicle interest, callback, part-exchange estimate |
| Workshop | Service types, locations, live slots, verified booking lookup | Book, amend, cancel |
| Dealership | Locations, department hours, holiday exceptions, business notices | Department message, callback |

Callbacks use one shared endpoint for sales, service, and parts. Northstar exposes no test-drive amendment/cancellation, no inventory mutation, and no offer mutation.

## Vehicles and sales

### Inventory and offers

#### `GET /api/vehicles` — search inventory

- **Inputs:** optional `q`, `make`, `model`, `fuelType`, `transmission`, `bodyStyle`, `availability`, `dealershipId`, `minPricePence`, `maxPricePence`, `maxMileage`, `minYear`, `sort`, `page`, and `pageSize`.
- **Output:** `{ items: Vehicle[], pagination }`. A vehicle contains IDs and dealership labels, make/model/variant, year, nullable price/monthly price, mileage, fuel, transmission, colour, body style, availability, registration, description, image URLs, and `updatedAt`.
- **Auth/state/idempotency:** public, read-only, safe to retry.
- **Validation and constraints:** exact filters are case-insensitive. `q` searches only make, model, variant, and colour. Integer filters must parse as whole numbers. `page` is clamped to at least 1; `pageSize` to 1–50. Sort values are `priceAsc`, `priceDesc`, `mileageAsc`, or `newest`; unknown values silently use `newest`. Null prices are excluded by price filters and sort last. Empty results are valid.
- **Errors:** `422 VALIDATION_ERROR` for malformed integer/pagination values.

#### `GET /api/vehicles/{vehicleId}` — vehicle detail

- **Inputs:** path `vehicleId`.
- **Output:** one `Vehicle` plus `highlights` (inspection, warranty, and delivery statements).
- **Auth/state/idempotency:** public, read-only.
- **Constraints/errors:** details are catalogue state, not a guarantee of continued availability; unknown ID returns `404 NOT_FOUND`.

#### `GET /api/vehicles/{vehicleId}/availability` — permitted next actions

- **Inputs:** path `vehicleId`.
- **Output:** `vehicleId`, `availability`, `canEnquire`, `canBookTestDrive`, `canRegisterInterest`, and nullable `nextTestDriveSlot { id, startsAt }`.
- **Auth/state/idempotency:** public, live read.
- **Constraints:** `canEnquire` is always true, including sold vehicles. Test drives require an `available` vehicle and an available slot. Interest is allowed only when `reserved`. Re-read immediately before presenting a final confirmation and expect commit-time races.
- **Errors:** unknown vehicle returns `404 NOT_FOUND`.

#### `GET /api/offers` — list new-car offers

- **Inputs:** optional case-insensitive exact `make` and `productType`.
- **Output:** `{ items }`; each offer has `id`, make/model/title, product type, monthly and upfront pence, nullable APR, term months, annual mileage, expiry date, description, and resolved image path.
- **Auth/state/idempotency:** public, read-only.
- **Constraints/errors:** ordered by monthly price; unmatched filters return an empty list. No platform validation error is defined.

#### `GET /api/offers/{offerId}` — offer detail

- **Inputs:** path `offerId`.
- **Output:** one offer in the same shape as the list.
- **Auth/state/idempotency:** public, read-only.
- **Errors:** unknown offer returns `404 NOT_FOUND`.

### Sales mutations

All operations below require `X-API-Key`, return `201` on creation, and should use an idempotency key. They validate the common contact fields.

#### `POST /api/sales-enquiries`

- **Inputs:** `dealershipId` (required, max 80), optional `vehicleId` (max 80), `enquiryType` (`general | availability | finance | part-exchange`), contact, and `message` (5–2,000 characters).
- **Output:** `id`, `reference` (`SALE-*`), supplied fields, `status: received`, and `createdAt`.
- **Mutation:** saves an enquiry only; it does not reserve a vehicle, approve finance, or promise a response time. Available, reserved, and sold vehicles may all be enquired about.
- **Errors:** `422 VALIDATION_ERROR`; `404 NOT_FOUND` for dealership/vehicle; idempotency errors described above.

#### `GET /api/sales-enquiries/{recordId}`

- **Inputs/output:** path ID; returns the complete saved enquiry, including PII.
- **Auth/state/idempotency:** API key required; read-only.
- **Errors:** `404 NOT_FOUND`. This is a backend reconciliation operation, not customer authentication.

#### `GET /api/test-drive-slots`

- **Inputs:** optional `dealershipId`, `vehicleId`, `dateFrom`, and `dateTo`.
- **Output:** `{ items }`; available slots only, with `id`, dealership/vehicle IDs, `startsAt`, status, dealership name, and vehicle make/model/variant.
- **Auth/state/idempotency:** public, live read.
- **Validation and constraints:** date bounds are inclusive and expected as `YYYY-MM-DD`, but the platform does not explicitly validate them. Unknown IDs yield no matches. Results are chronological and capped at 100.

#### `POST /api/test-drive-bookings`

- **Inputs:** required `Idempotency-Key`; `slotId` (max 80), common contact, optional `notes` (max 2,000).
- **Output:** `id`, `reference` (`TEST-*`), slot/dealership/vehicle IDs, contact/notes, `status: confirmed`, and `createdAt`.
- **Mutation:** atomically verifies vehicle and slot, creates the booking, and changes the slot from `available` to `booked`.
- **Constraints:** explicit user confirmation is required in the harness. A reserved vehicle returns `409 VEHICLE_RESERVED`; sold/otherwise unavailable returns `409 VEHICLE_UNAVAILABLE`; a claimed slot returns `409 SLOT_UNAVAILABLE`. Missing/oversized key returns `400 IDEMPOTENCY_KEY_REQUIRED` or `INVALID_IDEMPOTENCY_KEY`. No amend/cancel operation exists.

#### `GET /api/test-drive-bookings/{recordId}`

- **Inputs/output:** path ID; complete saved booking including PII.
- **Auth/state/idempotency:** API key required; read-only; use for reconciliation.
- **Errors:** `404 NOT_FOUND`.

#### `POST /api/vehicle-interests`

- **Inputs:** `vehicleId` (max 80), common contact, optional `notes` (max 2,000).
- **Output:** `id`, `reference` (`WAIT-*`), dealership/vehicle IDs, contact/notes, `status: registered`, and `createdAt`.
- **Mutation/constraints:** records interest only when the vehicle is currently `reserved`; it does not create a queue position or availability guarantee. Explicit confirmation is required.
- **Errors:** `409 VEHICLE_NOT_RESERVED`, `404 NOT_FOUND`, validation/idempotency errors.

#### `GET /api/vehicle-interests/{recordId}`

- **Inputs/output:** path ID; complete saved interest record including PII.
- **Auth/state/idempotency:** API key required; read-only.
- **Errors:** `404 NOT_FOUND`.

#### `POST /api/callback-requests`

- **Inputs:** `dealershipId` (max 80), `department` (`sales | service | parts`), optional `vehicleId` (max 80), contact, optional `preferredTime` (max 120), and `reason` (5–1,000 characters).
- **Output:** `id`, `reference` (`CALL-*`), supplied fields, `status: requested`, and `createdAt`.
- **Mutation/constraints:** records a preference, not a guaranteed callback time. `vehicleId` is valid for any department at API level. Explicit confirmation is required.
- **Errors:** `404 NOT_FOUND` for dealership/vehicle plus validation/idempotency errors.

#### `GET /api/callback-requests/{recordId}`

- **Inputs/output:** path ID; complete callback record including PII.
- **Auth/state/idempotency:** API key required; read-only.
- **Errors:** `404 NOT_FOUND`.

#### `POST /api/part-exchange-valuations`

- **Inputs:** `dealershipId` (max 80), `registration`, `mileage` (integer 0–1,000,000), `condition` (`excellent | good | fair`), and common contact.
- **Output:** `id`, `reference` (`PX-*`), supplied details, `estimateLowPence`, `estimateHighPence`, `status: estimated`, `createdAt`, and `estimateNotice`.
- **Validation:** registration is normalized to uppercase and must be 2–10 characters containing only letters, digits, and spaces.
- **Mutation/constraints:** persists a deterministic indicative range. It remains subject to inspection, provenance checks, and market conditions; never describe it as a guaranteed offer. Confirm before sharing PII and creating the record.
- **Errors:** `404 NOT_FOUND` for dealership plus validation/idempotency errors.

#### `GET /api/part-exchange-valuations/{recordId}`

- **Inputs/output:** path ID; saved valuation including PII and estimates. Unlike create, this raw record does not include `estimateNotice`; the adapter must obtain the notice from business information or retain it.
- **Auth/state/idempotency:** API key required; read-only.
- **Errors:** `404 NOT_FOUND`.

## Workshop

### `GET /api/service-types`

- **Inputs:** none.
- **Output:** `{ items }` with `id`, name, description, `durationMinutes`, and nullable `priceFromPence`.
- **Auth/state/idempotency:** public, read-only.
- **Constraints/errors:** “from” prices are not final quotes; no business error is defined.

### `GET /api/workshop-locations`

- **Inputs:** none.
- **Output:** `{ items }` in dealership shape: ID/name, address/town/postcode, phone/email, coordinates, and `brands[]`.
- **Auth/state/idempotency:** public, read-only.
- **Quirk:** the implementation returns all four dealerships; it has no separate workshop-enabled flag.

### `GET /api/workshop-availability`

- **Inputs:** optional `dealershipId`, `serviceTypeId`, `dateFrom`, and `dateTo`.
- **Output:** `{ items }`; available slots only, including IDs, `startsAt`, status, dealership/service names, duration, and nullable price-from pence.
- **Auth/state/idempotency:** public, live read.
- **Validation and constraints:** inclusive date filters are expected as `YYYY-MM-DD` but not explicitly validated. Unknown IDs yield no matches. Results are chronological and capped at 100. No availability is a valid result; seeded Bolton has none in the first seven days.

### `POST /api/workshop-bookings`

- **Inputs:** required `Idempotency-Key`; `slotId` (max 80), `registration`, `mileage` (integer 0–1,000,000), contact, and optional `notes` (max 2,000).
- **Output:** `id`, `reference` (`WORK-*`), slot/dealership/service IDs, vehicle/contact details, `status: confirmed`, `createdAt`, `updatedAt`, and null `cancelledAt`.
- **Mutation:** atomically claims an available slot and creates a confirmed booking. Explicit confirmation is required.
- **Constraints/errors:** registration rules match part exchange. Claimed/blocked slot returns `409 SLOT_UNAVAILABLE`; unknown slot returns `404 NOT_FOUND`; missing/invalid idempotency key and normal validation errors also apply.

### `POST /api/workshop-bookings/lookup`

- **Inputs:** `reference`, `lastName`, `registration`, and `phone`; all four are required. Reference is case-insensitive via uppercase normalization, surname is case-insensitive, registration ignores spaces/case, and phone uses standard normalization.
- **Output:** complete booking plus `startsAt`, `serviceTypeName`, and dealership name/address/town/postcode.
- **Auth/state/idempotency:** API key required; logically a read; no idempotency key.
- **Security constraint:** every value must match. Any mismatch returns the same `404 BOOKING_NOT_FOUND` without identifying the wrong field. Preserve that non-disclosure. A successful lookup establishes a harness verification grant for that booking/session; the Northstar API itself does not enforce that grant on later ID-based operations.
- **Errors:** `422 VALIDATION_ERROR` for malformed/missing identity fields; `404 BOOKING_NOT_FOUND` for no full match.

### `GET /api/workshop-bookings/{recordId}`

- **Inputs/output:** path internal booking ID; raw saved booking including PII, but not the joined appointment/service/dealership display fields returned by lookup.
- **Auth/state/idempotency:** API key required; read-only. Use only after verified lookup or for backend reconciliation.
- **Errors:** `404 NOT_FOUND`.

### `PATCH /api/workshop-bookings/{recordId}`

- **Inputs:** one or more of `slotId`, `mileage`, or `notes`; no other properties. Mileage is 0–1,000,000; notes are nullable/max 2,000; slot ID max 80.
- **Output:** the complete updated raw booking with a new `updatedAt` and `status: confirmed`.
- **Auth/state/idempotency:** API key required; mutating; no idempotency-key support. Require an active verification grant and explicit confirmation, then reconcile on ambiguous timeout.
- **Mutation:** changing slots occurs atomically: the new slot is reserved and the old slot released. The booking's dealership and service type become those of the new slot, so the harness must show all three changes in confirmation. Mileage/notes may be changed independently.
- **Constraints/errors:** cancelled bookings return `409 BOOKING_CANCELLED`; unavailable new slots return `409 SLOT_UNAVAILABLE` and leave the booking unchanged; unknown booking/slot returns `404 NOT_FOUND`; an empty or extra-field body returns `422 VALIDATION_ERROR`.

### `DELETE /api/workshop-bookings/{recordId}`

- **Inputs:** path internal booking ID; no body.
- **Output:** complete booking with `status: cancelled`, `cancelledAt`, and updated `updatedAt`.
- **Auth/state/idempotency:** API key required; consequential mutation; require verified lookup and explicit confirmation. Repeating cancellation returns the already-cancelled booking.
- **Mutation/constraints:** releases the booked slot. Unknown booking returns `404 NOT_FOUND`.

## Dealership

### `GET /api/dealerships`

- **Inputs:** none.
- **Output:** `{ items }` with ID/name, address/town/postcode, phone/email, latitude/longitude, and `brands[]`.
- **Auth/state/idempotency:** public, read-only.

### `GET /api/dealerships/{dealershipId}`

- **Inputs/output:** path ID; one dealership in the same shape as the list.
- **Auth/state/idempotency:** public, read-only.
- **Errors:** unknown dealership returns `404 NOT_FOUND`.

### `GET /api/dealerships/{dealershipId}/opening-hours`

- **Inputs:** path dealership ID.
- **Output:** `dealershipId`, `weekly[]`, and `holidayExceptions[]`. Weekly rows contain department, day name/index, open/close times, and `closed`; exception rows contain department, date, label, open/close times, and `closed`.
- **Auth/state/idempotency:** public, read-only.
- **Constraints:** departments are separate (`sales`, `service`, `parts`). Holiday exceptions override ordinary weekly hours; null open/close values mean closed. The adapter should calculate “open now” only after applying the exception for the requested local date.
- **Errors:** unknown dealership returns `404 NOT_FOUND`.

### `POST /api/dealership-messages`

- **Inputs:** `dealershipId` (max 80), `department` (`sales | service | parts | general`), `subject` (3–120 characters), `message` (5–2,000), `preferredContactMethod` (`email | phone`), and common contact.
- **Output:** `id`, `reference` (`MSG-*`), supplied fields, `status: received`, and `createdAt`.
- **Auth/state/idempotency:** API key required; creates a record; optional-but-recommended idempotency key. Explicit confirmation is required.
- **Constraints/errors:** “received” means saved for the department, not replied to. Unknown dealership returns `404 NOT_FOUND`; validation/idempotency errors apply.

### `GET /api/dealership-messages/{recordId}`

- **Inputs/output:** path ID; complete message including PII.
- **Auth/state/idempotency:** API key required; read-only.
- **Errors:** `404 NOT_FOUND`.

### `GET /api/business-information`

- **Inputs:** none.
- **Output:** organisation, currency, market, finance notice/minimum age, part-exchange estimate notice, and privacy contact.
- **Auth/state/idempotency:** public, read-only.
- **Constraint:** surface the supplied finance and estimate qualifications rather than generating legal wording.

## Error and state mapping

| Platform code | Adapter meaning | Harness action |
| --- | --- | --- |
| `VALIDATION_ERROR` | User-correctable input | Map `fieldErrors` to collected fields; preserve valid state |
| `NOT_FOUND` | Referenced resource disappeared/never existed | Refresh relevant catalogue or return to selection |
| `BOOKING_NOT_FOUND` | Workshop identity combination failed | Give generic failure; do not reveal which value mismatched |
| `SLOT_UNAVAILABLE` | Selected slot lost a race or is blocked | Keep details, refresh slots, ask user to reselect/reconfirm |
| `VEHICLE_RESERVED` | Vehicle became reserved | Offer interest registration or sales enquiry |
| `VEHICLE_UNAVAILABLE` | Vehicle is no longer test-drive eligible | Refresh vehicle state; offer enquiry where useful |
| `VEHICLE_NOT_RESERVED` | Interest target is no longer reserved | Refresh state; do not create interest |
| `BOOKING_CANCELLED` | Amendment attempted after cancellation | Report current state; do not retry |
| `IDEMPOTENCY_KEY_REQUIRED` / `INVALID_IDEMPOTENCY_KEY` | Adapter request defect | Do not ask the user to repair it; log and fail safely |
| `IDEMPOTENCY_CONFLICT` | Key/body invariant was violated | Stop retries, log at high severity, reconcile original record |
| `UNAUTHORISED` | Server configuration error | Do not expose credentials; show temporary unavailability |
| `INTERNAL_ERROR` | Platform failure | Retry only under the common safe-retry policy |

Canonical status meanings are: `received` = saved enquiry/message; `requested` = callback recorded; `registered` = reserved-vehicle interest recorded; `confirmed` = slot successfully claimed; `estimated` = indicative valuation only; `cancelled` = workshop slot released.

## Harness integration requirements

1. Cache slow-changing catalogue data briefly, but never treat cached vehicle or slot state as commit authority.
2. Store one stable idempotency key and exact payload with each pending create action. Generate a new key only when the user starts a genuinely new action.
3. Keep a typed action summary for confirmation. Re-check live availability immediately before showing it, then let the platform arbitrate the final race.
4. Persist returned internal IDs and customer-facing references separately. Show the reference to the user; keep internal IDs out of model-generated prose.
5. Gate workshop get/amend/cancel behind a session-scoped successful lookup. API-key possession alone is not customer authorization.
6. Redact names, email, phone, registration, free-text notes/messages, API keys, and lookup combinations from logs. Record operation, latency, result code, resource ID, and idempotency/retry metadata.
7. Resolve `/assets/...` against the configured platform URL and build website links as `/?vehicle={vehicleId}` rather than inventing vehicle URLs.
8. Treat empty result sets—especially Bolton workshop availability—as valid business truth, not a retryable platform error.

## Non-business and administrative endpoints

`GET /health`, `GET /openapi.json`, `GET /docs`, static assets, and the Systems Console are operational surfaces, not dealer-interface capabilities. `GET /admin/api/summary` is unauthenticated in the supplied local platform and contains saved-record summaries; it must not be proxied by the chatbot. `POST /admin/api/reset` requires `X-Admin-Key`, destroys local business records, and is restricted to development/test tooling. Neither admin endpoint belongs in the Northstar adapter.
