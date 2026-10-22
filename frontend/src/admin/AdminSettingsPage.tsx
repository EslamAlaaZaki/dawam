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
import {
  ErrorText,
  PageHead,
  formCardClasses,
  h3Classes,
  labelClasses,
} from "./parts";

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
      <p className="text-muted-foreground">Only admins can change these settings.</p>
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
    <section className="flex flex-col gap-6">
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
    <form onSubmit={onSubmit} aria-label="Self-registration">
      <section aria-label="Self-registration" className={formCardClasses}>
        <div>
          <h3 className={h3Classes}>Self-registration</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Who can create an account without an invitation.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm font-medium">
          <input
            name="enabled"
            type="checkbox"
            className="size-4 accent-primary"
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
        <p className="text-sm text-muted-foreground">
          One domain per line. Leave it empty to let any email address sign up.
          A domain matches exactly: list subdomains (e.g. eng.example.com) on
          their own lines.
        </p>
        {update.isError && <ErrorText>{update.error.message}</ErrorText>}
        <div className="flex items-center gap-2">
          <Button type="submit" variant="primary" disabled={update.isPending}>
            Save settings
          </Button>
        </div>
      </section>
    </form>
  );
}
