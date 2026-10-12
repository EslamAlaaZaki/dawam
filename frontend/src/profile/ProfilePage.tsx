"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useChangePassword, useSignOutEverywhere, useUpdateDisplayName } from "../api/account";
import { useMe, type Me } from "../api/queries";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Input } from "../components/ui/input";

/** The signed-in user's own account: display name, password and sessions. */
export function ProfilePage() {
  const me = useMe();
  if (!me.data) {
    return null; // the signed-in layout only renders this once `me` is known
  }
  return (
    <section className="flex max-w-xl flex-col gap-5">
      <div>
        <h2 className="text-2xl font-semibold tracking-[-0.01em]">Your profile</h2>
        <p className="mt-0.5 text-sand-11">
          Signed in as <strong className="font-medium text-sand-12">{me.data.email}</strong>
        </p>
      </div>
      <DisplayNameForm me={me.data} />
      <ChangePasswordForm />
      <SignOutEverywhere />
    </section>
  );
}

function DisplayNameForm({ me }: { me: Me }) {
  const update = useUpdateDisplayName();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update.mutate(String(new FormData(event.currentTarget).get("display_name") ?? ""), {
      onSuccess: () => toast.success("Display name saved."),
    });
  }

  return (
    <section
      className="flex flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5"
      aria-labelledby="display-name-title"
    >
      <h3 className="text-lg font-semibold" id="display-name-title">
        Name
      </h3>
      <form className="flex flex-col gap-3.5" onSubmit={onSubmit}>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Display name
          <Input
            name="display_name"
            defaultValue={me.display_name}
            autoComplete="name"
            maxLength={200}
          />
        </label>
        {update.isError && (
          <p className="text-sm text-red-11" role="alert">
            {update.error.message}
          </p>
        )}
        <Button type="submit" variant="primary" className="self-start" disabled={update.isPending}>
          Save name
        </Button>
      </form>
    </section>
  );
}

function ChangePasswordForm() {
  const change = useChangePassword();
  const [mismatch, setMismatch] = useState(false);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const next = String(form.get("new_password") ?? "");
    if (next !== String(form.get("confirm") ?? "")) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate(
      {
        current_password: String(form.get("current_password") ?? ""),
        new_password: next,
      },
      {
        onSuccess: () => {
          formElement.reset();
          toast.success("Password changed. Your other sessions have been signed out.");
        },
      },
    );
  }

  const problem = mismatch
    ? "The new passwords do not match."
    : change.isError
      ? change.error.message
      : null;

  return (
    <section
      className="flex flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5"
      aria-labelledby="password-title"
    >
      <h3 className="text-lg font-semibold" id="password-title">
        Change password
      </h3>
      <form className="flex flex-col gap-3.5" onSubmit={onSubmit}>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Current password
          <Input name="current_password" type="password" autoComplete="current-password" required />
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          New password
          <Input name="new_password" type="password" autoComplete="new-password" required />
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-medium">
          Confirm new password
          <Input name="confirm" type="password" autoComplete="new-password" required />
        </label>
        <p className="text-sm text-sand-11">
          At least 10 characters, and not a common password. Changing it signs you out on every
          other device.
        </p>
        {problem && (
          <p className="text-sm text-red-11" role="alert">
            {problem}
          </p>
        )}
        <Button type="submit" variant="primary" className="self-start" disabled={change.isPending}>
          Change password
        </Button>
      </form>
    </section>
  );
}

function SignOutEverywhere() {
  const router = useRouter();
  const signOut = useSignOutEverywhere({
    onSignedOut: () => router.replace("/login"),
  });
  return (
    <section
      className="flex flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5"
      aria-labelledby="sessions-title"
    >
      <h3 className="text-lg font-semibold" id="sessions-title">
        Sessions
      </h3>
      <p className="text-sm text-sand-11">
        Lost a device, or signed in somewhere you shouldn&apos;t have? Sign out of every session,
        this one included.
      </p>
      {signOut.isError && (
        <p className="text-sm text-red-11" role="alert">
          Could not sign out everywhere: {signOut.error.message}
        </p>
      )}
      <Button
        className="self-start border-red-11 text-red-11"
        disabled={signOut.isPending}
        onClick={() => signOut.mutate()}
      >
        Sign out everywhere
      </Button>
    </section>
  );
}
