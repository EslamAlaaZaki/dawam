// The toast library (Sonner) adds one fixed <style> element to the page when it loads.
// The production Content-Security-Policy allows exactly that element by its hash; when
// a Sonner upgrade changes the CSS, this fails and TOAST_STYLE_HASH needs the new value.
import { describe, expect, it } from "vitest";

import { TOAST_STYLE_HASH } from "./headers";

async function sha256Source(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return `'sha256-${btoa(String.fromCharCode(...new Uint8Array(digest)))}'`;
}

describe("the toast library's injected style", () => {
  it("is the one the Content-Security-Policy allows by hash", async () => {
    await import("sonner");
    const styles = [...document.head.querySelectorAll("style")].filter((style) =>
      style.textContent?.includes("[data-sonner-toaster]"),
    );

    expect(styles).toHaveLength(1);
    expect(await sha256Source(styles[0]!.textContent!)).toBe(TOAST_STYLE_HASH);
  });
});
