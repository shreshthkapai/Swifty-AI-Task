# Dealer Integration Process

> **Solution design 2 of 6 — How.** This document turns dealer-system discovery into a repeatable integration method whose output is a typed, dealer-specific adapter contract.
>
> Previous: [Harness Approach](./HARNESS-APPROACH.md) · Next: [Northstar Adapter Specification](./NORTHSTAR-ADAPTER-SPEC.md)

## Context

Northstar Motors is the first dealer integration for the car-dealership agent harness.

While reverse-engineering the supplied Northstar platform, I separated dealer-specific behaviour from the reusable dealership-domain layer used by the harness. This document captures that discovery and integration process as a repeatable approach for adding future dealerships without coupling the core agent runtime to one backend.

The concrete output of applying this process to the supplied platform is:

- [`NORTHSTAR-ADAPTER-SPEC.md`](./NORTHSTAR-ADAPTER-SPEC.md) — Northstar-specific API, business semantics, error handling, mutation safety, and adapter requirements.
- The harness architecture/runtime then consumes the resulting dealership-domain interface rather than Northstar's raw HTTP API.

This process is intentionally practical rather than framework-heavy: the immediate goal is a strong Northstar implementation, while preserving a clean path for future dealer integrations.

---

## Core Principle

> **The harness owns dealership conversation behaviour; the dealer adapter owns translation to a specific dealer's systems.**

The harness understands domain concepts such as:

- vehicles and availability;
- vehicle search and comparison;
- test drives;
- enquiries and callbacks;
- part exchange;
- dealerships and opening hours;
- servicing/workshops;
- booking, amendment, lookup, and cancellation.

A dealer adapter understands:

- endpoint paths;
- authentication;
- payload/response shapes;
- dealer-specific identifiers;
- validation;
- native error codes;
- retry/idempotency semantics;
- dealer-specific business rules;
- platform quirks.

Dealer-specific HTTP details should not leak into the core harness.

---

## Repeatable Integration Process

### 1. Identify Sources of Truth

Collect and inspect the available dealer-system sources:

- API/OpenAPI documentation;
- backend/server implementation where available;
- integration guides;
- business-semantics documents;
- contract/integration tests;
- seed or test data;
- admin/system consoles;
- existing frontend behaviour;
- configuration/environment variables.

If sources disagree, document the mismatch and define which source is treated as authoritative.

When implementation and tests are available, executable behaviour generally provides the strongest evidence of what the platform actually does.

**Output:** source-of-truth inventory.

---

### 2. Build the Capability Map

Map the dealer system into the common dealership domains supported by the harness.

#### Vehicles / Sales

Determine support for:

- inventory search;
- vehicle details;
- live availability;
- offers;
- sales enquiries;
- callbacks;
- test-drive discovery;
- test-drive booking;
- reserved-vehicle interest/waitlist;
- part exchange;
- finance-related information.

#### Workshop / Aftersales

Determine support for:

- service types;
- workshop locations;
- live availability;
- booking;
- booking lookup;
- amendment;
- cancellation.

#### Dealership Information

Determine support for:

- dealership locations;
- departments;
- opening hours;
- holiday exceptions;
- contact information;
- dealership messages.

Unsupported capabilities should be recorded explicitly rather than inferred at runtime.

**Output:** dealer capability matrix.

---

### 3. Reverse-Engineer Each Operation

For every supported operation, document:

#### Inputs

- required and optional fields;
- data types;
- formats;
- allowed values;
- normalization rules;
- size/range limits.

#### Outputs

- internal identifiers;
- customer-facing references;
- prices/currency;
- timestamps;
- status values;
- nullable fields;
- related entities.

#### Access

- public/private;
- authentication mechanism;
- authorization requirements;
- whether PII is returned.

#### State

Classify the operation as:

- static/slow-changing read;
- live read;
- create;
- amend;
- cancel/delete;
- customer verification/authentication operation.

#### Errors

Document:

- validation errors;
- not-found behaviour;
- conflicts/races;
- authentication failures;
- retryable failures;
- ambiguous outcomes after network interruption.

#### Business Meaning

Record what the operation actually guarantees.

Examples:

- a saved enquiry does not reserve a vehicle;
- a callback request does not guarantee a callback time;
- an indicative valuation is not a guaranteed purchase offer;
- catalogue availability may not represent commit-time availability.

**Output:** operation catalogue.

---

### 4. Identify Live-State and Race Conditions

Identify resources that can change between discovery and mutation, for example:

- vehicle availability;
- test-drive slots;
- workshop slots;
- booking state;
- expiring offers.

For each resource:

- determine whether it must be re-read before confirmation;
- identify the final commit authority;
- document expected conflict responses;
- define recovery behaviour.

Cached information must never be treated as commit-time business truth.

**Output:** freshness and race-condition rules.

---

### 5. Determine Idempotency and Retry Semantics

For each mutation, answer:

1. Does the dealer API support idempotency?
2. Is an idempotency key required?
3. How is the key scoped?
4. What happens on identical replay?
5. What happens if the same key is reused with a changed payload?
6. What happens after a transport timeout?
7. Can the result be reconciled through a protected read?
8. Is repeating cancellation safe?

Define safe retry policies independently for:

- reads;
- creates;
- amendments;
- cancellations.

**Output:** mutation-safety specification.

---

### 6. Extract and Classify Business Rules

Identify rules that affect what the agent may offer or execute.

Examples:

- sold/reserved/available vehicle behaviour;
- test-drive eligibility;
- enquiry eligibility;
- interest-registration eligibility;
- workshop verification requirements;
- finance restrictions;
- estimate/quote qualifications;
- department-specific opening hours;
- holiday overrides.

Classify each rule as either:

#### Dealer-specific behaviour

Belongs in the dealer adapter/specification.

Example:

> Northstar allows sales enquiries against sold vehicles.

#### Shared dealership-agent policy

Belongs in the harness.

Example:

> Consequential actions require explicit customer confirmation.

This prevents dealer quirks from contaminating reusable harness logic while avoiding unnecessary abstraction.

**Output:** business-rule ownership map.

---

### 7. Define Security and Privacy Boundaries

Document:

- credentials and where they may exist;
- calls that expose PII;
- customer-verification requirements;
- session-scoped authorization/verification state;
- sensitive fields that must not be logged;
- administrative/development endpoints that must never be exposed;
- internal errors or credential information that must not reach the customer.

The model must never receive dealer API credentials or arbitrary HTTP access to the dealer platform.

**Output:** security and PII specification.

---

### 8. Normalize Dealer Data into Domain Models

Do not pass dealer-native payloads throughout the harness.

Translate dealer responses into stable dealership-domain models such as:

- `Vehicle`
- `VehicleSearch`
- `VehicleAvailability`
- `Offer`
- `DealerLocation`
- `OpeningHours`
- `TestDriveSlot`
- `TestDriveBooking`
- `WorkshopService`
- `WorkshopSlot`
- `WorkshopBooking`
- `SalesEnquiry`
- `CallbackRequest`
- `PartExchangeEstimate`
- `DomainError`

Dealer IDs can be preserved internally where necessary, but the harness-facing contract should remain consistent.

**Output:** dealer-to-domain mapping.

---

### 9. Define the Harness-Facing Adapter Interface

Expose dealership operations rather than HTTP verbs or endpoint paths.

Example capability surface:

```text
searchVehicles
getVehicle
getVehicleAvailability
listOffers

createSalesEnquiry
listTestDriveSlots
bookTestDrive
registerInterest
requestCallback
valuePartExchange

listServiceTypes
listWorkshopLocations
listWorkshopSlots
bookWorkshop
lookupWorkshopBooking
amendWorkshopBooking
cancelWorkshopBooking

listDealerships
getDealership
getOpeningHours
sendDealershipMessage
getBusinessInformation
```

A dealer integration can explicitly mark a capability as unsupported when its backend does not provide it.

**Output:** typed dealer interface.

---

### 10. Normalize Errors

Map dealer-native errors into stable domain errors consumed by the harness.

Typical categories may include:

- `VALIDATION_ERROR`
- `NOT_FOUND`
- `UNAUTHORISED`
- `SLOT_UNAVAILABLE`
- `VEHICLE_UNAVAILABLE`
- `CONFLICT`
- `VERIFICATION_FAILED`
- `IDEMPOTENCY_ERROR`
- `TEMPORARY_FAILURE`
- `UNSUPPORTED_OPERATION`

Preserve useful structured metadata such as field errors and retryability while avoiding leakage of internal implementation details.

**Output:** error-mapping contract.

---

### 11. Define Caching Rules

Classify data according to freshness requirements.

#### Usually safe for brief caching

Examples:

- dealership locations;
- service types;
- normal opening hours;
- stable business information.

#### Must remain live or be revalidated

Examples:

- vehicle availability;
- workshop slots;
- test-drive slots;
- booking state around a mutation.

Cache behaviour should follow business semantics, not just technical convenience.

**Output:** freshness/cache policy.

---

### 12. Define Observability Requirements

For each adapter operation, capture safe operational metadata such as:

- dealer;
- operation name;
- latency;
- success/failure;
- normalized error code;
- safe resource identifier;
- retry count;
- idempotency/reconciliation metadata.

Avoid logging unnecessary:

- names;
- email addresses;
- phone numbers;
- registrations;
- free-text notes/messages;
- credentials;
- identity-verification combinations.

**Output:** safe operational logging contract.

---

### 13. Build the Edge-Case Catalogue

Derive dealer-specific edge cases from:

- seed data;
- tests;
- documented failures;
- race conditions;
- unusual state combinations;
- unsupported capabilities.

Every meaningful edge case should become:

- an adapter test;
- a harness evaluation scenario;
- or both.

Examples:

- no workshop availability;
- vehicle changes status after being shown;
- slot disappears before confirmation;
- price is unavailable;
- customer verification fails without disclosing which field was wrong;
- mutation times out after potentially succeeding.

**Output:** dealer edge-case suite.

---

### 14. Implement the Adapter

Once the specification is sufficiently complete:

1. implement the domain-facing interface;
2. centralize HTTP/authentication;
3. normalize responses into dealership-domain models;
4. centralize native-to-domain error mapping;
5. implement safe retry/idempotency behaviour;
6. add reconciliation where required;
7. test against the supplied/real dealer platform.

The adapter should not contain LLM prompting or user-facing conversational wording.

---

## Expected Deliverable per Dealer

A dealer integration should produce a dealer-specific specification such as:

```text
<DEALER>-ADAPTER-SPEC.md
```

It should cover, where applicable:

1. purpose and adapter boundary;
2. sources of truth;
3. capability map;
4. protocol/authentication;
5. operation catalogue;
6. business semantics;
7. validation/error mapping;
8. idempotency/retry behaviour;
9. freshness/race conditions;
10. security/PII constraints;
11. harness-facing domain interface;
12. caching rules;
13. observability requirements;
14. unsupported/admin surfaces;
15. edge-case/test catalogue;
16. harness integration requirements.

For this project, applying the process above produced:

- [`NORTHSTAR-ADAPTER-SPEC.md`](./NORTHSTAR-ADAPTER-SPEC.md)

---

## Integration Exit Criteria

A dealer adapter is ready to plug into the harness when:

- supported capabilities are mapped;
- unsupported capabilities are explicit;
- consequential operations have defined retry/idempotency semantics;
- business-critical live state has freshness rules;
- native errors are normalized;
- credentials remain server-side;
- PII handling/logging rules are defined;
- adapter methods expose dealership-domain concepts rather than raw HTTP;
- known edge cases are represented in tests/evaluations;
- integration/contract tests pass;
- core harness code does not require dealer-specific HTTP knowledge.

---

## Result

Adding another dealer should follow the same shape:

```text
Dealer systems
      ↓
Discovery + reverse engineering
      ↓
<DEALER>-ADAPTER-SPEC.md
      ↓
Typed dealer adapter
      ↓
Contract + edge-case tests
      ↓
Existing car-dealership agent harness
```

The purpose is not to introduce abstraction for its own sake.

It is to keep the conversational product stable while isolating the parts that genuinely differ between dealership systems, making future integrations faster, safer, and easier to reason about.
