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
  if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.messages)) {
    throw new ChatApiError("Chat returned an unexpected response.", {
      code: "invalid_response",
      status: response.status,
      retryable: true,
    });
  }
  return payload;
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

    sendTurn(payload) {
      return request("/api/chat/turns", {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
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
