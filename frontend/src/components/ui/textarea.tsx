import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";
import { fieldClasses } from "./input";

function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(fieldClasses, "min-h-16 py-2", className)}
      {...props}
    />
  );
}

export { Textarea };
