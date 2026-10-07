import { LlmSettingsPage } from "../../../../../admin/LlmSettingsPage";
import { RequireAdmin } from "../../../../../admin/RequireAdmin";

export default function LlmRolesBudgetsUsage() {
  return (
    <RequireAdmin>
      <LlmSettingsPage />
    </RequireAdmin>
  );
}
