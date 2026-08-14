import assert from "node:assert/strict";
import test from "node:test";

import { renderMessage } from "../src/features/webchat/render.js";
import { TestDocument } from "./dom.js";

const assistant = (blocks) => ({
  schema_version: 1,
  message_id: "message-1",
  client_turn_id: "turn-1",
  role: "assistant",
  created_at: "2026-08-14T10:00:00+00:00",
  text: null,
  blocks,
});

test("message rendering treats model text as inert text", () => {
  const document = new TestDocument();
  const message = assistant([{ kind: "text", payload: { schema_version: 1, text: "<img src=x onerror=alert(1)>" } }]);

  const rendered = renderMessage(document, message, { onAction() {} });

  assert.equal(rendered.textContent, "<img src=x onerror=alert(1)>");
  assert.equal(rendered.querySelector("img"), null);
});

test("vehicle cards render dealer facts and dispatch only their server action reference", () => {
  const document = new TestDocument();
  const actions = [];
  const message = assistant([{
    kind: "vehicle_cards",
    payload: {
      schema_version: 1,
      recommended_entity_id: "veh-003",
      vehicles: [{
        id: "veh-003", label: "2022 BMW X3 xDrive20d M Sport", make: "BMW", model: "X3",
        variant: "xDrive20d M Sport", year: 2022, price: { display: "£34,995" }, monthly_price: null,
        mileage: 18200, fuel_type: "Diesel", transmission: "Automatic", body_style: "SUV",
        colour: "Black", availability: "available", dealership_id: "dealer-1",
        dealership_name: "Northstar Manchester", image_url: "/assets/veh-003.svg",
      }],
      actions: [{ action_id: "server-action", action_type: "select_vehicle", entity_id: "veh-003", label: "View vehicle" }],
    },
  }]);

  const rendered = renderMessage(document, message, { onAction: (action) => actions.push(action) });
  rendered.querySelector('[data-action-id="server-action"]').click();

  assert.match(rendered.textContent, /BMW X3/);
  assert.match(rendered.textContent, /£34,995/);
  assert.match(rendered.textContent, /18,200 miles/);
  assert.match(rendered.textContent, /Recommended/);
  assert.equal(rendered.querySelector(".ns-chat-vehicle").classList.contains("is-recommended"), true);
  assert.deepEqual(actions, [{ action_id: "server-action", action_type: "select_vehicle" }]);
});

test("recommended vehicle is followed by a clear other-options separator", () => {
  const document = new TestDocument();
  const base = {
    label: "Volvo XC40 Plus", make: "Volvo", model: "XC40", variant: "Plus",
    price: { display: "Â£43,250" }, monthly_price: null, mileage: 9750,
    fuel_type: "Electric", transmission: "Automatic", body_style: "SUV",
    colour: "Black Stone", availability: "available", dealership_id: "dealer-1",
    dealership_name: "Northstar Stockport", image_url: null,
  };
  const message = assistant([{
    kind: "vehicle_cards",
    payload: {
      schema_version: 1,
      recommended_entity_id: "veh-recommended",
      vehicles: [
        { ...base, id: "veh-recommended", year: 2025 },
        { ...base, id: "veh-other", year: 2022, colour: "Crystal White" },
      ],
      actions: [],
    },
  }]);

  const rendered = renderMessage(document, message, { onAction() {} });
  const list = rendered.querySelector(".ns-chat-vehicle-list");

  assert.deepEqual(
    list.childNodes.map((item) => item.className),
    ["ns-chat-vehicle is-recommended", "ns-chat-other-options", "ns-chat-vehicle"],
  );
  assert.equal(list.childNodes[1].textContent, "Other options");
});

test("confirmation renders an explicit summary with confirm and cancel controls", () => {
  const document = new TestDocument();
  const message = assistant([{
    kind: "confirmation",
    payload: {
      schema_version: 1,
      pending_action_id: "pending-1",
      pending_action_type: "test_drive_booking",
      summary: { vehicle_id: "veh-003", slot_id: "slot-1", customer: { first_name: "Alex" } },
      expires_at: "2026-08-14T11:00:00+00:00",
      actions: [
        { action_id: "pending-1", action_type: "confirm", label: "Confirm" },
        { action_id: "pending-1", action_type: "cancel", label: "Cancel" },
      ],
    },
  }]);

  const rendered = renderMessage(document, message, { onAction() {} });

  assert.match(rendered.textContent, /Review before sending/);
  assert.match(rendered.textContent, /Vehicle/);
  assert.equal(rendered.querySelectorAll("button").length, 2);
});

test("record, comparison, notice and safe link blocks remain readable", () => {
  const document = new TestDocument();
  const blocks = [
    { kind: "notice", payload: { schema_version: 1, code: "slot_unavailable", text: "That slot is no longer available." } },
    { kind: "slot_choices", payload: { schema_version: 1, items: [{ id: "slot-1", vehicle_label: "BMW X3", starts_at: "2026-08-15T10:00:00+01:00" }], actions: [] } },
    { kind: "comparison", payload: { schema_version: 1, columns: [{ vehicle_id: "veh-1", label: "BMW X3" }], rows: [{ label: "Price", values: ["£34,995"] }] } },
    { kind: "link", payload: { schema_version: 1, label: "View on website", href: "javascript:alert(1)" } },
  ];

  const rendered = renderMessage(document, assistant(blocks), { onAction() {} });

  assert.match(rendered.textContent, /slot is no longer available/);
  assert.match(rendered.textContent, /BMW X3/);
  assert.match(rendered.textContent, /£34,995/);
  assert.equal(rendered.querySelector("a").getAttribute("href"), null);
});

test("opening hours and valuation results use customer-readable values", () => {
  const document = new TestDocument();
  const blocks = [
    {
      kind: "opening_hours",
      payload: {
        schema_version: 1,
        items: [{ kind: "regular", day_of_week: 0, department: "sales", opens_at: "09:00", closes_at: "18:00", closed: false }],
      },
    },
    {
      kind: "action_result",
      payload: {
        schema_version: 1,
        items: [{ id: "valuation-1", estimate_low: 950000, estimate_high: 1100000, currency: "GBP" }],
      },
    },
  ];

  const rendered = renderMessage(document, assistant(blocks), { onAction() {} });

  assert.match(rendered.textContent, /Monday/);
  assert.match(rendered.textContent, /£9,500/);
  assert.match(rendered.textContent, /£11,000/);
  assert.doesNotMatch(rendered.textContent, /950000/);
});
