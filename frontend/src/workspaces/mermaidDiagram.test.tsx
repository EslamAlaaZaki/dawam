import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Markdown } from "./chatParts";

const mermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(),
}));
vi.mock("mermaid", () => ({ default: mermaid }));

const DIAGRAM = "erDiagram\n    fact_loan }o--|| dim_customer : customer_key";

beforeEach(() => {
  mermaid.initialize.mockClear();
  mermaid.render.mockReset();
});

describe("a mermaid block in an answer", () => {
  it("is drawn by the library in strict mode", async () => {
    mermaid.render.mockResolvedValue({ svg: '<svg data-testid="drawn"></svg>' });

    render(<Markdown>{`Here:\n\n\`\`\`mermaid\n${DIAGRAM}\n\`\`\``}</Markdown>);

    expect(await screen.findByTestId("drawn")).toBeInTheDocument();
    expect(mermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ securityLevel: "strict", startOnLoad: false }),
    );
    expect(mermaid.render).toHaveBeenCalledWith(expect.any(String), DIAGRAM);
  });

  it("shows its source when it does not parse", async () => {
    mermaid.render.mockRejectedValue(new Error("Parse error"));

    render(<Markdown>{`\`\`\`mermaid\n${DIAGRAM}\n\`\`\``}</Markdown>);

    await waitFor(() => expect(mermaid.render).toHaveBeenCalled());
    expect(screen.getByText(/fact_loan/)).toBeInTheDocument();
  });

  it("leaves other code blocks alone and never renders raw HTML", () => {
    render(<Markdown>{"```sql\nselect 1\n```\n\n<script>alert(1)</script>"}</Markdown>);

    expect(screen.getByText("select 1")).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
    expect(mermaid.render).not.toHaveBeenCalled();
  });
});
