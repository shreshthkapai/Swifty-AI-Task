import assert from "node:assert/strict";
import test from "node:test";

import { createWebchat, pageObservation } from "../src/features/webchat/webchat.js";
import { TestDocument, TestEvent, deferred } from "./dom.js";

function message(role, text, id = "message-1") {
  return {
    schema_version: 1, message_id: id, client_turn_id: "turn-1", role,
    created_at: "2026-08-14T10:00:00+00:00", text,
    blocks: text ? [] : [{ kind: "text", payload: { schema_version: 1, text: "I found three vehicles." } }],
  };
}

function setup(apiOverrides = {}) {
  const document = new TestDocument();
  const root = document.createElement("div");
  const api = {
    getSession: async () => ({ schema_version: 1, revision: 0, messages: [] }),
    sendTurn: async () => ({ schema_version: 1, revision: 1, duplicate: false, messages: [message("user", "Hello", "u1"), message("assistant", null, "a1")] }),
    deleteSession: async () => undefined,
    ...apiOverrides,
  };
  let id = 0;
  const chat = createWebchat({
    root,
    api,
    document,
    idFactory: () => `turn-${++id}`,
    getPageObservation: () => ({ current_url: "/#vehicles", page_vehicle_id: "veh-003" }),
  });
  return { document, root, api, chat };
}

test("session restoration renders saved transcript and launcher remains closed", async () => {
  const { root, chat } = setup({
    getSession: async () => ({ schema_version: 1, revision: 4, messages: [message("user", "Show me an SUV")] }),
  });

  await chat.init();

  assert.equal(root.querySelector('[data-role="panel"]').hidden, true);
  assert.match(root.querySelector('[data-role="transcript"]').textContent, /Show me an SUV/);
  assert.equal(root.querySelector('[data-role="launcher"]').getAttribute("aria-expanded"), "false");
});

test("failed session restoration exposes a working retry", async () => {
  let attempts = 0;
  const { root, chat } = setup({
    getSession: async () => {
      attempts += 1;
      if (attempts === 1) throw Object.assign(new Error("History unavailable."), { retryable: true });
      return { schema_version: 1, revision: 1, messages: [message("user", "Restored after retry")] };
    },
  });
  await chat.init();

  assert.equal(root.querySelector('[data-role="retry"]').hidden, false);
  root.querySelector('[data-role="retry"]').click();
  await chat.whenIdle();

  assert.equal(attempts, 2);
  assert.match(root.querySelector('[data-role="transcript"]').textContent, /Restored after retry/);
});

test("session loading disables the composer until restoration finishes", async () => {
  const pending = deferred();
  const { root, chat } = setup({ getSession: async () => pending.promise });

  const loading = chat.init();

  assert.equal(root.querySelector("textarea").disabled, true);
  assert.equal(root.querySelector('[data-role="panel"]').getAttribute("aria-busy"), "true");
  pending.resolve({ schema_version: 1, revision: 0, messages: [] });
  await loading;
  assert.equal(root.querySelector("textarea").disabled, false);
});

test("launcher opens a focus-managed panel and Escape closes it", async () => {
  const { document, root, chat } = setup();
  await chat.init();
  const launcher = root.querySelector('[data-role="launcher"]');
  launcher.click();

  assert.equal(root.querySelector('[data-role="panel"]').hidden, false);
  assert.equal(launcher.hidden, true);
  assert.equal(launcher.getAttribute("aria-expanded"), "true");
  assert.equal(root.querySelector(".ns-chat-header").hidden, false);
  assert.equal(
    root.querySelector('[data-role="new-conversation"]').textContent,
    "New chat",
  );
  assert.equal(document.activeElement, root.querySelector("textarea"));

  root.querySelector('[data-role="panel"]').dispatchEvent(new TestEvent("keydown", { key: "Escape" }));
  assert.equal(root.querySelector('[data-role="panel"]').hidden, true);
  assert.equal(launcher.hidden, false);
  assert.equal(document.activeElement, launcher);
});

test("wheel input at transcript boundaries does not scroll the dealership page", async () => {
  const { root, chat } = setup();
  await chat.init();
  const transcript = root.querySelector('[data-role="transcript"]');
  transcript.clientHeight = 300;
  transcript.scrollHeight = 900;

  transcript.scrollTop = 600;
  const pastBottom = new TestEvent("wheel", { deltaY: 40 });
  transcript.dispatchEvent(pastBottom);
  assert.equal(pastBottom.defaultPrevented, true);

  transcript.scrollTop = 300;
  const withinTranscript = new TestEvent("wheel", { deltaY: 40 });
  transcript.dispatchEvent(withinTranscript);
  assert.equal(withinTranscript.defaultPrevented, false);

  transcript.scrollTop = 0;
  const pastTop = new TestEvent("wheel", { deltaY: -40 });
  transcript.dispatchEvent(pastTop);
  assert.equal(pastTop.defaultPrevented, true);
});

test("completed response anchors its opening instead of jumping below long cards", async () => {
  const pending = deferred();
  const { root, chat } = setup({ sendTurn: async () => pending.promise });
  await chat.init();
  const transcript = root.querySelector('[data-role="transcript"]');
  transcript.clientHeight = 300;
  transcript.scrollHeight = 900;
  transcript.scrollTop = 600;

  root.querySelector("textarea").value = "Choose one for me";
  root.querySelector("form").dispatchEvent(new TestEvent("submit"));
  transcript.scrollCalls = [];
  pending.resolve({
    schema_version: 1,
    revision: 1,
    duplicate: false,
    messages: [
      message("user", "Choose one for me", "u1"),
      message("assistant", "I would choose the second vehicle.", "a1"),
    ],
  });
  await chat.whenIdle();

  assert.equal(transcript.scrollCalls.length, 1);
  assert.notEqual(transcript.scrollCalls[0].top, Number.MAX_SAFE_INTEGER);
  assert.equal(transcript.scrollCalls[0].behavior, "smooth");
});

test("completed response preserves position when customer scrolls up while waiting", async () => {
  const pending = deferred();
  const { root, chat } = setup({ sendTurn: async () => pending.promise });
  await chat.init();
  const transcript = root.querySelector('[data-role="transcript"]');
  transcript.clientHeight = 300;
  transcript.scrollHeight = 900;

  root.querySelector("textarea").value = "Show me options";
  root.querySelector("form").dispatchEvent(new TestEvent("submit"));
  transcript.scrollTop = 120;
  transcript.scrollCalls = [];
  pending.resolve({
    schema_version: 1,
    revision: 1,
    duplicate: false,
    messages: [message("assistant", "I found several options.", "a1")],
  });
  await chat.whenIdle();

  assert.equal(transcript.scrollTop, 120);
  assert.deepEqual(transcript.scrollCalls, []);
});

test("streamed answer appears incrementally and follows only while already at bottom", async () => {
  const pending = deferred();
  let emitDelta;
  const { root, chat } = setup({
    sendTurn: async (_payload, { onTextDelta }) => {
      emitDelta = onTextDelta;
      return pending.promise;
    },
  });
  await chat.init();
  const transcript = root.querySelector('[data-role="transcript"]');
  transcript.clientHeight = 300;
  transcript.scrollHeight = 900;
  transcript.scrollTop = 600;
  root.querySelector("textarea").value = "Help me choose";
  root.querySelector("form").dispatchEvent(new TestEvent("submit"));

  transcript.scrollCalls = [];
  emitDelta("A quick ");
  emitDelta("answer.");
  assert.equal(root.querySelector(".ns-chat-streaming").textContent, "A quick answer.");
  assert.equal(transcript.scrollTop, 600);

  transcript.scrollTop = 120;
  transcript.scrollCalls = [];
  emitDelta(" More detail.");
  assert.equal(transcript.scrollTop, 120);
  assert.deepEqual(transcript.scrollCalls, []);

  pending.resolve({
    schema_version: 1,
    revision: 1,
    duplicate: false,
    messages: [
      message("user", "Help me choose", "u1"),
      message("assistant", "A quick answer. More detail.", "a1"),
    ],
  });
  await chat.whenIdle();
  assert.equal(root.querySelector(".ns-chat-streaming"), null);
});

test("sending text includes live page context and blocks duplicate sends", async () => {
  const pending = deferred();
  const payloads = [];
  const { root, chat } = setup({
    sendTurn: async (payload) => { payloads.push(payload); return pending.promise; },
  });
  await chat.init();
  root.querySelector('[data-role="launcher"]').click();
  const composer = root.querySelector("textarea");
  composer.value = "Can I test drive this?";
  const form = root.querySelector("form");
  form.dispatchEvent(new TestEvent("submit"));
  form.dispatchEvent(new TestEvent("submit"));

  assert.equal(payloads.length, 1);
  assert.deepEqual(payloads[0], {
    client_turn_id: "turn-1",
    text: "Can I test drive this?",
    page_observation: { current_url: "/#vehicles", page_vehicle_id: "veh-003" },
  });
  assert.equal(root.querySelector('[data-role="send"]').disabled, true);

  pending.resolve({ schema_version: 1, revision: 1, duplicate: false, messages: [message("user", "Can I test drive this?", "u1"), message("assistant", null, "a1")] });
  await chat.whenIdle();
  assert.equal(root.querySelector("textarea").disabled, false);
  assert.equal(root.querySelector('[data-role="panel"]').getAttribute("aria-busy"), "false");
});

test("server-issued action buttons submit their exact opaque reference", async () => {
  const payloads = [];
  const actionMessage = {
    ...message("assistant", null),
    blocks: [{ kind: "actions", payload: { schema_version: 1, actions: [{ action_id: "opaque-1", action_type: "show_more", label: "Show more", entity_id: "group-1" }] } }],
  };
  const { root, chat } = setup({
    getSession: async () => ({ schema_version: 1, revision: 1, messages: [actionMessage] }),
    sendTurn: async (payload) => { payloads.push(payload); return { schema_version: 1, revision: 2, duplicate: false, messages: [] }; },
  });
  await chat.init();

  root.querySelector('[data-action-id="opaque-1"]').click();
  await chat.whenIdle();

  assert.deepEqual(payloads[0], {
    client_turn_id: "turn-1",
    action: { action_id: "opaque-1", action_type: "show_more" },
    page_observation: { current_url: "/#vehicles", page_vehicle_id: "veh-003" },
  });
  assert.equal(root.querySelector('[data-action-id="opaque-1"]').disabled, true);
});

test("Enter sends while Shift+Enter preserves a newline", async () => {
  const payloads = [];
  const { root, chat } = setup({
    sendTurn: async (payload) => { payloads.push(payload); return { schema_version: 1, revision: 1, duplicate: false, messages: [] }; },
  });
  await chat.init();
  const composer = root.querySelector("textarea");
  composer.value = "First line";
  const shifted = new TestEvent("keydown", { key: "Enter", shiftKey: true, isComposing: false });
  composer.dispatchEvent(shifted);
  assert.equal(shifted.defaultPrevented, false);

  const enter = new TestEvent("keydown", { key: "Enter", shiftKey: false, isComposing: false });
  composer.dispatchEvent(enter);
  await chat.whenIdle();
  assert.equal(enter.defaultPrevented, true);
  assert.equal(payloads.length, 1);
});

test("multiline composer grows to its bounded content height", async () => {
  const { root, chat } = setup();
  await chat.init();
  const composer = root.querySelector("textarea");
  composer.scrollHeight = 88;

  composer.dispatchEvent(new TestEvent("input"));

  assert.equal(composer.style.height, "88px");
});

test("recoverable failure keeps the attempted turn and offers safe retry", async () => {
  let attempts = 0;
  const { root, chat } = setup({
    sendTurn: async () => {
      attempts += 1;
      if (attempts === 1) throw Object.assign(new Error("Please try again."), { retryable: true });
      return { schema_version: 1, revision: 1, duplicate: false, messages: [] };
    },
  });
  await chat.init();
  root.querySelector("textarea").value = "Show me BMWs";
  root.querySelector("form").dispatchEvent(new TestEvent("submit"));
  await chat.whenIdle();

  assert.match(root.querySelector('[data-role="error"]').textContent, /Please try again/);
  root.querySelector('[data-role="retry"]').click();
  await chat.whenIdle();
  assert.equal(attempts, 2);
});

test("new conversation requires an in-panel confirmation before deletion", async () => {
  let deleted = 0;
  const { root, chat } = setup({ deleteSession: async () => { deleted += 1; } });
  await chat.init();
  root.querySelector('[data-role="new-conversation"]').click();
  assert.equal(deleted, 0);
  assert.equal(root.querySelector('[data-role="reset-confirmation"]').hidden, false);

  root.querySelector('[data-role="confirm-reset"]').click();
  await chat.whenIdle();
  assert.equal(deleted, 1);
  assert.match(root.querySelector('[data-role="transcript"]').textContent, /How can I help/);
});

test("page observation exposes only relative URL, current vehicle and visible filters", () => {
  const controls = {
    make: { value: "BMW" }, body: { value: "SUV" }, fuel: { value: "" },
    price: { value: "3500000" }, sort: { value: "newest" }, query: { value: "" },
  };

  assert.deepEqual(pageObservation({
    location: { pathname: "/", hash: "#vehicles", search: "?vehicle=veh-003" },
    controls,
  }), {
    current_url: "/#vehicles",
    page_vehicle_id: "veh-003",
    search_filters: { make: "BMW", body_style: "SUV", max_price_minor: 3500000, sort: "newest" },
  });
});
