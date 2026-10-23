import Link from "next/link";

import { PageHeader } from "../components/PageHeader";
import { linkClasses } from "./parts";

/** Shown for a Workspace the API answers 404 for: missing, or not the user's to see. */
export function WorkspaceNotFound() {
  return (
    <section className="flex flex-col gap-3">
      <PageHeader
        title="Workspace not found"
        description="It does not exist, or you are not a member of it."
      />
      <Link href="/" className={linkClasses}>
        Back to your Workspaces
      </Link>
    </section>
  );
}
