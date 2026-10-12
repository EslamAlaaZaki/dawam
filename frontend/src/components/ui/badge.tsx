import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/** The prototype's pills: a status or a kind, with an optional dot (`dot`). */
const badgeVariants = cva(
  "inline-flex h-5 items-center gap-[5px] rounded-pill px-[7px] text-xs font-medium whitespace-nowrap [&_svg]:size-3",
  {
    variants: {
      variant: {
        neutral: "bg-sand-3 text-sand-11",
        success: "bg-grass-3 text-grass-11",
        warning: "bg-amber-3 text-pill-warning-fg",
        danger: "bg-red-3 text-red-11",
        accent: "bg-jade-3 text-pill-accent-fg",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

function Badge({
  className,
  variant,
  asChild = false,
  dot = false,
  children,
  ...props
}: ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean; dot?: boolean }) {
  const Comp = asChild ? Slot.Root : "span";
  return (
    <Comp data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props}>
      {dot && <i aria-hidden className="size-1.5 rounded-full bg-current" />}
      {children}
    </Comp>
  );
}

export { Badge, badgeVariants };
