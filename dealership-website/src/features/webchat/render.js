import { assetUrl } from "../../shared/api.js";

const LABELS = {
  address: "Address",
  annual_mileage: "Annual mileage",
  apr_percent: "APR",
  dealership_name: "Dealership",
  duration_minutes: "Duration",
  estimate_high: "Upper estimate",
  estimate_low: "Lower estimate",
  expires_on: "Offer ends",
  finance_minimum_age: "Minimum age",
  finance_notice: "Finance information",
  mileage: "Mileage",
  monthly_price: "Monthly price",
  opens_at: "Opens",
  closes_at: "Closes",
  part_exchange_notice: "Part exchange",
  price_from: "Price from",
  privacy_contact: "Privacy contact",
  registration: "Registration",
  service: "Service",
  service_name: "Service",
  starts_at: "Time",
  status: "Status",
  term_months: "Term",
  upfront_price: "Upfront payment",
  vehicle_id: "Vehicle",
  vehicle_label: "Vehicle",
};

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const ACTION_SUBMISSION_TEXT = {
  cancel: "Action cancelled",
  confirm: "Action confirmed",
  find_test_drive_slots: "Requested test-drive times",
  register_interest: "Requested to register interest",
  retry: "Request retried",
  sales_enquiry: "Sales enquiry selected",
  select_dealership: "Dealership selected",
  select_test_drive_slot: "Test-drive slot selected",
  select_vehicle: "Vehicle selected",
  select_workshop_slot: "Workshop slot selected",
  show_more: "Requested more results",
  start_over: "Started over",
  switch_workflow: "Changed task",
};

const HIDDEN_FIELDS = new Set([
  "id", "dealership_id", "service_type_id", "vehicle_id", "latitude", "longitude",
  "can_book_test_drive", "can_register_interest", "can_enquire", "kind", "currency",
]);

function element(document, tagName, className, text) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function title(value) {
  return LABELS[value] || String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDateTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return String(value);
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(parsed);
}

function formatMoney(value, currency = "GBP") {
  if (value === null || value === undefined) return "Price on request";
  if (typeof value === "object" && typeof value.display === "string") return value.display;
  const amount = typeof value === "object" ? value.amount_minor : value;
  const selectedCurrency = typeof value === "object" ? value.currency || currency : currency;
  if (!Number.isInteger(amount)) return String(value);
  return new Intl.NumberFormat("en-GB", {
    style: "currency", currency: selectedCurrency, maximumFractionDigits: amount % 100 === 0 ? 0 : 2,
  }).format(amount / 100);
}

function formatValue(key, value, record = {}) {
  if (value === null || value === undefined || value === "") return null;
  if (["starts_at", "expires_at"].includes(key)) return formatDateTime(value);
  if (key === "day_of_week" && Number.isInteger(value) && WEEKDAYS[value]) return WEEKDAYS[value];
  if (key === "expires_on" || key === "date") return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(new Date(`${value}T12:00:00`));
  if (key === "mileage") return `${Number(value).toLocaleString("en-GB")} miles`;
  if (key === "duration_minutes") return `${value} minutes`;
  if (key === "term_months") return `${value} months`;
  if (key === "annual_mileage") return `${Number(value).toLocaleString("en-GB")} miles`;
  if (key === "apr_percent") return `${value}% APR`;
  if (["monthly_price", "upfront_price"].includes(key)) return formatMoney(value, record.currency);
  if (["estimate_low", "estimate_high"].includes(key)) return formatMoney(value, record.currency);
  if (key === "price_from") return formatMoney(value, record.currency);
  if (key === "address" && Array.isArray(value)) return value.filter(Boolean).join(", ");
  if (Array.isArray(value)) return value.join(" · ");
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return null;
  return String(value);
}

function actionButton(document, action, onAction, emphasis = false) {
  const button = element(document, "button", `ns-chat-action${emphasis ? " ns-chat-action--primary" : ""}`, action.label || "Select");
  button.type = "button";
  button.dataset.actionId = action.action_id;
  button.dataset.actionType = action.action_type;
  button.addEventListener("click", () => onAction({
    action_id: action.action_id,
    action_type: action.action_type,
  }));
  return button;
}

function actionsFor(payload, entityId) {
  return (payload.actions || []).filter((action) => entityId === undefined || action.entity_id === entityId);
}

function renderActions(document, payload, onAction, className = "ns-chat-actions") {
  const container = element(document, "div", className);
  for (const action of payload.actions || []) {
    container.append(actionButton(document, action, onAction, action.action_type === "confirm"));
  }
  return container;
}

function safeImage(value) {
  if (typeof value !== "string" || value.startsWith("data:") || value.startsWith("javascript:")) return null;
  try {
    const resolved = value.startsWith("/") ? assetUrl(value) : new URL(value);
    return String(resolved).startsWith("http://") || String(resolved).startsWith("https://") ? String(resolved) : null;
  } catch {
    return null;
  }
}

function renderVehicleCards(document, payload, onAction) {
  const list = element(document, "div", "ns-chat-vehicle-list");
  const vehicles = payload.vehicles || [];
  for (const [index, vehicle] of vehicles.entries()) {
    if (index === 1 && payload.recommended_entity_id && vehicles.length > 1) {
      list.append(element(document, "p", "ns-chat-other-options", "Other options"));
    }
    const card = element(document, "article", "ns-chat-vehicle");
    const recommended = payload.recommended_entity_id === vehicle.id;
    if (recommended) card.classList.add("is-recommended");
    const imageUrl = safeImage(vehicle.image_url);
    if (imageUrl) {
      const image = element(document, "img", "ns-chat-vehicle__image");
      image.src = imageUrl;
      image.alt = vehicle.label || `${vehicle.make || ""} ${vehicle.model || ""}`.trim();
      image.loading = "lazy";
      card.append(image);
    }
    const body = element(document, "div", "ns-chat-vehicle__body");
    if (recommended) body.append(element(document, "p", "ns-chat-recommendation", "Recommended"));
    body.append(
      element(document, "p", "ns-chat-kicker", `${vehicle.year} · ${vehicle.dealership_name || "Northstar"}`),
      element(document, "h4", "ns-chat-vehicle__title", `${vehicle.make} ${vehicle.model}`),
      element(document, "p", "ns-chat-muted", vehicle.variant),
      element(document, "p", "ns-chat-vehicle__specs", `${Number(vehicle.mileage).toLocaleString("en-GB")} miles · ${vehicle.fuel_type} · ${vehicle.transmission}`),
    );
    const price = element(document, "div", "ns-chat-vehicle__price");
    price.append(element(document, "strong", null, formatMoney(vehicle.price)));
    if (vehicle.monthly_price) price.append(element(document, "span", null, `${formatMoney(vehicle.monthly_price)} / month`));
    body.append(price);
    const actions = actionsFor(payload, vehicle.id);
    if (actions.length) body.append(renderActions(document, { actions }, onAction));
    card.append(body);
    list.append(card);
  }
  return list;
}

function recordHeading(kind, record) {
  if (kind === "slot_choices") return record.vehicle_label || record.service_name || "Available appointment";
  if (kind === "workshop_services") return record.name || "Workshop service";
  if (kind === "dealerships") return record.name || "Northstar dealership";
  if (kind === "offer_cards") return record.title || `${record.make || ""} ${record.model || ""}`.trim();
  if (kind === "booking_details") return `Booking ${record.reference || "details"}`;
  if (kind === "availability") return `Vehicle ${record.status || "availability"}`;
  if (kind === "opening_hours") {
    return record.label || (Number.isInteger(record.day_of_week) ? WEEKDAYS[record.day_of_week] : "Opening hours");
  }
  if (kind === "vehicle_details") return "Vehicle details";
  if (kind === "action_result") return "Confirmed";
  if (kind === "business_information") return record.organisation || "Northstar Motors";
  return record.name || record.label || title(kind);
}

function renderRecords(document, kind, payload, onAction) {
  const list = element(document, "div", `ns-chat-records ns-chat-records--${kind}`);
  for (const record of payload.items || []) {
    const item = element(document, "article", "ns-chat-record");
    item.append(element(document, "h4", "ns-chat-record__title", recordHeading(kind, record)));
    const details = element(document, "dl", "ns-chat-record__details");
    for (const [key, value] of Object.entries(record)) {
      if (HIDDEN_FIELDS.has(key) || ["name", "title", "label", "description"].includes(key)) continue;
      const formatted = formatValue(key, value, record);
      if (formatted === null) continue;
      const pair = element(document, "div", "ns-chat-record__pair");
      pair.append(element(document, "dt", null, title(key)), element(document, "dd", null, formatted));
      details.append(pair);
    }
    if (record.description) item.append(element(document, "p", "ns-chat-record__description", record.description));
    if (details.childNodes.length) item.append(details);
    const actions = actionsFor(payload, record.id);
    if (actions.length) item.append(renderActions(document, { actions }, onAction));
    list.append(item);
  }
  return list;
}

function renderComparison(document, payload) {
  const wrapper = element(document, "div", "ns-chat-comparison-wrap");
  const table = element(document, "table", "ns-chat-comparison");
  const header = element(document, "tr");
  header.append(element(document, "th", null, "Compare"));
  for (const column of payload.columns || []) header.append(element(document, "th", null, column.label));
  const head = element(document, "thead");
  head.append(header);
  const body = element(document, "tbody");
  for (const row of payload.rows || []) {
    const tr = element(document, "tr");
    tr.append(element(document, "th", null, row.label));
    for (const value of row.values || []) tr.append(element(document, "td", null, value));
    body.append(tr);
  }
  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

function summaryEntries(value, prefix = "") {
  const entries = [];
  for (const [key, item] of Object.entries(value || {})) {
    const name = prefix ? `${prefix} ${title(key)}` : title(key);
    if (item && typeof item === "object" && !Array.isArray(item)) entries.push(...summaryEntries(item, name));
    else {
      const formatted = formatValue(key, item, value);
      if (formatted !== null) entries.push([name, formatted]);
    }
  }
  return entries;
}

function renderConfirmation(document, payload, onAction) {
  const card = element(document, "section", "ns-chat-confirmation");
  card.append(
    element(document, "p", "ns-chat-kicker", "Confirmation required"),
    element(document, "h4", null, "Review before sending"),
  );
  const details = element(document, "dl", "ns-chat-confirmation__summary");
  for (const [label, value] of summaryEntries(payload.summary)) {
    const pair = element(document, "div");
    pair.append(element(document, "dt", null, label), element(document, "dd", null, value));
    details.append(pair);
  }
  card.append(details, renderActions(document, payload, onAction));
  return card;
}

function renderLink(document, payload) {
  const link = element(document, "a", "ns-chat-link", payload.label || "Open link");
  if (typeof payload.href === "string" && (payload.href.startsWith("/") || payload.href.startsWith("#"))) {
    link.setAttribute("href", payload.href);
  }
  return link;
}

function renderBlock(document, block, onAction) {
  const payload = block?.payload || {};
  if (payload.schema_version !== 1) return element(document, "p", "ns-chat-notice", "This response cannot be displayed.");
  if (block.kind === "text") return element(document, "p", "ns-chat-text", payload.text || "");
  if (block.kind === "notice") {
    const notice = element(document, "div", "ns-chat-notice", payload.text || "");
    notice.dataset.noticeCode = payload.code || "notice";
    return notice;
  }
  if (block.kind === "vehicle_cards") return renderVehicleCards(document, payload, onAction);
  if (block.kind === "comparison") return renderComparison(document, payload);
  if (block.kind === "actions") return renderActions(document, payload, onAction);
  if (block.kind === "confirmation") return renderConfirmation(document, payload, onAction);
  if (block.kind === "link") return renderLink(document, payload);
  if (block.kind === "action_submission") {
    return element(
      document,
      "p",
      "ns-chat-action-submission",
      ACTION_SUBMISSION_TEXT[payload.action_type] || "Option selected",
    );
  }
  if (Array.isArray(payload.items)) return renderRecords(document, block.kind, payload, onAction);
  return element(document, "p", "ns-chat-notice", "This response cannot be displayed.");
}

export function renderMessage(document, message, { onAction }) {
  const article = element(document, "article", `ns-chat-message ns-chat-message--${message.role || "assistant"}`);
  article.dataset.messageId = message.message_id || "";
  article.setAttribute("aria-label", message.role === "user" ? "You" : "Northstar AI");
  if (typeof message.text === "string") article.append(element(document, "p", "ns-chat-bubble", message.text));
  for (const block of message.blocks || []) article.append(renderBlock(document, block, onAction));
  return article;
}
