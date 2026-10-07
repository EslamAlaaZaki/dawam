// The AI assistant's chat (spec stories 141, 144, 151, 152): conversations, answers
// streamed over Server-Sent Events, and stopping a running answer.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Conversation = components["schemas"]["ConversationOut"];
export type ConversationDetail = components["schemas"]["ConversationDetailOut"];
export type ChatMessage = components["schemas"]["MessageOut"];
export type Run = components["schemas"]["RunOut"];
export type ToolCall = components["schemas"]["ToolCallOut"];
export type PageContext = components["schemas"]["PageContextIn"];

const conversationsKey = (workspaceId: string) =>
  ["workspace", workspaceId, "assistant", "conversations"] as const;
export const conversationKey = (workspaceId: string, conversationId: string) =>
  [...conversationsKey(workspaceId), conversationId] as const;

const ROOT = "/api/v1/workspaces/{workspace_id}/assistant/conversations";

/** The member's own conversations and those shared with the Workspace, newest first. */
export function useConversations(workspaceId: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: conversationsKey(workspaceId),
    queryFn: async () => {
      const { data, error, response } = await client.GET(ROOT, {
        params: { path: { workspace_id: workspaceId } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

export function useConversation(workspaceId: string, conversationId: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: conversationKey(workspaceId, conversationId ?? ""),
    enabled: conversationId !== null,
    queryFn: async () => {
      const { data, error, response } = await client.GET(`${ROOT}/{conversation_id}`, {
        params: { path: { workspace_id: workspaceId, conversation_id: conversationId ?? "" } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useCreateConversation(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await client.POST(ROOT, {
        params: { path: { workspace_id: workspaceId } },
        body: { title: "" },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: conversationsKey(workspaceId) }),
  });
}

/** Share a conversation with the Workspace's members, or stop sharing it. */
export function useShareConversation(workspaceId: string, conversationId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (shared: boolean) => {
      const { data, error, response } = await client.PATCH(`${ROOT}/{conversation_id}`, {
        params: { path: { workspace_id: workspaceId, conversation_id: conversationId } },
        body: { shared_with_workspace: shared },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: conversationsKey(workspaceId) }),
  });
}

export function useStopResponse(workspaceId: string) {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (conversationId: string) => {
      const { error, response } = await client.POST(`${ROOT}/{conversation_id}/stop`, {
        params: { path: { workspace_id: workspaceId, conversation_id: conversationId } },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
  });
}

/** What the server sends while it answers. */
export type AnswerEvent =
  | { type: "started"; runId: string }
  | { type: "text"; text: string }
  | { type: "tool"; call: ToolCall }
  | { type: "done"; run: Run };

interface Frame {
  event: string;
  data: string;
}

/** Splits a Server-Sent Events text into complete frames, and the unfinished rest. */
export function splitFrames(buffer: string): { frames: Frame[]; rest: string } {
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  const frames: Frame[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) {
        event = line.slice("event: ".length);
      } else if (line.startsWith("data: ")) {
        data.push(line.slice("data: ".length));
      }
    }
    frames.push({ event, data: data.join("\n") });
  }
  return { frames, rest };
}

function toAnswerEvent({ event, data }: Frame): AnswerEvent | null {
  const body = JSON.parse(data);
  switch (event) {
    case "started":
      return { type: "started", runId: body.run_id };
    case "text":
      return { type: "text", text: body.text };
    case "tool":
      return { type: "tool", call: body };
    case "done":
      return { type: "done", run: body.run };
    default:
      return null;
  }
}

/**
 * Ask the assistant and report each event as it arrives. Refusals before the answer
 * starts (archived Workspace, a run already going) are thrown as `ApiError`; a model that
 * cannot answer ends with a `done` event whose run says why.
 */
export function useSendMessage(workspaceId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      conversationId: string;
      content: string;
      context?: PageContext;
      onEvent: (event: AnswerEvent) => void;
    }) => {
      const { error, response } = await client.POST(`${ROOT}/{conversation_id}/messages`, {
        params: { path: { workspace_id: workspaceId, conversation_id: input.conversationId } },
        body: { content: input.content, context: input.context ?? null },
        parseAs: "stream",
      });
      if (error || !response.body) {
        const body = error as { error: components["schemas"]["ErrorBody"] } | undefined;
        throw new ApiError(
          response.status,
          body?.error ?? { code: "no_stream", message: "The assistant did not answer.", details: {} },
        );
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = splitFrames(buffer);
        buffer = rest;
        for (const frame of frames) {
          const event = toAnswerEvent(frame);
          if (event) {
            input.onEvent(event);
          }
        }
      }
    },
    onSettled: (_data, _error, input) =>
      queryClient.invalidateQueries({
        queryKey: conversationKey(workspaceId, input.conversationId),
      }),
  });
}
