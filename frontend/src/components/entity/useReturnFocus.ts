import { useLayoutEffect, useRef } from "react";

/**
 * Radix returns focus to a Dialog's trigger, but our popups open from state, with no
 * trigger. Remember what had focus as the popup opened and hand it back on close; pass
 * the result to the content's `onCloseAutoFocus`.
 */
export function useReturnFocus(open: boolean) {
  const opener = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (open && document.activeElement instanceof HTMLElement) {
      opener.current = document.activeElement;
    }
  }, [open]);
  return (event: Event) => {
    if (opener.current?.isConnected) {
      event.preventDefault();
      opener.current.focus();
    }
  };
}
