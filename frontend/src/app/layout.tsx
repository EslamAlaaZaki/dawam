import type { Metadata } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { Shell } from "../shell/Shell";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "DAWAM",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Render every page per request: the Content-Security-Policy nonce Next.js puts on
  // its scripts is new for each one (see src/proxy.ts).
  await connection();
  return (
    <html lang="en">
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
