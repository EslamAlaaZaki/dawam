import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "./chatParts";

const BLOCK = JSON.stringify([
  { name: "Churn rate", rationale: "crm.customers has a closed_at column", unit: "%" },
  { name: "Net revenue", rationale: "fact_sales holds amounts", definition: "Sales less returns" },
]);

describe("a dawam-kpis block in an answer", () => {
  it("renders one card per suggestion", () => {
    render(<Markdown>{`Here are KPIs.\n\n\`\`\`dawam-kpis\n${BLOCK}\n\`\`\``}</Markdown>);

    const cards = within(screen.getByRole("list", { name: "Suggested KPIs" })).getAllByRole(
      "listitem",
    );
    expect(cards).toHaveLength(2);
    expect(within(cards[0]!).getByText("Churn rate")).toBeInTheDocument();
    expect(within(cards[0]!).getByText("%")).toHaveClass("font-mono");
    expect(within(cards[1]!).getByText("Sales less returns")).toBeInTheDocument();
    expect(screen.getByText("Here are KPIs.")).toBeInTheDocument();
  });

  it("shows a block that is not valid JSON as plain code", () => {
    render(<Markdown>{'```dawam-kpis\n[{"name": "Chu\n```'}</Markdown>);

    expect(screen.queryByRole("list", { name: "Suggested KPIs" })).toBeNull();
    expect(screen.getByText(/"name": "Chu/)).toBeInTheDocument();
  });

  it("shows a list with a missing field as plain code", () => {
    render(<Markdown>{'```dawam-kpis\n[{"name": "Churn"}]\n```'}</Markdown>);

    expect(screen.queryByRole("list", { name: "Suggested KPIs" })).toBeNull();
  });
});
