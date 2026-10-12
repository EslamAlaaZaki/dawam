import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-60 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "border-accent-solid bg-accent-solid text-on-accent hover:border-accent-solid-hover hover:bg-accent-solid-hover",
        secondary: "border-sand-6 bg-sand-1 text-sand-12 hover:bg-sand-3",
        ghost: "border-transparent bg-transparent text-sand-11 hover:bg-sand-3 hover:text-sand-12",
      },
      size: {
        default: "h-8 px-3",
        sm: "h-[26px] px-2 text-mono",
        lg: "h-9 px-4",
        icon: "size-8",
      },
    },
    defaultVariants: { variant: "secondary", size: "default" },
  },
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "button";
  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
