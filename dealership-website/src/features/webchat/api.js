const DEFAULT_BASE_URL = "http://localhost:4020";

export class ChatApiError extends Error {
  constructor(message, { code = "chat_unavailable", status = 0, retryable = true } = {}) {
    super(message);
    this.name = "ChatApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

function endpoint(baseUrl, path) {
  return `${String(baseUrl || DEFAULT_BASE_URL).replace(/\/$/, "")}${path}`;
}

async function parseResponse(response) {
  if (response.status === 204) return undefined;
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const safeError = payload?.error;
    throw new ChatApiError(
      typeof safeError?.message === "string"
        ? safeError.message
        : "Chat is temporarily unavailable. Please try again.",
      {
        code: typeof safeError?.code === "string" ? safeError.code : "chat_unavailable",
        status: response.status,
        retryable: response.status === 409 || response.status >= 500,
      },
    );
  }
  return validatePayload(payload, response.status);
}

function validatePayload(payload, status = 200) {
  if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.messages)) {
    throw new ChatApiError("Chat returned an unexpected response.", {
      code: "invalid_response",
      status,
      retryable: true,
    });
  }
  return payload;
}

async function parseTurnStream(response, onTextDelta) {
  if (!response.ok) return parseResponse(response);
  if (!response.body || typeof response.body.getReader !== "function") {
    throw new ChatApiError("Chat returned an unexpected response.", {
      code: "invalid_response", status: response.status, retryable: true,
    });
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = null;

  function consume(line) {
    if (!line.trim()) return;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      throw new ChatApiError("Chat returned an unexpected response.", {
        code: "invalid_response", status: response.status, retryable: true,
      });
    }
    if (event?.type === "text_delta" && typeof event.delta === "string") {
      onTextDelta?.(event.delta);
      return;
    }
    if (event?.type === "complete") {
      if (completed !== null) throw new ChatApiError("Chat returned duplicate completion data.", {
        code: "invalid_response", status: response.status, retryable: true,
      });
      completed = validatePayload(event.payload, response.status);
      return;
    }
    if (event?.type === "error") {
      throw new ChatApiError(
        typeof event.error?.message === "string"
          ? event.error.message
          : "Chat is temporarily unavailable. Please try again.",
        {
          code: typeof event.error?.code === "string" ? event.error.code : "chat_unavailable",
          status: response.status,
          retryable: event.error?.retryable !== false,
        },
      );
    }
    throw new ChatApiError("Chat returned an unexpected response.", {
      code: "invalid_response", status: response.status, retryable: true,
    });
  }

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = done ? "" : lines.pop();
    for (const line of lines) consume(line);
    if (done) {
      if (buffer) consume(buffer);
      break;
    }
  }
  if (completed === null) {
    throw new ChatApiError("Chat response ended before completion.", {
      code: "incomplete_response", status: response.status, retryable: true,
    });
  }
  return completed;
}

export function createChatApi({ baseUrl = DEFAULT_BASE_URL, fetchImpl = globalThis.fetch } = {}) {
  if (typeof fetchImpl !== "function") throw new TypeError("fetchImpl must be a function");

  async function request(path, options) {
    try {
      return await parseResponse(await fetchImpl(endpoint(baseUrl, path), options));
    } catch (error) {
      if (error instanceof ChatApiError) throw error;
      throw new ChatApiError("Chat is temporarily unavailable. Please try again.", {
        code: "network_error",
        retryable: true,
      });
    }
  }

  return {
    getSession() {
      return request("/api/chat/session", {
        method: "GET",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
    },

    async sendTurn(payload, { onTextDelta } = {}) {
      try {
        const response = await fetchImpl(endpoint(baseUrl, "/api/chat/turns/stream"), {
          method: "POST",
          credentials: "include",
          headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        return await parseTurnStream(response, onTextDelta);
      } catch (error) {
        if (error instanceof ChatApiError) throw error;
        throw new ChatApiError("Chat is temporarily unavailable. Please try again.", {
          code: "network_error", retryable: true,
        });
      }
    },

    async deleteSession() {
      try {
        const response = await fetchImpl(endpoint(baseUrl, "/api/chat/session"), {
          method: "DELETE",
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (response.status === 204) return;
        await parseResponse(response);
      } catch (error) {
        if (error instanceof ChatApiError) throw error;
        throw new ChatApiError("The conversation could not be cleared. Please try again.", {
          code: "network_error",
          retryable: true,
        });
      }
    },
  };
}
