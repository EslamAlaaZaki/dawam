import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/** The text-field look, shared by Input, Textarea and Select's trigger. */
const fieldClasses =
  "w-full rounded-md border border-control-edge bg-background px-2.5 text-foreground placeholder:text-subtle-foreground focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:border-danger";

function Input({ className, type, ...props }: ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(fieldClasses, "h-9 min-w-0", className)}
      {...props}
    />
  );
}

export { fieldClasses, Input };
