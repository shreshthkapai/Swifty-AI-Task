export class TestEvent {
  constructor(type, properties = {}) {
    this.type = type;
    this.defaultPrevented = false;
    Object.assign(this, properties);
  }

  preventDefault() {
    this.defaultPrevented = true;
  }
}

class TestClassList {
  constructor(element) {
    this.element = element;
  }

  _values() {
    return new Set(this.element.className.split(/\s+/).filter(Boolean));
  }

  add(...names) {
    const values = this._values();
    names.forEach((name) => values.add(name));
    this.element.className = [...values].join(" ");
  }

  remove(...names) {
    const values = this._values();
    names.forEach((name) => values.delete(name));
    this.element.className = [...values].join(" ");
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.contains(name) : force;
    enabled ? this.add(name) : this.remove(name);
    return enabled;
  }

  contains(name) {
    return this._values().has(name);
  }
}

function matches(element, selector) {
  if (selector.startsWith("#")) return element.id === selector.slice(1);
  if (selector.startsWith(".")) return element.classList.contains(selector.slice(1));
  const attribute = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
  if (attribute) {
    const value = element.getAttribute(attribute[1]);
    return attribute[2] === undefined ? value !== null : value === attribute[2];
  }
  return element.tagName.toLowerCase() === selector.toLowerCase();
}

export class TestElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.parentElement = null;
    this.childNodes = [];
    this.attributes = new Map();
    this.dataset = {};
    this.className = "";
    this.classList = new TestClassList(this);
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.style = {};
    this.clientHeight = 0;
    this.scrollTop = 0;
    this.scrollHeight = 36;
    this.offsetTop = 0;
    this.scrollCalls = [];
    this._text = "";
    this.listeners = new Map();
  }

  set id(value) { this.setAttribute("id", value); }
  get id() { return this.getAttribute("id") || ""; }

  set textContent(value) {
    this._text = String(value ?? "");
    this.childNodes = [];
  }

  get textContent() {
    return this._text + this.childNodes.map((child) => child.textContent).join("");
  }

  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === "string"
        ? this.ownerDocument.createTextNode(node)
        : node;
      child.parentElement = this;
      this.childNodes.push(child);
    }
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.childNodes = [];
    this._text = "";
    this.append(...nodes);
  }

  remove() {
    if (!this.parentElement) return;
    this.parentElement.childNodes = this.parentElement.childNodes.filter((item) => item !== this);
    this.parentElement = null;
  }

  setAttribute(name, value) {
    const text = String(value);
    this.attributes.set(name, text);
    if (name === "class") this.className = text;
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, character) => character.toUpperCase());
      this.dataset[key] = text;
    }
  }

  getAttribute(name) {
    if (name === "class") return this.className || null;
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_, character) => character.toUpperCase());
      return this.dataset[key] ?? null;
    }
    return this.attributes.get(name) ?? null;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
  }

  dispatchEvent(event) {
    event.target ||= this;
    for (const listener of this.listeners.get(event.type) || []) listener(event);
    return !event.defaultPrevented;
  }

  click() {
    if (!this.disabled) this.dispatchEvent(new TestEvent("click"));
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const result = [];
    for (const child of this.childNodes) {
      if (matches(child, selector)) result.push(child);
      result.push(...child.querySelectorAll(selector));
    }
    return result;
  }

  closest(selector) {
    let current = this;
    while (current) {
      if (matches(current, selector)) return current;
      current = current.parentElement;
    }
    return null;
  }

  scrollTo(options) {
    this.scrollCalls.push(options);
    if (typeof options?.top === "number") {
      this.scrollTop = Math.max(
        0,
        Math.min(options.top, Math.max(0, this.scrollHeight - this.clientHeight)),
      );
    }
  }
}

export class TestDocument {
  constructor() {
    this.activeElement = null;
  }

  createElement(tagName) {
    return new TestElement(tagName, this);
  }

  createTextNode(value) {
    const node = new TestElement("#text", this);
    node.textContent = value;
    return node;
  }

  createDocumentFragment() {
    return new TestElement("#fragment", this);
  }
}

export function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
