"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useMe } from "../api/queries";
import {
  useForcePasswordReset,
  useUpdateUser,
  useUsers,
  type AdminUser,
  type UserFilters,
} from "../api/users";
import { Loading } from "../shell/Loading";

const ROLES = { admin: "Admin", user: "User" } as const;

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "Never";
}

/** Every user, searched and filtered, with what an admin can do to each (stories 14-19). */
export function UsersPage() {
  const [filters, setFilters] = useState<UserFilters>({});
  const users = useUsers(filters);

  function onSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const q = String(form.get("q") ?? "").trim();
    const role = String(form.get("role") ?? "");
    const status = String(form.get("status") ?? "");
    setFilters({
      ...(q && { q }),
      ...((role === "admin" || role === "user") && { role }),
      ...(status && { active: status === "active" }),
    });
  }

  return (
    <section className="page admin-page">
      <h2>Users</h2>
      <p>
        <Link href="/admin/users/new">Create user</Link>
      </p>
      <form className="admin-filters" role="search" onSubmit={onSearch}>
        <label>
          Search
          <input name="q" type="search" placeholder="Email or name" />
        </label>
        <label>
          Role
          <select name="role" defaultValue="">
            <option value="">Any role</option>
            <option value="admin">Admin</option>
            <option value="user">User</option>
          </select>
        </label>
        <label>
          Status
          <select name="status" defaultValue="">
            <option value="">Any status</option>
            <option value="active">Active</option>
            <option value="deactivated">Deactivated</option>
          </select>
        </label>
        <button type="submit">Search</button>
      </form>
      {users.isPending ? (
        <Loading />
      ) : users.isError ? (
        <p role="alert">Could not load the users: {users.error.message}</p>
      ) : (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Name</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Last sign-in</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.data.pages.flatMap((page) =>
                page.items.map((user) => <UserRow key={user.id} user={user} />),
              )}
            </tbody>
          </table>
          {users.data.pages[0]?.items.length === 0 && <p>No users match.</p>}
          {users.hasNextPage && (
            <button
              type="button"
              onClick={() => void users.fetchNextPage()}
              disabled={users.isFetchingNextPage}
            >
              Load more
            </button>
          )}
        </>
      )}
    </section>
  );
}

function UserRow({ user }: { user: AdminUser }) {
  const me = useMe();
  const update = useUpdateUser();
  const reset = useForcePasswordReset();
  const isMe = me.data?.id === user.id;
  const problem = update.isError ? update.error : reset.isError ? reset.error : null;

  let status = user.is_active ? "Active" : "Deactivated";
  if (user.is_active && user.must_change_password) {
    status += " (temporary password)";
  }

  return (
    <tr aria-label={user.email}>
      <td>{user.email}</td>
      <td>{user.display_name}</td>
      <td>{ROLES[user.system_role]}</td>
      <td>{status}</td>
      <td>{when(user.last_login_at)}</td>
      <td>
        <div className="admin-actions">
          <button
            type="button"
            className="secondary"
            disabled={update.isPending}
            onClick={() =>
              update.mutate({
                id: user.id,
                system_role: user.system_role === "admin" ? "user" : "admin",
              })
            }
          >
            {user.system_role === "admin" ? "Make user" : "Make admin"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={update.isPending}
            onClick={() => update.mutate({ id: user.id, is_active: !user.is_active })}
          >
            {user.is_active ? "Deactivate" : "Reactivate"}
          </button>
          {!isMe && (
            <button
              type="button"
              className="secondary"
              disabled={reset.isPending}
              onClick={() => reset.mutate(user.id)}
            >
              Force password reset
            </button>
          )}
        </div>
        {problem && <p role="alert">{problem.message}</p>}
        {reset.data?.delivery === "sent" && (
          <p className="form-ok">Sessions ended; a reset link was emailed to {user.email}.</p>
        )}
        {reset.data?.delivery === "link_for_admin" && (
          <p className="form-ok">
            Sessions ended. The reset link could not be emailed: share it from{" "}
            <Link href="/admin/email/links">Links to share</Link>.
          </p>
        )}
      </td>
    </tr>
  );
}
