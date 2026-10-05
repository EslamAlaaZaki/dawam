import { RequireAdmin } from "../../../../admin/RequireAdmin";
import { WorkspacesPage } from "../../../../admin/WorkspacesPage";

export default function AdminWorkspaces() {
  return (
    <RequireAdmin>
      <WorkspacesPage />
    </RequireAdmin>
  );
}
