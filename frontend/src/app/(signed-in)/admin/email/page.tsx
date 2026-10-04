import { RequireAdmin } from "../../../../admin/RequireAdmin";
import { SmtpSettingsPage } from "../../../../admin/SmtpSettingsPage";

export default function EmailSettings() {
  return (
    <RequireAdmin>
      <SmtpSettingsPage />
    </RequireAdmin>
  );
}
