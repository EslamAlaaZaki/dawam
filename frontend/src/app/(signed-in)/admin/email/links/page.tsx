import { RequireAdmin } from "../../../../../admin/RequireAdmin";
import { UndeliveredLinksPage } from "../../../../../admin/UndeliveredLinksPage";

export default function LinksToShare() {
  return (
    <RequireAdmin>
      <UndeliveredLinksPage />
    </RequireAdmin>
  );
}
