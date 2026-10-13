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
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { SettingsCard } from "../components/entity/SettingsCard";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { Loading } from "../shell/Loading";
import { ErrorText, NativeSelect, PageHead, labelClasses } from "./parts";

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
  const [turningOff, setTurningOff] = useState(false);

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
    <form onSubmit={onSubmit} aria-label="SMTP settings">
      <SettingsCard
        title="SMTP server"
        description="DAWAM sends its email through this server."
        footer={
          <>
            <Button type="submit" variant="primary" disabled={save.isPending}>
              Save
            </Button>
            {saved && (
              <Button type="button" onClick={() => setTurningOff(true)}>
                Turn SMTP off
              </Button>
            )}
          </>
        }
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
      </SettingsCard>
      <ConfirmDialog
        open={turningOff}
        onOpenChange={(next) => {
          if (!next) clear.reset();
          setTurningOff(next);
        }}
        title="Turn SMTP off?"
        description="The saved server settings are removed. Links DAWAM would email are listed under Links to share instead."
        confirmLabel="Turn SMTP off"
        pending={clear.isPending}
        error={clear.error}
        onConfirm={() => clear.mutate(undefined, { onSuccess: () => setTurningOff(false) })}
      />
    </form>
  );
}

function TestEmail() {
  const send = useSendTestEmail();
  const [to, setTo] = useState("");

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    send.mutate(to.trim() || null, {
      onSuccess: (sent) => toast.success(`Test email sent to ${sent.to}.`),
    });
  }

  return (
    <form onSubmit={onSubmit} aria-label="Test email">
      <SettingsCard
        title="Send a test email"
        footer={
          <Button type="submit" disabled={send.isPending}>
            Send test email
          </Button>
        }
      >
      <label className={labelClasses}>
        Recipient (empty: your own address)
        <Input
          name="to"
          type="email"
          value={to}
          onChange={(e) => setTo(e.target.value)}
        />
      </label>
      {send.isError && <ErrorText>{send.error.message}</ErrorText>}
      </SettingsCard>
    </form>
  );
}
