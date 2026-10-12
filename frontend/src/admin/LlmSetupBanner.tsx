"use client";

import { TriangleAlert } from "lucide-react";

import { useLlmSetup } from "../api/llm";

/** Shown to admins on every page until an agent model is registered and its test passed. */
export function LlmSetupBanner() {
  const setup = useLlmSetup();
  if (!setup.data || setup.data.complete) {
    return null;
  }
  return (
    <div
      role="alert"
      className="dawam-ui mx-6 mt-5 flex items-start gap-2.5 rounded-lg border border-amber-11/40 bg-amber-3 px-3 py-2.5 text-sand-12"
    >
      <TriangleAlert
        aria-hidden
        className="mt-0.5 size-4 shrink-0 text-pill-warning-fg"
      />
      <p>
        AI setup is incomplete: register an agent model and test it under{" "}
        <a href="/admin/llm" className="font-medium underline">
          Language models
        </a>
        .
      </p>
    </div>
  );
}
