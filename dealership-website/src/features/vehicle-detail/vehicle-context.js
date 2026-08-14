export function createVehicleContextHost({ dialog, chatRoot, home }) {
  if (!dialog?.append || !chatRoot || !home?.append) {
    throw new TypeError("dialog, chatRoot and home are required");
  }
  return {
    mount() {
      if (chatRoot.parentElement !== dialog) dialog.append(chatRoot);
    },
    restore() {
      if (chatRoot.parentElement !== home) home.append(chatRoot);
    },
  };
}
