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
      return response(200, { schema_version: 1, revision: 1, duplicate: false, messages: [] });
    },
  });
  const payload = {
    client_turn_id: "turn-1",
    text: "Show me BMW SUVs",
    page_observation: { current_url: "/#vehicles", page_vehicle_id: "veh-003" },
  };

  await api.sendTurn(payload);

  assert.equal(captured.url, "http://localhost:4020/api/chat/turns");
  assert.equal(captured.options.method, "POST");
  assert.equal(captured.options.credentials, "include");
  assert.deepEqual(JSON.parse(captured.options.body), payload);
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
