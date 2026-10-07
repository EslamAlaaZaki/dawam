import { ChangeSetPage } from "../../../../../../workspaces/ChangeSetReview";

export default async function ChangeSetRoute({
  params,
}: {
  params: Promise<{ workspaceId: string; changeSetId: string }>;
}) {
  const { workspaceId, changeSetId } = await params;
  return <ChangeSetPage workspaceId={workspaceId} changeSetId={changeSetId} />;
}
