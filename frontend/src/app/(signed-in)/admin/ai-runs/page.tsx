import { RequireAdmin } from "../../../../admin/RequireAdmin";
import { RunExplorerPage } from "../../../../admin/RunExplorerPage";

export default function AiRuns() {
  return (
    <RequireAdmin>
      <RunExplorerPage />
    </RequireAdmin>
  );
}
