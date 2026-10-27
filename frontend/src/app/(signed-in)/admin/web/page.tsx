import { RequireAdmin } from "../../../../admin/RequireAdmin";
import { WebAccessPage } from "../../../../admin/WebAccessPage";

export default function WebAccess() {
  return (
    <RequireAdmin>
      <WebAccessPage />
    </RequireAdmin>
  );
}
