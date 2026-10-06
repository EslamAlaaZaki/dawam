// The app's pages and layouts put together as Next.js routes them, for component tests
// (which render without Next.js). Keep it in step with the files under src/app/.
import { usePathname } from "next/navigation";

import EmailSettingsPage from "../app/(signed-in)/admin/email/page";
import LinksToSharePage from "../app/(signed-in)/admin/email/links/page";
import LlmSettingsPage from "../app/(signed-in)/admin/llm/page";
import SignedInLayout from "../app/(signed-in)/layout";
import InvitationsPage from "../app/(signed-in)/admin/invitations/page";
import AdminWorkspacesPage from "../app/(signed-in)/admin/workspaces/page";
import AdminSettingsPage from "../app/(signed-in)/admin/settings/page";
import SecurityEventsPage from "../app/(signed-in)/admin/security-events/page";
import CreateUserPage from "../app/(signed-in)/admin/users/new/page";
import UsersPage from "../app/(signed-in)/admin/users/page";
import ChangePasswordPage from "../app/(signed-in)/change-password/page";
import HomePage from "../app/(signed-in)/page";
import ProfilePage from "../app/(signed-in)/profile/page";
import NewWorkspace from "../app/(signed-in)/workspaces/new/page";
import AcceptInvitationPage from "../app/accept-invitation/page";
import ForgotPasswordPage from "../app/forgot-password/page";
import LoginPage from "../app/login/page";
import ResetPasswordPage from "../app/reset-password/page";
import NotFound from "../app/not-found";
import SignUpPage from "../app/signup/page";
import { Shell } from "../shell/Shell";
import { WorkspacePage } from "../workspaces/WorkspacePage";

const WORKSPACE_PATH = /^\/workspaces\/([^/]+)$/;

function Page({ pathname }: { pathname: string }) {
  switch (pathname) {
    case "/login":
      return <LoginPage />;
    case "/signup":
      return <SignUpPage />;
    case "/profile":
      return (
        <SignedInLayout>
          <ProfilePage />
        </SignedInLayout>
      );
    case "/admin/settings":
      return (
        <SignedInLayout>
          <AdminSettingsPage />
        </SignedInLayout>
      );
    case "/admin/users":
      return (
        <SignedInLayout>
          <UsersPage />
        </SignedInLayout>
      );
    case "/admin/users/new":
      return (
        <SignedInLayout>
          <CreateUserPage />
        </SignedInLayout>
      );
    case "/admin/workspaces":
      return (
        <SignedInLayout>
          <AdminWorkspacesPage />
        </SignedInLayout>
      );
    case "/admin/invitations":
      return (
        <SignedInLayout>
          <InvitationsPage />
        </SignedInLayout>
      );
    case "/admin/security-events":
      return (
        <SignedInLayout>
          <SecurityEventsPage />
        </SignedInLayout>
      );
    case "/change-password":
      return (
        <SignedInLayout>
          <ChangePasswordPage />
        </SignedInLayout>
      );
    case "/forgot-password":
      return <ForgotPasswordPage />;
    case "/reset-password":
      return <ResetPasswordPage />;
    case "/accept-invitation":
      return <AcceptInvitationPage />;
    case "/admin/email":
      return (
        <SignedInLayout>
          <EmailSettingsPage />
        </SignedInLayout>
      );
    case "/admin/llm":
      return (
        <SignedInLayout>
          <LlmSettingsPage />
        </SignedInLayout>
      );
    case "/admin/email/links":
      return (
        <SignedInLayout>
          <LinksToSharePage />
        </SignedInLayout>
      );
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
