import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Components use Next.js's router; tests drive an in-memory one instead.
vi.mock("next/navigation", () => import("./navigation"));
vi.mock("next/link", () => import("./link"));

// React Flow (the diagrams) measures its canvas, which jsdom cannot.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
globalThis.DOMMatrixReadOnly = class {
  m22 = 1;
} as unknown as typeof DOMMatrixReadOnly;

afterEach(() => {
  cleanup();
});
