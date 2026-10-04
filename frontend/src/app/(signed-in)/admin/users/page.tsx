import { RequireAdmin } from "../../../../admin/RequireAdmin";
import { UsersPage } from "../../../../admin/UsersPage";

export default function Users() {
  return (
    <RequireAdmin>
      <UsersPage />
    </RequireAdmin>
  );
}
