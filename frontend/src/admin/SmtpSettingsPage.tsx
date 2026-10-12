"use client";

import { useState, type FormEvent } from "react";

import {
  useClearSmtpSettings,
  useSaveSmtpSettings,
  useSendTestEmail,
  useSmtpSettings,
  type SmtpSettings,
  type SmtpSettingsInput,
} from "../api/mail";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { Loading } from "../shell/Loading";
import { ErrorText, NativeSelect, PageHead, labelClasses } from "./parts";

const cardClasses =
  "flex max-w-xl flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5";

const SECURITY_OPTIONS: {
  value: SmtpSettingsInput["security"];
  label: string;
}[] = [
  { value: "starttls", label: "STARTTLS (usually port 587)" },
  { value: "tls", label: "TLS from the start (usually port 465)" },
  { value: "none", label: "None: plain SMTP (usually port 25)" },
];

export function SmtpSettingsPage() {
  const settings = useSmtpSettings();
  if (settings.isPending) {
    return <Loading />;
  }
  if (settings.isError) {
    return (
      <ErrorText>
        Could not load the SMTP settings: {settings.error.message}
      </ErrorText>
    );
  }
  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Email settings"
        description={
          settings.data
            ? "DAWAM sends email through this SMTP server."
            : "SMTP is off: links DAWAM would email (such as password resets) are listed under " +
              "Links to share, for you to pass on."
        }
      />
      {/* Remount the form when the saved settings change, so it shows them. */}
      <SmtpForm
        key={settings.data?.updated_at ?? "none"}
        saved={settings.data}
      />
      {settings.data && <TestEmail />}
    </section>
  );
}

function SmtpForm({ saved }: { saved: SmtpSettings | null }) {
  const save = useSaveSmtpSettings();
  const clear = useClearSmtpSettings();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const username = String(form.get("username") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const body: SmtpSettingsInput = {
      host: String(form.get("host") ?? "").trim(),
      port: Number(form.get("port")),
      security: String(form.get("security")) as SmtpSettingsInput["security"],
      sender: String(form.get("sender") ?? "").trim(),
      username: username || null,
    };
    // Left empty: keep the saved password (the API then leaves it as it is).
    if (password) {
      body.password = password;
    }
    save.mutate(body, {
      onSuccess: () => toast.success("SMTP settings saved."),
    });
  }

  return (
    <form
      className={cardClasses}
      onSubmit={onSubmit}
      aria-label="SMTP settings"
    >
      <label className={labelClasses}>
        SMTP host
        <Input name="host" defaultValue={saved?.host ?? ""} required />
      </label>
      <label className={labelClasses}>
        Port
        <Input
          name="port"
          type="number"
          min={1}
          max={65535}
          defaultValue={saved?.port ?? 587}
          required
        />
      </label>
      <label className={labelClasses}>
        Encryption
        <NativeSelect
          name="security"
          defaultValue={saved?.security ?? "starttls"}
        >
          {SECURITY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Username (empty if the server needs no sign-in)
        <Input
          name="username"
          autoComplete="off"
          defaultValue={saved?.username ?? ""}
        />
      </label>
      <label className={labelClasses}>
        Password
        <Input
          name="password"
          type="password"
          autoComplete="new-password"
          placeholder={
            saved?.has_password ? "Saved: leave empty to keep it" : ""
          }
        />
      </label>
      <label className={labelClasses}>
        From address
        <Input
          name="sender"
          type="email"
          defaultValue={saved?.sender ?? ""}
          required
        />
      </label>
      {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          Save
        </Button>
        {saved && (
          <Button
            type="button"
            onClick={() => clear.mutate()}
            disabled={clear.isPending}
          >
            Turn SMTP off
          </Button>
        )}
      </div>
      {clear.isError && <ErrorText>{clear.error.message}</ErrorText>}
    </form>
  );
}

function TestEmail() {
  const send = useSendTestEmail();
  const [to, setTo] = useState("");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    send.mutate(to.trim() || null);
  }

  return (
    <form className={cardClasses} onSubmit={onSubmit} aria-label="Test email">
      <h3 className="text-lg font-semibold">Send a test email</h3>
      <label className={labelClasses}>
        Recipient (empty: your own address)
        <Input
          name="to"
          type="email"
          value={to}
          onChange={(e) => setTo(e.target.value)}
        />
      </label>
      <Button type="submit" className="self-start" disabled={send.isPending}>
        Send test email
      </Button>
      {send.isSuccess && (
        <p className="text-sm text-sand-11" role="status">
          Test email sent to {send.data.to}.
        </p>
      )}
      {send.isError && <ErrorText>{send.error.message}</ErrorText>}
    </form>
  );
}
