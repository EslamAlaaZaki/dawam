import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Components use Next.js's router; tests drive an in-memory one instead.
vi.mock("next/navigation", () => import("./navigation"));

afterEach(() => {
  cleanup();
});
