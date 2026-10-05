"use client";

import { useLlmSetup } from "../api/llm";

/** Shown to admins on every page until an agent model is registered and its test passed. */
export function LlmSetupBanner() {
  const setup = useLlmSetup();
  if (!setup.data || setup.data.complete) {
    return null;
  }
  return (
    <p role="alert" className="login-error">
      AI setup is incomplete: register an agent model and test it under{" "}
      <a href="/admin/llm">Language models</a>.
    </p>
  );
}
