// The app's pages and layouts put together as Next.js routes them, for component tests
// (which render without Next.js). Keep it in step with the files under src/app/.
import { usePathname } from "next/navigation";

import SignedInLayout from "../app/(signed-in)/layout";
import HomePage from "../app/(signed-in)/page";
import NewWorkspace from "../app/(signed-in)/workspaces/new/page";
import LoginPage from "../app/login/page";
import NotFound from "../app/not-found";
import { Shell } from "../shell/Shell";
import { WorkspacePage } from "../workspaces/WorkspacePage";

const WORKSPACE_PATH = /^\/workspaces\/([^/]+)$/;

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
    case "/workspaces/new":
      return (
        <SignedInLayout>
          <NewWorkspace />
        </SignedInLayout>
      );
  }
  const workspaceId = WORKSPACE_PATH.exec(pathname)?.[1];
  if (workspaceId !== undefined) {
    // app/(signed-in)/workspaces/[workspaceId]/page.tsx is an async server component;
    // render the screen it renders, with the parameter Next.js would give it.
    return (
      <SignedInLayout>
        <WorkspacePage workspaceId={decodeURIComponent(workspaceId)} />
      </SignedInLayout>
    );
  }
  return <NotFound />;
}

export function TestApp() {
  return (
    <Shell>
      <Page pathname={usePathname()} />
    </Shell>
  );
}
