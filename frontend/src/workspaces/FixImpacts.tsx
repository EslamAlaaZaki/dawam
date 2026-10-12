"use client";

import Link from "next/link";

import { useFixImpacts } from "../api/propagation";
import type { Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { ErrorText, OkText, linkClasses } from "./parts";

/**
 * The "Fix impacts" action (story 113a): recomputes the propagation Change Set from the
 * open validation errors propagation can fix, then links to it for review.
 */
export function FixImpacts({ workspace }: { workspace: Workspace }) {
  const fix = useFixImpacts(workspace.id);
  const changeSetId = fix.data?.change_set_id;

  return (
    <div className="flex flex-col items-start gap-2">
      <Button
        type="button"
        disabled={fix.isPending}
        onClick={() => fix.mutate()}
      >
        Fix impacts
      </Button>
      {fix.isSuccess &&
        (changeSetId ? (
          <OkText role="status">
            Proposed a propagation Change Set for {fix.data.impacts.length}{" "}
            impacted {fix.data.impacts.length === 1 ? "Asset" : "Assets"}.{" "}
            <Link
              href={`/workspaces/${workspace.id}/change-sets/${changeSetId}`}
              className={linkClasses}
            >
              Review it
            </Link>
          </OkText>
        ) : (
          <OkText role="status">
            Nothing broken can be fixed by propagation.
          </OkText>
        ))}
      {fix.isError && <ErrorText>{fix.error.message}</ErrorText>}
    </div>
  );
}
