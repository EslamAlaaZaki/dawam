// A stand-in for `next/link` in component tests: an anchor whose click moves the
// in-memory location of ./navigation.ts, as a client-side navigation would.
import type { AnchorHTMLAttributes } from "react";

import { setLocation } from "./navigation";

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { href: string };

export default function Link({ href, onClick, ...props }: LinkProps) {
  return (
    <a
      {...props}
      href={href}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) {
          event.preventDefault();
          setLocation(href);
        }
      }}
    />
  );
}
