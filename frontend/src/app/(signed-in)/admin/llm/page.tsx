import { LlmProvidersPage } from "../../../../admin/LlmProvidersPage";
import { RequireAdmin } from "../../../../admin/RequireAdmin";

export default function LlmSettings() {
  return (
    <RequireAdmin>
      <LlmProvidersPage />
    </RequireAdmin>
  );
}
