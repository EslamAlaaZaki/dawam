import { fireEvent } from "@testing-library/react";

/** Opens a Radix menu from its trigger the way the keyboard does (Enter on the focused trigger). */
export function openMenu(trigger: HTMLElement): void {
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "Enter" });
}
