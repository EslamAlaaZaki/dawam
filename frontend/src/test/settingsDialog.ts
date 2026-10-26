import { fireEvent, screen, within } from "@testing-library/react";

/** Opens the Workspace settings dialog, on `tab` when given, and returns the dialog. */
export async function openSettings(
  tab?: "Details" | "AI" | "Document AI" | "Archive & delete",
) {
  fireEvent.click(
    await screen.findByRole("button", { name: "Workspace settings" }),
  );
  const dialog = await screen.findByRole("dialog", {
    name: "Workspace settings",
  });
  if (tab) {
    const trigger = await within(dialog).findByRole("tab", { name: tab });
    // Radix tabs activate on mouse down, not click.
    fireEvent.mouseDown(trigger, { button: 0, ctrlKey: false });
  }
  return dialog;
}
