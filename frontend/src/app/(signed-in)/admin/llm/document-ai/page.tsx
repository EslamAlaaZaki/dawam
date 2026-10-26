import { DocumentAiSettingsPage } from "../../../../../admin/DocumentAiSettingsPage";
import { RequireAdmin } from "../../../../../admin/RequireAdmin";

export default function DocumentAi() {
  return (
    <RequireAdmin>
      <DocumentAiSettingsPage />
    </RequireAdmin>
  );
}
