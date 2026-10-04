import { RequireAdmin } from "../../../../admin/RequireAdmin";
import { SecurityEventsPage } from "../../../../admin/SecurityEventsPage";

export default function SecurityEvents() {
  return (
    <RequireAdmin>
      <SecurityEventsPage />
    </RequireAdmin>
  );
}
