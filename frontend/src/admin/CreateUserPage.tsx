"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent } from "react";

import { useCreateUser } from "../api/users";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { ErrorText, NativeSelect, PageHead, labelClasses } from "./parts";

/**
 * Create a user with a temporary password (story 15a): they must change it at first
 * sign-in, so the admin never knows their real password.
 */
export function CreateUserPage() {
  const router = useRouter();
  const create = useCreateUser();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "");
    create.mutate(
      {
        email,
        display_name: String(form.get("display_name") ?? ""),
        system_role: form.get("system_role") === "admin" ? "admin" : "user",
        temporary_password: String(form.get("temporary_password") ?? ""),
      },
      {
        onSuccess: () => {
          toast.success(`${email} created.`);
          router.push("/admin/users");
        },
      },
    );
  }

  return (
    <section className="flex max-w-xl flex-col gap-5">
      <PageHead
        title="Create user"
        description="Set up an account with a temporary password the user replaces at first sign-in."
      />
      <form
        className="flex flex-col gap-3.5 rounded-xl border border-sand-5 bg-sand-1 p-5"
        onSubmit={onSubmit}
      >
        <label className={labelClasses}>
          Email
          <Input name="email" type="email" autoComplete="off" required />
        </label>
        <label className={labelClasses}>
          Display name
          <Input
            name="display_name"
            autoComplete="off"
            maxLength={200}
            required
          />
        </label>
        <label className={labelClasses}>
          System role
          <NativeSelect name="system_role" defaultValue="user">
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Temporary password
          <Input
            name="temporary_password"
            type="text"
            autoComplete="off"
            required
          />
        </label>
        <p className="text-sm text-sand-11">
          At least 10 characters, and not a common password. Give it to the user
          through a channel you trust; they must replace it when they first sign
          in.
        </p>
        {create.isError && <ErrorText>{create.error.message}</ErrorText>}
        <div className="flex items-center gap-2">
          <Button type="submit" variant="primary" disabled={create.isPending}>
            Create user
          </Button>
          <Button asChild variant="ghost">
            <Link href="/admin/users">Cancel</Link>
          </Button>
        </div>
      </form>
    </section>
  );
}
