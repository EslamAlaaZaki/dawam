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
import { Loading } from "../shell/Loading";

const SECURITY_OPTIONS: { value: SmtpSettingsInput["security"]; label: string }[] = [
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
      <p className="page" role="alert">
        Could not load the SMTP settings: {settings.error.message}
      </p>
    );
  }
  return (
    <section className="page admin-page">
      <h2>Email settings</h2>
      <p>
        {settings.data
          ? "DAWAM sends email through this SMTP server."
          : "SMTP is off: links DAWAM would email (such as password resets) are listed under " +
            "Links to share, for you to pass on."}
      </p>
      {/* Remount the form when the saved settings change, so it shows them. */}
      <SmtpForm key={settings.data?.updated_at ?? "none"} saved={settings.data} />
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
    save.mutate(body);
  }

  return (
    <form className="admin-form" onSubmit={onSubmit} aria-label="SMTP settings">
      <label>
        SMTP host
        <input name="host" defaultValue={saved?.host ?? ""} required />
      </label>
      <label>
        Port
        <input
          name="port"
          type="number"
          min={1}
          max={65535}
          defaultValue={saved?.port ?? 587}
          required
        />
      </label>
      <label>
        Encryption
        <select name="security" defaultValue={saved?.security ?? "starttls"}>
          {SECURITY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Username (empty if the server needs no sign-in)
        <input name="username" autoComplete="off" defaultValue={saved?.username ?? ""} />
      </label>
      <label>
        Password
        <input
          name="password"
          type="password"
          autoComplete="new-password"
          placeholder={saved?.has_password ? "Saved: leave empty to keep it" : ""}
        />
      </label>
      <label>
        From address
        <input name="sender" type="email" defaultValue={saved?.sender ?? ""} required />
      </label>
      {save.isError && (
        <p className="login-error" role="alert">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && <p role="status">Saved.</p>}
      <div className="admin-actions">
        <button type="submit" disabled={save.isPending}>
          Save
        </button>
        {saved && (
          <button
            type="button"
            className="secondary"
            onClick={() => clear.mutate()}
            disabled={clear.isPending}
          >
            Turn SMTP off
          </button>
        )}
      </div>
      {clear.isError && (
        <p className="login-error" role="alert">
          {clear.error.message}
        </p>
      )}
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
    <form className="admin-form" onSubmit={onSubmit} aria-label="Test email">
      <h3>Send a test email</h3>
      <label>
        Recipient (empty: your own address)
        <input name="to" type="email" value={to} onChange={(e) => setTo(e.target.value)} />
      </label>
      <button type="submit" disabled={send.isPending}>
        Send test email
      </button>
      {send.isSuccess && <p role="status">Test email sent to {send.data.to}.</p>}
      {send.isError && (
        <p className="login-error" role="alert">
          {send.error.message}
        </p>
      )}
    </form>
  );
}
