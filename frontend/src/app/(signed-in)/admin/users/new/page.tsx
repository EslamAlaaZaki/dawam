import { CreateUserPage } from "../../../../../admin/CreateUserPage";
import { RequireAdmin } from "../../../../../admin/RequireAdmin";

export default function CreateUser() {
  return (
    <RequireAdmin>
      <CreateUserPage />
    </RequireAdmin>
  );
}
