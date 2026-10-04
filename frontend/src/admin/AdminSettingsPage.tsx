"use client";

import { type FormEvent } from "react";

import { useAdminSettings, useUpdateAdminSettings, type AdminSettings } from "../api/admin";
import { useMe } from "../api/queries";
import { Loading } from "../shell/Loading";

/** Installation-wide settings; only admins can read or change them (the API enforces it). */
export function AdminSettingsPage() {
  const me = useMe();
  const isAdmin = me.data?.system_role === "admin";
  const settings = useAdminSettings({ enabled: isAdmin });
  // Kept here, not in the form, so its outcome outlives the form's remount after a save.
  const update = useUpdateAdminSettings();

  let content;
  if (!isAdmin) {
    content = <p>Only admins can change these settings.</p>;
  } else if (settings.isPending) {
    content = <Loading />;
  } else if (settings.isError) {
    content = <p role="alert">Could not load the settings: {settings.error.message}</p>;
  } else {
    // Keyed by the saved values, so the form shows them again after every save.
    content = (
      <RegistrationForm
        key={JSON.stringify(settings.data)}
        settings={settings.data}
        update={update}
      />
    );
  }
  return (
    <section className="page settings">
      <h2>Admin settings</h2>
      {content}
    </section>
  );
}

function RegistrationForm({
  settings,
  update,
}: {
  settings: AdminSettings;
  update: ReturnType<typeof useUpdateAdminSettings>;
}) {
  const { registration } = settings;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    update.mutate({
      registration: {
        enabled: form.get("enabled") === "on",
        allowed_email_domains: String(form.get("domains") ?? "")
          .split(/[\s,]+/)
          .filter(Boolean),
      },
    });
  }

  return (
    <section className="settings-section" aria-labelledby="registration-title">
      <h3 id="registration-title">Self-registration</h3>
      <form className="settings-form" onSubmit={onSubmit}>
        <label className="settings-check">
          <input name="enabled" type="checkbox" defaultChecked={registration.enabled} />
          Allow self-registration
        </label>
        <label>
          Allowed email domains
          <textarea
            name="domains"
            rows={4}
            defaultValue={registration.allowed_email_domains.join("\n")}
            placeholder="example.com"
          />
        </label>
        <p className="form-hint">
          One domain per line. Leave it empty to let any email address sign up. A domain
          matches exactly: list subdomains (e.g. eng.example.com) on their own lines.
        </p>
        {update.isError && (
          <p className="login-error" role="alert">
            {update.error.message}
          </p>
        )}
        {update.isSuccess && <p className="form-ok">Settings saved.</p>}
        <button type="submit" disabled={update.isPending}>
          Save settings
        </button>
      </form>
    </section>
  );
}
