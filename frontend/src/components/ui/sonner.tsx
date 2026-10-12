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
      icons={{ success: <CircleCheck className="size-4 text-accent-7" aria-hidden /> }}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            "flex w-full items-center gap-2.5 rounded-lg bg-n12 px-3.5 py-2.5 text-n1 shadow-overlay",
          title: "font-medium",
          description: "text-n1/80",
        },
      }}
      {...props}
    />
  );
}

export { toast, Toaster };
