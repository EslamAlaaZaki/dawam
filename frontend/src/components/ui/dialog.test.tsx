import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "./dialog";

function Example() {
  return (
    <Dialog>
      <DialogTrigger>Open it</DialogTrigger>
      <DialogContent aria-describedby={undefined}>
        <DialogTitle>Are you sure</DialogTitle>
        <button type="button">Confirm</button>
      </DialogContent>
    </Dialog>
  );
}

describe("a dialog", () => {
  it("moves focus into itself, wraps Tab inside, and gives focus back on close", async () => {
    render(<Example />);
    const trigger = screen.getByRole("button", { name: "Open it" });
    trigger.focus();

    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Are you sure" });
    await waitFor(() =>
      expect(dialog).toContainElement(document.activeElement as HTMLElement),
    );

    // Tab from the last control wraps to the first, rather than leaving for the page.
    const close = screen.getByRole("button", { name: "Close" });
    close.focus();
    fireEvent.keyDown(close, { key: "Tab" });
    expect(screen.getByRole("button", { name: "Confirm" })).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(trigger).toHaveFocus();
  });

  it("has a close button with a name", async () => {
    render(<Example />);
    fireEvent.click(screen.getByRole("button", { name: "Open it" }));

    expect(await screen.findByRole("button", { name: "Close" })).toBeVisible();
  });
});
