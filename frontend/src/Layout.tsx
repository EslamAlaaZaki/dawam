import { Outlet } from "react-router";

import { useApiVersion, useMe, useSignOut } from "./api/queries";

function ApiStatus() {
  const version = useApiVersion();
  if (version.isPending) {
    return <span>Connecting to the API…</span>;
  }
  if (version.isError) {
    return <span role="alert">API unavailable: {version.error.message}</span>;
  }
  return <span>API version {version.data.version}</span>;
}

function SignedInUser() {
  const me = useMe();
  const signOut = useSignOut();
  if (!me.data) {
    return null;
  }
  return (
    <div className="shell-user">
      <span className="shell-user-name">{me.data.display_name}</span>
      <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
        Sign out
      </button>
      {signOut.isError && <span role="alert">Could not sign out: {signOut.error.message}</span>}
    </div>
  );
}

/** The frame of every page: header (with the signed-in user), content, API status. */
export function Layout() {
  return (
    <div className="shell">
      <header className="shell-header">
        <h1>DAWAM</h1>
        <p>Data Analysis &amp; Warehouse Architecture Modeler</p>
        <SignedInUser />
      </header>
      <main className="shell-main">
        <Outlet />
      </main>
      <footer className="shell-footer">
        <ApiStatus />
      </footer>
    </div>
  );
}
