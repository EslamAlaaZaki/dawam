# The frontend is styled with Tailwind CSS and shadcn/ui

The frontend moves from one hand-written `globals.css` to Tailwind CSS v4 with shadcn/ui components built on Radix primitives. Colours come from the Radix Colors scales (warm-grey neutrals, a teal/jade accent, semantic success, warning, danger and info), exposed as semantic CSS variables with light and dark themes. Radix gives the dialogs, menus, popovers and tabs the keyboard behaviour and focus handling that §9 of the spec requires (keyboard-navigable core flows, WCAG AA contrast) without us writing it. shadcn/ui copies the component source into the repo instead of installing a styled library, so we restyle it freely and the app does not inherit a stock template look.

## Considered options

- **Plain CSS with design tokens**: no new dependencies, but every accessible primitive (dialog focus trap, menu keyboard navigation, popover dismissal) would be hand-written and tested by us.
- **A full component library (Mantine, MUI)**: accessible out of the box, but its components are themed rather than owned, so screens drift towards the library's recognisable default look, which the redesign is meant to avoid.
