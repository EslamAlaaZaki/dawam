"use client";

import { useRouter } from "next/navigation";

import { useFixImpacts } from "../api/propagation";
import type { Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { ErrorText } from "./parts";

/**
 * The "Fix impacts" action (story 113a): recomputes the propagation Change Set from the
 * open validation errors propagation can fix, then links to it for review.
 */
export function FixImpacts({ workspace }: { workspace: Workspace }) {
  const fix = useFixImpacts(workspace.id);
  const router = useRouter();

  return (
    <div className="flex flex-col items-start gap-2">
      <Button
        type="button"
        disabled={fix.isPending}
        onClick={() =>
          fix.mutate(undefined, {
            onSuccess: (outcome) => {
              const changeSetId = outcome.change_set_id;
              if (!changeSetId) {
                toast.success("Nothing broken can be fixed by propagation.");
                return;
              }
              const count = outcome.impacts.length;
              toast.success(
                `Proposed a propagation Change Set for ${count} impacted ${count === 1 ? "Asset" : "Assets"}.`,
                {
                  action: {
                    label: "Review it",
                    onClick: () =>
                      router.push(
                        `/workspaces/${workspace.id}/change-sets/${changeSetId}`,
                      ),
                  },
                },
              );
            },
          })
        }
      >
        Fix impacts
      </Button>
      {fix.isError && <ErrorText>{fix.error.message}</ErrorText>}
    </div>
  );
}
