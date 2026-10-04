import { WorkspacePage } from "../../../../workspaces/WorkspacePage";

export default async function WorkspaceRoute({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  return <WorkspacePage workspaceId={workspaceId} />;
}
