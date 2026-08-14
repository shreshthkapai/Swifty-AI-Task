import assert from "node:assert/strict";
import test from "node:test";

import { createVehicleContextHost } from "../src/features/vehicle-detail/vehicle-context.js";

function parent(name) {
  return {
    name,
    childNodes: [],
    append(node) {
      if (node.parentElement) {
        node.parentElement.childNodes = node.parentElement.childNodes.filter(
          (candidate) => candidate !== node,
        );
      }
      node.parentElement = this;
      this.childNodes.push(node);
    },
  };
}

test("vehicle context keeps the persistent chat inside the modal and restores it on close", () => {
  const home = parent("page");
  const dialog = parent("vehicle dialog");
  const chatRoot = { parentElement: null, preservedConversation: "conversation-42" };
  home.append(chatRoot);
  const contextHost = createVehicleContextHost({ dialog, chatRoot, home });

  contextHost.mount();

  assert.equal(chatRoot.parentElement, dialog);
  assert.equal(dialog.childNodes[0], chatRoot);
  assert.equal(chatRoot.preservedConversation, "conversation-42");

  contextHost.restore();

  assert.equal(chatRoot.parentElement, home);
  assert.equal(home.childNodes[0], chatRoot);
  assert.equal(chatRoot.preservedConversation, "conversation-42");
});
