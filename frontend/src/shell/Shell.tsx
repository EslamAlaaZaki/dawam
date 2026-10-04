import type { ReactNode } from "react";

import { ApiStatus } from "./ApiStatus";
import { SignedInUser } from "./SignedInUser";

/** The frame of every page: header (with the signed-in user), content, API status. */
export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="shell">
      <header className="shell-header">
        <h1>DAWAM</h1>
        <p>Data Analysis &amp; Warehouse Architecture Modeler</p>
        <SignedInUser />
      </header>
      <main className="shell-main">{children}</main>
      <footer className="shell-footer">
        <ApiStatus />
      </footer>
    </div>
  );
}
