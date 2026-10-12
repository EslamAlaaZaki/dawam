// @vitest-environment node
// The theme switcher (next-themes) sets the theme with an inline script before the page
// paints; the Content-Security-Policy runs it only with the request's nonce.
import { getNonce } from "get-nonce";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Providers } from "./providers";

describe("the app's providers", () => {
  it("put the request's nonce on the theme script", () => {
    const html = renderToString(
      <Providers nonce="request-nonce">
        <p>page</p>
      </Providers>,
    );

    expect(html).toMatch(/<script nonce="request-nonce">[^<]*colorScheme/);
    expect(html).toContain("<p>page</p>");
  });

  it("hand the nonce to the styles dialogs and menus add while open", () => {
    renderToString(<Providers nonce="request-nonce">page</Providers>);

    expect(getNonce()).toBe("request-nonce");
  });
});
