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

// The theme switcher asks whether the system prefers dark; jsdom has no media queries.
if (typeof window !== "undefined") {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      onchange: null,
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

afterEach(() => {
  cleanup();
  if (typeof window !== "undefined") {
    localStorage.clear();
    document.documentElement.className = "";
  }
});
