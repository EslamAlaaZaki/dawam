// The app's pages and layouts put together as Next.js routes them, for component tests
// (which render without Next.js). Keep it in step with the files under src/app/.
import { usePathname } from "next/navigation";

import SignedInLayout from "../app/(signed-in)/layout";
import HomePage from "../app/(signed-in)/page";
import LoginPage from "../app/login/page";
import NotFound from "../app/not-found";
import { Shell } from "../shell/Shell";

function Page({ pathname }: { pathname: string }) {
  switch (pathname) {
    case "/login":
      return <LoginPage />;
    case "/":
      return (
        <SignedInLayout>
          <HomePage />
        </SignedInLayout>
      );
    default:
      return <NotFound />;
  }
}

export function TestApp() {
  return (
    <Shell>
      <Page pathname={usePathname()} />
    </Shell>
  );
}
