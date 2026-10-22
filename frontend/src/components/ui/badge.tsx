import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/** A status, a kind or a data tag (strong text on a subtle tint), with an optional dot (`dot`). */
const badgeVariants = cva(
  "inline-flex h-5 items-center gap-1 rounded-md px-1.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3",
  {
    variants: {
      variant: {
        neutral: "bg-muted text-muted-foreground",
        success: "bg-success-subtle text-success-strong",
        warning: "bg-warning-subtle text-warning-strong",
        danger: "bg-danger-subtle text-danger-strong",
        info: "bg-info-subtle text-info-strong",
        accent: "bg-primary-subtle text-primary",
        pk: "bg-primary-subtle text-tag-pk",
        fk: "bg-info-subtle text-tag-fk",
        pii: "bg-danger-subtle text-tag-pii",
        dq: "bg-warning-subtle text-tag-dq",
        mapped: "bg-success-subtle text-tag-mapped",
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
