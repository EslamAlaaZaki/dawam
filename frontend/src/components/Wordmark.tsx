import { cn } from "../lib/utils";

/** DAWAM's mark (three layered rhombi, the top one in the accent) and its name. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 text-sm font-bold tracking-[0.14em] text-sand-12",
        className,
      )}
    >
      <svg viewBox="0 0 24 24" fill="none" aria-hidden className="size-[22px]">
        <path d="M12 3 21 7.5 12 12 3 7.5Z" className="fill-accent-solid" />
        <path
          d="M3 12 12 16.5 21 12"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
          opacity=".55"
        />
        <path
          d="M3 16.5 12 21 21 16.5"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
          opacity=".3"
        />
      </svg>
      <span>DAWAM</span>
    </div>
  );
}
