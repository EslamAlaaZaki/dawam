import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * Teaches tailwind-merge the theme's extra font sizes (theme.css `--text-*`).
 * Without this it reads `text-mono` as a text colour and drops a real colour
 * such as the tooltip's `text-background`.
 */
const twMerge = extendTailwindMerge({
  extend: {
    theme: { text: ["2xs", "mono", "md"] },
  },
});

/** Joins class names, letting a later Tailwind utility override an earlier one. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
