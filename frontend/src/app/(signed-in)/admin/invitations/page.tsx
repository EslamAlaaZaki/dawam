import { InvitationsPage } from "../../../../admin/InvitationsPage";
import { RequireAdmin } from "../../../../admin/RequireAdmin";

export default function Invitations() {
  return (
    <RequireAdmin>
      <InvitationsPage />
    </RequireAdmin>
  );
}
