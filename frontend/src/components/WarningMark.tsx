"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

/** A small "!" next to a value, whose tooltip (and accessible name) says what is wrong. */
export function WarningMark({ message }: { message: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          role="img"
          aria-label={message}
          className="ml-1 inline-flex size-4 shrink-0 items-center justify-center rounded-full bg-warning-subtle align-middle text-xs font-semibold text-warning-strong"
        >
          !
        </span>
      </TooltipTrigger>
      <TooltipContent>{message}</TooltipContent>
    </Tooltip>
  );
}
