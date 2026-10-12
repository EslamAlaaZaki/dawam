import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const FILES = [
  "StarDiagram.tsx",
  "LineageGraph.tsx",
  "lineageLayout.ts",
  "BusMatrix.tsx",
  "StarPanel.tsx",
  "../workspaces/SourceErDiagram.tsx",
];

describe("diagram colours", () => {
  it.each(FILES)("%s has no hard-coded colour (a theme switch must restyle it)", (file) => {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    expect(source).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(source).not.toMatch(/\b(?:rgb|hsl)a?\(/);
  });
});
