"use client";

import { useState } from "react";

import { useDismissUndeliveredLink, useUndeliveredLinks, type UndeliveredLink } from "../api/mail";
import { Loading } from "../shell/Loading";

const PURPOSES: Record<string, string> = {
  password_reset: "Password reset",
  invitation: "Invitation",
};

const REASONS: Record<UndeliveredLink["reason"], string> = {
  smtp_not_configured: "SMTP is off",
  smtp_failed: "Sending failed",
};

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function UndeliveredLinksPage() {
  const links = useUndeliveredLinks();
  if (links.isPending) {
    return <Loading />;
  }
  if (links.isError) {
    return (
      <p className="page" role="alert">
        Could not load the links: {links.error.message}
      </p>
    );
  }
  return (
    <section className="page admin-page">
      <h2>Links to share</h2>
      <p>
        Links DAWAM could not email. Pass each one to its recipient only, through a channel
        you trust: whoever has a link can use it. A link leaves this list when it expires.
      </p>
      {links.data.length === 0 ? (
        <p>There are no links to share.</p>
      ) : (
        <ul className="link-list">
          {links.data.map((link) => (
            <LinkItem key={link.id} link={link} />
          ))}
        </ul>
      )}
    </section>
  );
}

function LinkItem({ link }: { link: UndeliveredLink }) {
  const dismiss = useDismissUndeliveredLink();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(link.url);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <li className="link-item" aria-label={`Link for ${link.recipient}`}>
      <p>
        <strong>{PURPOSES[link.purpose] ?? link.purpose}</strong> for {link.recipient}
        <br />
        <small>
          {REASONS[link.reason]} · created {when(link.created_at)} · expires{" "}
          {when(link.expires_at)}
        </small>
      </p>
      <input readOnly value={link.url} aria-label="Link" onFocus={(e) => e.target.select()} />
      <div className="admin-actions">
        <button type="button" onClick={copy}>
          {copied ? "Copied" : "Copy link"}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => dismiss.mutate(link.id)}
          disabled={dismiss.isPending}
        >
          Remove
        </button>
      </div>
      {dismiss.isError && <p role="alert">{dismiss.error.message}</p>}
    </li>
  );
}
