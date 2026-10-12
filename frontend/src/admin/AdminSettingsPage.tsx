"use client";

import { type FormEvent } from "react";

import {
  useAdminSettings,
  useUpdateAdminSettings,
  type AdminSettings,
} from "../api/admin";
import { useMe } from "../api/queries";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Textarea } from "../components/ui/textarea";
import { Loading } from "../shell/Loading";
import { ErrorText, PageHead, labelClasses } from "./parts";

/** Installation-wide settings; only admins can read or change them (the API enforces it). */
export function AdminSettingsPage() {
  const me = useMe();
  const isAdmin = me.data?.system_role === "admin";
  const settings = useAdminSettings({ enabled: isAdmin });
  // Kept here, not in the form, so its outcome outlives the form's remount after a save.
  const update = useUpdateAdminSettings();

  let content;
  if (!isAdmin) {
    content = (
      <p className="text-sand-11">Only admins can change these settings.</p>
    );
  } else if (settings.isPending) {
    content = <Loading />;
  } else if (settings.isError) {
    content = (
      <ErrorText>
        Could not load the settings: {settings.error.message}
      </ErrorText>
    );
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
    <section className="flex flex-col gap-5">
      <PageHead
        title="Admin settings"
        description="Settings for the whole DAWAM server."
      />
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
    update.mutate(
      {
        registration: {
          enabled: form.get("enabled") === "on",
          allowed_email_domains: String(form.get("domains") ?? "")
            .split(/[\s,]+/)
            .filter(Boolean),
        },
      },
      { onSuccess: () => toast.success("Settings saved.") },
    );
  }

  return (
    <section
      className="flex max-w-xl flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5"
      aria-labelledby="registration-title"
    >
      <h3 id="registration-title" className="text-lg font-semibold">
        Self-registration
      </h3>
      <form className="flex flex-col gap-3.5" onSubmit={onSubmit}>
        <label className="flex items-center gap-2 text-sm font-medium">
          <input
            name="enabled"
            type="checkbox"
            className="size-4 accent-[var(--accent-solid)]"
            defaultChecked={registration.enabled}
          />
          Allow self-registration
        </label>
        <label className={labelClasses}>
          Allowed email domains
          <Textarea
            name="domains"
            rows={4}
            defaultValue={registration.allowed_email_domains.join("\n")}
            placeholder="example.com"
          />
        </label>
        <p className="text-sm text-sand-11">
          One domain per line. Leave it empty to let any email address sign up.
          A domain matches exactly: list subdomains (e.g. eng.example.com) on
          their own lines.
        </p>
        {update.isError && <ErrorText>{update.error.message}</ErrorText>}
        <Button
          type="submit"
          variant="primary"
          className="self-start"
          disabled={update.isPending}
        >
          Save settings
        </Button>
      </form>
    </section>
  );
}
