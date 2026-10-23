import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("shows the title as a heading with its description and actions", () => {
    render(
      <PageHeader
        title="Users"
        description="Everyone who can sign in."
        actions={<button type="button">Invite people</button>}
      />,
    );

    expect(screen.getByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(screen.getByText("Everyone who can sign in.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Invite people" })).toBeInTheDocument();
  });

  it("leaves out the description and actions when none are given", () => {
    const { container } = render(<PageHeader title="Users" />);

    expect(container.querySelector("p")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("is not a second banner landmark next to the shell's top bar", () => {
    render(<PageHeader title="Users" />);

    expect(screen.queryByRole("banner")).toBeNull();
  });
});
