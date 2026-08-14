import assert from "node:assert/strict";
import test from "node:test";

import { ChatApiError, createChatApi } from "../src/features/webchat/api.js";

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

test("chat API restores the cookie-backed session with credentials", async () => {
  const requests = [];
  const api = createChatApi({
    baseUrl: "http://localhost:4020",
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response(200, { schema_version: 1, revision: 0, messages: [] });
    },
  });

  const session = await api.getSession();

  assert.equal(session.revision, 0);
  assert.deepEqual(requests, [{
    url: "http://localhost:4020/api/chat/session",
    options: { method: "GET", credentials: "include", headers: { Accept: "application/json" } },
  }]);
});

test("chat API sends one strict JSON turn payload", async () => {
  let captured;
  const api = createChatApi({
    baseUrl: "http://localhost:4020/",
    fetchImpl: async (url, options) => {
      captured = { url, options };
      return new Response(
        '{"type":"text_delta","delta":"One "}\n'
          + '{"type":"text_delta","delta":"answer."}\n'
          + '{"type":"complete","payload":{"schema_version":1,"revision":1,"duplicate":false,"messages":[]}}\n',
        { status: 200, headers: { "content-type": "application/x-ndjson" } },
      );
    },
  });
  const payload = {
    client_turn_id: "turn-1",
    text: "Show me BMW SUVs",
    page_observation: { current_url: "/#vehicles", page_vehicle_id: "veh-003" },
  };

  const deltas = [];
  const result = await api.sendTurn(payload, { onTextDelta: (delta) => deltas.push(delta) });

  assert.equal(captured.url, "http://localhost:4020/api/chat/turns/stream");
  assert.equal(captured.options.method, "POST");
  assert.equal(captured.options.credentials, "include");
  assert.deepEqual(JSON.parse(captured.options.body), payload);
  assert.deepEqual(deltas, ["One ", "answer."]);
  assert.equal(result.revision, 1);
});

test("stream errors expose only the safe event envelope", async () => {
  const api = createChatApi({
    fetchImpl: async () => new Response(
      '{"type":"text_delta","delta":"Partial"}\n'
        + '{"type":"error","error":{"code":"chat_unavailable","message":"Please try again.","retryable":true}}\n',
      { status: 200, headers: { "content-type": "application/x-ndjson" } },
    ),
  });

  await assert.rejects(
    () => api.sendTurn({ client_turn_id: "turn-2", text: "hello" }),
    (error) => error instanceof ChatApiError
      && error.code === "chat_unavailable"
      && error.message === "Please try again."
      && error.retryable === true,
  );
});

test("chat API exposes only the server safe error envelope", async () => {
  const api = createChatApi({
    fetchImpl: async () => response(503, {
      error: { code: "planner_unavailable", message: "The assistant is temporarily unavailable. Please try again." },
      request_id: "request-1",
    }),
  });

  await assert.rejects(
    () => api.getSession(),
    (error) => error instanceof ChatApiError
      && error.code === "planner_unavailable"
      && error.message === "The assistant is temporarily unavailable. Please try again."
      && error.retryable === true,
  );
});
