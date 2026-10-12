"use client";

import { useState } from "react";

import {
  useDismissUndeliveredLink,
  useUndeliveredLinks,
  type UndeliveredLink,
} from "../api/mail";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Loading } from "../shell/Loading";
import { ErrorText, PageHead, EmptyState } from "./parts";

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
      <ErrorText>Could not load the links: {links.error.message}</ErrorText>
    );
  }
  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Links to share"
        description="Links DAWAM could not email. Pass each one to its recipient only, through a channel you trust: whoever has a link can use it. A link leaves this list when it expires."
      />
      {links.data.length === 0 ? (
        <EmptyState>There are no links to share.</EmptyState>
      ) : (
        <ul className="flex max-w-3xl flex-col gap-3">
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
    <li
      className="flex flex-col gap-2.5 rounded-xl border border-sand-5 bg-sand-1 p-4"
      aria-label={`Link for ${link.recipient}`}
    >
      <p>
        <strong>{PURPOSES[link.purpose] ?? link.purpose}</strong> for{" "}
        {link.recipient}
        <br />
        <small className="text-sand-11">
          {REASONS[link.reason]} · created {when(link.created_at)} · expires{" "}
          {when(link.expires_at)}
        </small>
      </p>
      <Input
        readOnly
        value={link.url}
        aria-label="Link"
        onFocus={(e) => e.target.select()}
      />
      <div className="flex flex-wrap gap-2">
        <Button type="button" onClick={copy}>
          {copied ? "Copied" : "Copy link"}
        </Button>
        <Button
          type="button"
          onClick={() => dismiss.mutate(link.id)}
          disabled={dismiss.isPending}
        >
          Remove
        </Button>
      </div>
      {dismiss.isError && <ErrorText>{dismiss.error.message}</ErrorText>}
    </li>
  );
}
