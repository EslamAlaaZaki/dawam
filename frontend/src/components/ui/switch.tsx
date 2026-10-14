import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

type SwitchProps = Omit<
  ComponentProps<"button">,
  "onChange" | "role" | "type"
> & {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
};

/** A toggle switch: a button with `role="switch"`, labelled by a wrapping <label>. */
function Switch({
  checked,
  onCheckedChange,
  className,
  ...props
}: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      data-slot="switch"
      data-state={checked ? "checked" : "unchecked"}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "peer relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-control-edge bg-sand-5 transition-colors disabled:cursor-not-allowed disabled:opacity-60 data-[state=checked]:border-accent-solid data-[state=checked]:bg-accent-solid",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden
        className={cn(
          "pointer-events-none block size-3.5 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-[18px]" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

export { Switch };
