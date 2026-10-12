import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

/** The text-field look, shared by Input, Textarea and Select's trigger. */
const fieldClasses =
  "w-full rounded-md border border-control-edge bg-sand-1 px-2.5 text-sand-12 placeholder:text-sand-10 focus-visible:border-jade-11 focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-jade-7 disabled:cursor-not-allowed disabled:opacity-60 aria-invalid:border-red-11";

function Input({ className, type, ...props }: ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(fieldClasses, "h-[34px] min-w-0", className)}
      {...props}
    />
  );
}

export { fieldClasses, Input };
