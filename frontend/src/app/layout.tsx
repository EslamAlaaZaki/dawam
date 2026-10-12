import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import type { Metadata } from "next";
import { headers } from "next/headers";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { nonceFromPolicy } from "../security/headers";
import { Shell } from "../shell/Shell";
import "./theme.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "DAWAM",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Render every page per request: the Content-Security-Policy nonce Next.js puts on
  // its scripts is new for each one (see src/proxy.ts).
  await connection();
  const nonce = nonceFromPolicy((await headers()).get("Content-Security-Policy"));
  // The fonts are self-hosted (next/font): no request leaves the site. The theme
  // script sets <html>'s class before React hydrates, hence suppressHydrationWarning.
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
      suppressHydrationWarning
    >
      <body>
        <Providers nonce={nonce}>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
