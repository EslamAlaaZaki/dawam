import Link from "next/link";

/** Shown for a Workspace the API answers 404 for: missing, or not the user's to see. */
export function WorkspaceNotFound() {
  return (
    <section className="page">
      <h2>Workspace not found</h2>
      <p>It does not exist, or you are not a member of it.</p>
      <Link href="/">Back to your Workspaces</Link>
    </section>
  );
}
