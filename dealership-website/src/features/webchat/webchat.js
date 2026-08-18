import { renderMarkdown } from "./markdown.js";
import { renderMessage } from "./render.js";

function element(document, tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function control(document, { role, label, className = "", text = label }) {
  const button = element(document, "button", className, text);
  button.type = "button";
  button.dataset.role = role;
  button.setAttribute("aria-label", label);
  return button;
}

function shell(document) {
  const launcher = control(document, {
    role: "launcher", label: "Open Northstar AI assistant", className: "ns-chat-launcher", text: "",
  });
  launcher.setAttribute("aria-expanded", "false");
  launcher.setAttribute("aria-controls", "northstar-chat-panel");
  launcher.append(
    element(document, "span", "ns-chat-launcher__mark", "✦"),
    element(document, "span", "ns-chat-launcher__label", "Ask AI"),
    element(document, "span", "ns-chat-launcher__unread", "New"),
  );

  const panel = element(document, "section", "ns-chat-panel");
  panel.id = "northstar-chat-panel";
  panel.dataset.role = "panel";
  panel.hidden = true;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "false");
  panel.setAttribute("aria-labelledby", "northstar-chat-title");

  const header = element(document, "header", "ns-chat-header");
  const identity = element(document, "div", "ns-chat-identity");
  identity.append(element(document, "span", "ns-chat-identity__mark", "N"));
  const identityCopy = element(document, "div");
  const heading = element(document, "h2", null, "Northstar AI");
  heading.id = "northstar-chat-title";
  const availability = element(document, "p", "ns-chat-availability", "Online · Ready to help");
  availability.dataset.role = "availability";
  identityCopy.append(heading, availability);
  identity.append(identityCopy);
  const headerControls = element(document, "div", "ns-chat-header__controls");
  const reset = control(document, {
    role: "new-conversation",
    label: "Start new conversation",
    className: "ns-chat-icon-button ns-chat-new-button",
    text: "New chat",
  });
  const close = control(document, { role: "close", label: "Close assistant", className: "ns-chat-icon-button", text: "×" });
  headerControls.append(reset, close);
  header.append(identity, headerControls);

  const status = element(document, "div", "ns-chat-status", "Loading your conversation…");
  status.dataset.role = "status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");

  const transcript = element(document, "div", "ns-chat-transcript");
  transcript.dataset.role = "transcript";
  transcript.setAttribute("role", "log");
  transcript.setAttribute("aria-live", "polite");
  transcript.setAttribute("aria-relevant", "additions text");

  const error = element(document, "div", "ns-chat-error");
  error.dataset.role = "error";
  error.setAttribute("role", "alert");
  error.hidden = true;
  const errorText = element(document, "p");
  const retry = control(document, { role: "retry", label: "Retry message", className: "ns-chat-error__retry", text: "Try again" });
  error.append(errorText, retry);

  const resetConfirmation = element(document, "div", "ns-chat-reset-confirmation");
  resetConfirmation.dataset.role = "reset-confirmation";
  resetConfirmation.setAttribute("role", "alertdialog");
  resetConfirmation.hidden = true;
  resetConfirmation.append(
    element(document, "p", null, "Start a new conversation? This clears the current chat on this browser."),
  );
  const resetActions = element(document, "div", "ns-chat-actions");
  resetActions.append(
    control(document, { role: "cancel-reset", label: "Keep conversation", className: "ns-chat-action", text: "Keep chat" }),
    control(document, { role: "confirm-reset", label: "Clear and start again", className: "ns-chat-action ns-chat-action--danger", text: "Start new" }),
  );
  resetConfirmation.append(resetActions);

  const form = element(document, "form", "ns-chat-composer");
  const field = element(document, "div", "ns-chat-composer__field");
  const textarea = element(document, "textarea");
  textarea.rows = 1;
  textarea.maxLength = 4000;
  textarea.placeholder = "Ask about cars, test drives or servicing…";
  textarea.setAttribute("aria-label", "Message Northstar AI");
  const send = control(document, { role: "send", label: "Send message", className: "ns-chat-send", text: "↑" });
  send.type = "submit";
  field.append(textarea, send);
  form.append(field, element(document, "p", "ns-chat-composer__hint", "Enter to send · Shift + Enter for a new line"));

  panel.append(header, status, transcript, error, resetConfirmation, form);
  return { launcher, panel, status, transcript, error, errorText, retry, resetConfirmation, reset, close, form, textarea, send, availability };
}

function contextualPrompts(observation) {
  const url = observation?.current_url || "";
  const vehicleId = observation?.page_vehicle_id;
  const filters = observation?.search_filters;

  if (vehicleId) {
    return {
      heading: "Interested in this vehicle?",
      subtitle: "I can help with details, availability, test drives or finance.",
      prompts: ["Is this car available?", "Book a test drive for this car", "What finance options are there?"],
    };
  }

  if (url.includes("service") || url.includes("workshop")) {
    return {
      heading: "Need a service or repair?",
      subtitle: "I can help you book, reschedule or check on a workshop appointment.",
      prompts: ["Book a service", "Check my booking status", "What services do you offer?"],
    };
  }

  if (url.includes("location") || url.includes("contact")) {
    return {
      heading: "Looking for a dealership?",
      subtitle: "I can find opening hours, directions and contact details.",
      prompts: ["Find my nearest dealership", "What are your opening hours?", "Send a message to the team"],
    };
  }

  if (url.includes("vehicles") || filters) {
    return {
      heading: "Looking for the right car?",
      subtitle: "Tell me what you're after and I'll search our stock.",
      prompts: ["Show me electric SUVs", "What's available under £25,000?", "Compare the top options"],
    };
  }

  return {
    heading: "How can I help today?",
    subtitle: "Search current stock, arrange a test drive, manage a workshop booking or find a dealership.",
    prompts: ["Show me available SUVs", "Book a service", "Find my nearest dealership"],
  };
}

function welcome(document, onPrompt, observation) {
  const ctx = contextualPrompts(observation);
  const container = element(document, "section", "ns-chat-welcome");
  container.append(
    element(document, "p", "ns-chat-kicker", "Northstar assistant"),
    element(document, "h3", null, ctx.heading),
    element(document, "p", "ns-chat-muted", ctx.subtitle),
  );
  const prompts = element(document, "div", "ns-chat-prompts");
  for (const prompt of ctx.prompts) {
    const button = element(document, "button", "ns-chat-prompt", prompt);
    button.type = "button";
    button.addEventListener("click", () => onPrompt(prompt));
    prompts.append(button);
  }
  container.append(prompts);
  return container;
}

function typingIndicator(document) {
  const wrapper = element(document, "article", "ns-chat-message ns-chat-message--assistant ns-chat-typing");
  wrapper.setAttribute("aria-label", "Northstar AI is typing");
  const bubble = element(document, "div", "ns-chat-typing-dots");
  bubble.append(
    element(document, "span", "ns-chat-dot"),
    element(document, "span", "ns-chat-dot"),
    element(document, "span", "ns-chat-dot"),
  );
  wrapper.append(bubble);
  return wrapper;
}

function streamingMessage(document, text) {
  const message = renderMessage(document, {
    message_id: "streaming",
    role: "assistant",
    text,
    blocks: [],
  }, { onAction: () => {} });
  message.classList.add("ns-chat-streaming");
  message.setAttribute("aria-label", "Northstar AI is responding");
  return message;
}

function suggestFollowUps(lastMessage) {
  if (!lastMessage || lastMessage.role !== "assistant") return [];
  const blocks = lastMessage.blocks || [];
  const kinds = new Set(blocks.map((b) => b.kind));

  if (kinds.has("vehicle_cards")) {
    return ["Tell me more about the first one", "Anything cheaper?", "Book a test drive"];
  }
  if (kinds.has("confirmation")) {
    return [];
  }
  if (kinds.has("slot_choices")) {
    return ["Show me different dates", "A different location?"];
  }
  if (kinds.has("dealerships")) {
    return ["What are their opening hours?", "Send them a message"];
  }
  if (kinds.has("workshop_services")) {
    return ["Book a service", "How long does it take?"];
  }
  if (kinds.has("opening_hours")) {
    return ["Are you open this weekend?", "Book an appointment"];
  }

  if (lastMessage.text) {
    return ["Tell me more", "Show me some options"];
  }
  return [];
}

function renderChips(document, suggestions, onPrompt) {
  if (!suggestions.length) return null;
  const container = element(document, "div", "ns-chat-chips");
  for (const text of suggestions) {
    const chip = element(document, "button", "ns-chat-chip", text);
    chip.type = "button";
    chip.addEventListener("click", () => onPrompt(text));
    container.append(chip);
  }
  return container;
}

function cleanFilters(controls) {
  if (!controls) return undefined;
  const configured = [
    ["query", "query", (value) => value],
    ["make", "make", (value) => value],
    ["body", "body_style", (value) => value],
    ["fuel", "fuel_type", (value) => value],
    ["price", "max_price_minor", (value) => Number(value)],
    ["sort", "sort", (value) => value],
  ];
  const result = {};
  for (const [controlName, key, transform] of configured) {
    const value = controls[controlName]?.value;
    if (value) result[key] = transform(value);
  }
  return Object.keys(result).length ? result : undefined;
}

export function pageObservation({ location, controls }) {
  const parameters = new URLSearchParams(location.search || "");
  const observation = { current_url: `${location.pathname || "/"}${location.hash || ""}` };
  const vehicleId = parameters.get("vehicle");
  if (vehicleId) observation.page_vehicle_id = vehicleId;
  const searchFilters = cleanFilters(controls);
  if (searchFilters) observation.search_filters = searchFilters;
  return observation;
}

export function createWebchat({ root, api, getPageObservation, document = globalThis.document, idFactory = () => crypto.randomUUID() }) {
  if (!root || !api || typeof getPageObservation !== "function") throw new TypeError("root, api and getPageObservation are required");
  const nodes = shell(document);
  root.classList.add("ns-chat");
  root.replaceChildren(nodes.panel, nodes.launcher);
  let messages = [];
  let open = false;
  let busy = false;
  let inFlight = Promise.resolve();
  let optimisticText = null;
  let streamingText = "";
  let retryOperation = null;
  let submittedActionIds = new Set();

  function rememberSubmittedActions(items) {
    for (const message of items) {
      for (const block of message.blocks || []) {
        const actionId = block.kind === "action_submission" && block.payload?.action_id;
        if (actionId) submittedActionIds.add(actionId);
      }
    }
  }

  function setStatus(text, loading = false) {
    nodes.status.textContent = text;
    nodes.status.hidden = !text;
    root.classList.toggle("is-loading", loading);
  }

  function renderTranscript({ anchor = "preserve", behavior = "smooth" } = {}) {
    const previousTop = nodes.transcript.scrollTop;
    const wasAtBottom = (
      previousTop + nodes.transcript.clientHeight
      >= nodes.transcript.scrollHeight - 2
    );
    const fragment = document.createDocumentFragment();
    if (!messages.length && !optimisticText) fragment.append(welcome(document, submitText, getPageObservation()));
    for (const item of messages) fragment.append(renderMessage(document, item, { onAction: submitAction }));
    if (optimisticText) {
      fragment.append(renderMessage(document, {
        message_id: "optimistic", role: "user", text: optimisticText, blocks: [],
      }, { onAction: submitAction }));
    }
    if (!busy && !optimisticText && messages.length) {
      const chips = renderChips(document, suggestFollowUps(messages[messages.length - 1]), submitText);
      if (chips) fragment.append(chips);
    }
    if (busy && !streamingText) fragment.append(typingIndicator(document));
    if (streamingText) fragment.append(streamingMessage(document, streamingText));
    nodes.transcript.replaceChildren(fragment);
    applyBusyState();
    if (anchor === "bottom") {
      nodes.transcript.scrollTo({ top: Number.MAX_SAFE_INTEGER, behavior });
      return;
    }
    if (anchor === "response" && wasAtBottom) {
      const assistantMessages = nodes.transcript.querySelectorAll(".ns-chat-message--assistant");
      const response = assistantMessages[assistantMessages.length - 1];
      if (response) {
        nodes.transcript.scrollTo({
          top: Math.max(0, response.offsetTop - nodes.transcript.offsetTop - 4),
          behavior,
        });
      }
      return;
    }
    nodes.transcript.scrollTop = previousTop;
  }

  function appendTextDelta(delta) {
    if (!delta) return;
    const wasAtBottom = (
      nodes.transcript.scrollTop + nodes.transcript.clientHeight
      >= nodes.transcript.scrollHeight - 2
    );
    const indicator = nodes.transcript.querySelector(".ns-chat-typing");
    if (indicator) indicator.remove();
    streamingText += delta;
    let draft = nodes.transcript.querySelector(".ns-chat-streaming");
    if (!draft) {
      draft = streamingMessage(document, streamingText);
      nodes.transcript.append(draft);
    } else {
      draft.querySelector(".ns-chat-bubble").innerHTML = renderMarkdown(streamingText);
    }
    setStatus("", true);
    if (wasAtBottom) {
      nodes.transcript.scrollTop = Math.max(
        0,
        nodes.transcript.scrollHeight - nodes.transcript.clientHeight,
      );
    }
  }

  function applyBusyState() {
    nodes.textarea.disabled = busy;
    nodes.send.disabled = busy || !nodes.textarea.value.trim();
    for (const button of nodes.transcript.querySelectorAll("button")) {
      button.disabled = busy || submittedActionIds.has(button.dataset.actionId);
    }
    nodes.panel.setAttribute("aria-busy", String(busy));
  }

  function showError(error, retry = null) {
    nodes.errorText.textContent = error?.message || "Chat is temporarily unavailable. Please try again.";
    retryOperation = error?.retryable ? retry : null;
    nodes.retry.hidden = retryOperation === null;
    nodes.error.hidden = false;
  }

  function clearError() {
    nodes.error.hidden = true;
    nodes.errorText.textContent = "";
    retryOperation = null;
  }

  function setOpen(nextOpen) {
    open = nextOpen;
    nodes.panel.hidden = !open;
    nodes.launcher.hidden = open;
    nodes.launcher.setAttribute("aria-expanded", String(open));
    root.classList.toggle("is-open", open);
    if (open) {
      root.classList.remove("has-unread");
      nodes.transcript.scrollTo({ top: nodes.transcript.scrollHeight, behavior: "instant" });
      nodes.textarea.focus();
    } else {
      nodes.launcher.focus();
    }
  }

  async function perform(payload, displayText = null) {
    if (busy) return;
    busy = true;
    optimisticText = displayText;
    streamingText = "";
    clearError();
    setStatus("Northstar AI is working…", true);
    renderTranscript({ anchor: "bottom", behavior: "instant" });
    let completed = false;
    try {
      const result = await api.sendTurn(payload, { onTextDelta: appendTextDelta });
      messages.push(...result.messages);
      rememberSubmittedActions(result.messages);
      optimisticText = null;
      streamingText = "";
      completed = true;
      if (!open) root.classList.add("has-unread");
    } catch (error) {
      streamingText = "";
      showError(error, () => {
        inFlight = perform(payload, displayText);
        return inFlight;
      });
    } finally {
      busy = false;
      setStatus("", false);
      renderTranscript({ anchor: completed ? "response" : "preserve", behavior: "instant" });
      if (open) nodes.textarea.focus();
    }
  }

  function payloadFor(content) {
    const payload = { client_turn_id: idFactory(), ...content };
    const observation = getPageObservation();
    if (observation && Object.keys(observation).length) payload.page_observation = observation;
    return payload;
  }

  function submitText(value = nodes.textarea.value) {
    const text = String(value || "").trim();
    if (!text || busy) return;
    nodes.textarea.value = "";
    nodes.textarea.style.height = "auto";
    const payload = payloadFor({ text });
    inFlight = perform(payload, text);
  }

  function submitAction(action) {
    if (busy || !action?.action_id || !action?.action_type) return;
    submittedActionIds.add(action.action_id);
    inFlight = perform(payloadFor({ action: { action_id: action.action_id, action_type: action.action_type } }));
  }

  async function resetConversation() {
    if (busy) return;
    busy = true;
    applyBusyState();
    clearError();
    try {
      await api.deleteSession();
      messages = [];
      submittedActionIds = new Set();
      optimisticText = null;
      streamingText = "";
      nodes.resetConfirmation.hidden = true;
    } catch (error) {
      showError(error, () => {
        inFlight = resetConversation();
        return inFlight;
      });
    } finally {
      busy = false;
      applyBusyState();
      renderTranscript({ anchor: "bottom", behavior: "auto" });
      nodes.textarea.focus();
    }
  }

  if (typeof globalThis.addEventListener === "function") {
    globalThis.addEventListener("hashchange", () => {
      if (!messages.length && !busy) renderTranscript();
    });
  }
  nodes.launcher.addEventListener("click", () => setOpen(true));
  nodes.close.addEventListener("click", () => setOpen(false));
  nodes.panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    }
  });
  nodes.transcript.addEventListener("wheel", (event) => {
    const atTop = nodes.transcript.scrollTop <= 0;
    const atBottom = nodes.transcript.scrollTop + nodes.transcript.clientHeight >= nodes.transcript.scrollHeight - 1;
    if ((event.deltaY < 0 && atTop) || (event.deltaY > 0 && atBottom)) event.preventDefault();
  }, { passive: false });
  nodes.form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitText();
  });
  nodes.textarea.addEventListener("input", () => {
    nodes.textarea.style.height = "auto";
    nodes.textarea.style.height = `${Math.min(nodes.textarea.scrollHeight, 104)}px`;
    applyBusyState();
  });
  nodes.textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submitText();
    }
  });
  nodes.reset.addEventListener("click", () => {
    nodes.resetConfirmation.hidden = false;
    root.querySelector('[data-role="cancel-reset"]').focus();
  });
  root.querySelector('[data-role="cancel-reset"]').addEventListener("click", () => {
    nodes.resetConfirmation.hidden = true;
    nodes.textarea.focus();
  });
  root.querySelector('[data-role="confirm-reset"]').addEventListener("click", () => {
    inFlight = resetConversation();
  });
  nodes.retry.addEventListener("click", () => {
    if (retryOperation === null || busy) return;
    const operation = retryOperation;
    retryOperation = null;
    operation();
  });

  async function loadSession() {
    if (busy) return;
    busy = true;
    clearError();
    setStatus("Loading your conversation…", true);
    applyBusyState();
    try {
      const session = await api.getSession();
      messages = session.messages;
      rememberSubmittedActions(messages);
      nodes.availability.textContent = "Online · Ready to help";
      clearError();
    } catch (error) {
      showError(error, () => {
        inFlight = loadSession();
        return inFlight;
      });
      nodes.availability.textContent = "Temporarily unavailable";
    } finally {
      busy = false;
      setStatus("", false);
      renderTranscript({ anchor: "bottom", behavior: "auto" });
    }
  }

  return {
    init() {
      inFlight = loadSession();
      return inFlight;
    },
    whenIdle() { return inFlight; },
    open() { setOpen(true); },
    close() { setOpen(false); },
  };
}
