"use client";

import Link from "next/link";

import { useFixImpacts } from "../api/propagation";
import type { Workspace } from "../api/workspaces";

/**
 * The "Fix impacts" action (story 113a): recomputes the propagation Change Set from the
 * open validation errors propagation can fix, then links to it for review.
 */
export function FixImpacts({ workspace }: { workspace: Workspace }) {
  const fix = useFixImpacts(workspace.id);
  const changeSetId = fix.data?.change_set_id;

  return (
    <div className="fix-impacts">
      <button type="button" disabled={fix.isPending} onClick={() => fix.mutate()}>
        Fix impacts
      </button>
      {fix.isSuccess &&
        (changeSetId ? (
          <p role="status">
            Proposed a propagation Change Set for {fix.data.impacts.length} impacted{" "}
            {fix.data.impacts.length === 1 ? "Asset" : "Assets"}.{" "}
            <Link href={`/workspaces/${workspace.id}/change-sets/${changeSetId}`}>Review it</Link>
          </p>
        ) : (
          <p role="status">Nothing broken can be fixed by propagation.</p>
        ))}
      {fix.isError && (
        <p className="form-error" role="alert">
          {fix.error.message}
        </p>
      )}
    </div>
  );
}
