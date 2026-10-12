"use client";

import { CircleCheck } from "lucide-react";
import { useTheme } from "next-themes";
import { toast, Toaster as Sonner, type ToasterProps } from "sonner";

/**
 * Where Toasts appear: short confirmations of the user's own action. Errors are never
 * toasts; they stay inline. Styled as the prototype's toast: inverted, bottom right.
 */
function Toaster(props: ToasterProps) {
  const { resolvedTheme } = useTheme();
  return (
    <Sonner
      theme={resolvedTheme === "dark" ? "dark" : "light"}
      position="bottom-right"
      icons={{ success: <CircleCheck className="size-4 text-toast-icon" aria-hidden /> }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            "flex w-full items-center gap-2.5 rounded-lg bg-sand-12 px-3.5 py-2.5 text-sand-1 shadow-overlay",
          title: "font-medium",
          description: "text-sand-1/80",
          actionButton:
            "ml-auto h-[26px] shrink-0 rounded-md border border-sand-1/40 px-2 text-mono font-medium text-sand-1 hover:bg-sand-1/10",
        },
      }}
      {...props}
    />
  );
}

export { toast, Toaster };
