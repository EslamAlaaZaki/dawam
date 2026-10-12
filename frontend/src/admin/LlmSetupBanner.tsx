"use client";

import { useLlmSetup } from "../api/llm";

/** Shown to admins on every page until an agent model is registered and its test passed. */
export function LlmSetupBanner() {
  const setup = useLlmSetup();
  if (!setup.data || setup.data.complete) {
    return null;
  }
  return (
    <p
      role="alert"
      className="dawam-ui mx-6 mt-5 rounded-md bg-amber-3 px-3 py-2 text-sm text-pill-warning-fg"
    >
      AI setup is incomplete: register an agent model and test it under{" "}
      <a href="/admin/llm" className="font-medium underline">
        Language models
      </a>
      .
    </p>
  );
}
