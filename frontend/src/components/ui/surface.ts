/**
 * What every floating part (dialog, menu, popover, select list) shares: they render in
 * a portal outside `.dawam-ui`, so they set the redesign's text style themselves.
 */
export const surfaceClasses =
  "border border-border bg-surface-raised font-sans text-base/[1.45] text-foreground shadow-overlay antialiased";

/** Fade and zoom in and out (tw-animate-css), keyed on Radix's open state. */
export const popInClasses =
  "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95";
