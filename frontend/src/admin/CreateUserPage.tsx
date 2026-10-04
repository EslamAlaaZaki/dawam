"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent } from "react";

import { useCreateUser } from "../api/users";

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
    create.mutate(
      {
        email: String(form.get("email") ?? ""),
        display_name: String(form.get("display_name") ?? ""),
        system_role: form.get("system_role") === "admin" ? "admin" : "user",
        temporary_password: String(form.get("temporary_password") ?? ""),
      },
      { onSuccess: () => router.push("/admin/users") },
    );
  }

  return (
    <section className="page settings">
      <h2>Create user</h2>
      <form className="settings-form" onSubmit={onSubmit}>
        <label>
          Email
          <input name="email" type="email" autoComplete="off" required />
        </label>
        <label>
          Display name
          <input name="display_name" autoComplete="off" maxLength={200} required />
        </label>
        <label>
          System role
          <select name="system_role" defaultValue="user">
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <label>
          Temporary password
          <input name="temporary_password" type="text" autoComplete="off" required />
        </label>
        <p className="form-hint">
          At least 10 characters, and not a common password. Give it to the user through a
          channel you trust; they must replace it when they first sign in.
        </p>
        {create.isError && (
          <p className="login-error" role="alert">
            {create.error.message}
          </p>
        )}
        <div className="admin-actions">
          <button type="submit" disabled={create.isPending}>
            Create user
          </button>
          <Link href="/admin/users">Cancel</Link>
        </div>
      </form>
    </section>
  );
}
