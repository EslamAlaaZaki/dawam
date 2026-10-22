"use client";

import Link from "next/link";
import { MailPlus, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useMe } from "../api/queries";
import {
  useCreateUser,
  useForcePasswordReset,
  useUpdateUser,
  useUsers,
  type AdminUser,
  type UserFilters,
} from "../api/users";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { EntityTable, type EntityColumn } from "../components/entity/EntityTable";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { PageHeader } from "../components/PageHeader";
import { ErrorText, NativeSelect, labelClasses } from "./parts";

const ROLES = { admin: "Admin", user: "User" } as const;

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "Never";
}

function statusText(user: AdminUser): string {
  let status = user.is_active ? "Active" : "Deactivated";
  if (user.is_active && user.must_change_password) {
    status += " (temporary password)";
  }
  return status;
}

const USER_COLUMNS: EntityColumn<AdminUser>[] = [
  { header: "Email", cell: (user) => user.email },
  { header: "Name", cell: (user) => user.display_name },
  {
    header: "Role",
    cell: (user) =>
      user.system_role === "admin" ? (
        <Badge variant="accent">{ROLES.admin}</Badge>
      ) : (
        <span className="text-muted-foreground">{ROLES.user}</span>
      ),
  },
  {
    header: "Status",
    cell: (user) => (
      <Badge
        variant={
          !user.is_active
            ? "danger"
            : user.must_change_password
              ? "warning"
              : "success"
        }
        dot
      >
        {statusText(user)}
      </Badge>
    ),
  },
  {
    header: "Last sign-in",
    className: "text-muted-foreground",
    cell: (user) => when(user.last_login_at),
  },
];

/** Every user, searched and filtered, with what an admin can do to each (stories 14-19). */
export function UsersPage() {
  const [filters, setFilters] = useState<UserFilters>({});
  const users = useUsers(filters);
  const [creating, setCreating] = useState(false);
  const createButton = (
    <Button onClick={() => setCreating(true)}>
      <UserPlus aria-hidden />
      Create user
    </Button>
  );

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
    <section className="flex flex-col gap-5">
      <PageHeader
        title="Users"
        description="Everyone who can sign in to this DAWAM server."
        actions={
          <>
            {createButton}
            <Button asChild variant="primary">
              <Link href="/admin/invitations">
                <MailPlus aria-hidden />
                Invite user
              </Link>
            </Button>
          </>
        }
      />
      <form
        className="flex flex-wrap items-end gap-2"
        role="search"
        aria-label="Filter users"
        onSubmit={onSearch}
      >
        <label className={`${labelClasses} w-64`}>
          Search
          <Input name="q" type="search" placeholder="Email or name" />
        </label>
        <label className={labelClasses}>
          Role
          <NativeSelect name="role" defaultValue="">
            <option value="">Any role</option>
            <option value="admin">Admin</option>
            <option value="user">User</option>
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Status
          <NativeSelect name="status" defaultValue="">
            <option value="">Any status</option>
            <option value="active">Active</option>
            <option value="deactivated">Deactivated</option>
          </NativeSelect>
        </label>
        <Button type="submit" className="h-[34px]">
          Search
        </Button>
      </form>
      <EntityTable
        rows={users.data?.pages.flatMap((page) => page.items)}
        rowKey={(user) => user.id}
        rowLabel={(user) => user.email}
        columns={USER_COLUMNS}
        actions={(user) => <UserActions user={user} />}
        isPending={users.isPending}
        error={users.error}
        loadErrorPrefix="Could not load the users:"
        emptyMessage="No users match."
        emptyAction={createButton}
        count="users"
        hasMore={users.hasNextPage}
        isLoadingMore={users.isFetchingNextPage}
        onLoadMore={() => void users.fetchNextPage()}
      />
      <CreateUserDialog open={creating} onOpenChange={setCreating} />
    </section>
  );
}

/** What an admin can do to one user; users are never deleted. */
function UserActions({ user }: { user: AdminUser }) {
  const me = useMe();
  const update = useUpdateUser();
  const reset = useForcePasswordReset();
  const [editing, setEditing] = useState(false);
  const [deactivating, setDeactivating] = useState(false);
  const isMe = me.data?.id === user.id;
  const problem = update.isError
    ? update.error
    : reset.isError
      ? reset.error
      : null;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setEditing(true)}>
          Edit
        </Button>
        <Button
          size="sm"
          disabled={update.isPending}
          onClick={() =>
            update.mutate({
              id: user.id,
              system_role: user.system_role === "admin" ? "user" : "admin",
            })
          }
        >
          {user.system_role === "admin" ? "Make user" : "Make admin"}
        </Button>
        <Button
          size="sm"
          disabled={update.isPending}
          onClick={() =>
            user.is_active
              ? setDeactivating(true)
              : update.mutate({ id: user.id, is_active: true })
          }
        >
          {user.is_active ? "Deactivate" : "Reactivate"}
        </Button>
        {!isMe && user.is_active && (
          <Button
            size="sm"
            disabled={reset.isPending}
            onClick={() =>
              reset.mutate(user.id, {
                onSuccess: (result) => {
                  if (result.delivery === "sent") {
                    toast.success(
                      `Sessions ended; a reset link was emailed to ${user.email}.`,
                    );
                  }
                },
              })
            }
          >
            Force password reset
          </Button>
        )}
      </div>
      {problem && !deactivating && <ErrorText>{problem.message}</ErrorText>}
      {reset.data?.delivery === "link_for_admin" && (
        <p className="mt-1.5 text-sm text-muted-foreground">
          Sessions ended. The reset link could not be emailed: share it from{" "}
          <Link
            href="/admin/email/links"
            className="font-medium text-foreground underline"
          >
            Links to share
          </Link>
          .
        </p>
      )}
      <EditUserDialog user={user} open={editing} onOpenChange={setEditing} />
      <ConfirmDialog
        open={deactivating}
        onOpenChange={(next) => {
          if (!next) update.reset();
          setDeactivating(next);
        }}
        title={`Deactivate ${user.email}?`}
        description="They are signed out at once and cannot sign in until you reactivate them."
        confirmLabel="Deactivate"
        pending={update.isPending}
        error={update.error}
        onConfirm={() =>
          update.mutate(
            { id: user.id, is_active: false },
            { onSuccess: () => setDeactivating(false) },
          )
        }
      />
    </>
  );
}

/** Edit one user; the API changes the role only, so the identity fields are read-only. */
function EditUserDialog({
  user,
  open,
  onOpenChange,
}: {
  user: AdminUser;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateUser();

  function onSubmit(form: FormData) {
    const system_role = form.get("system_role") === "admin" ? "admin" : "user";
    if (system_role === user.system_role) {
      onOpenChange(false);
      return;
    }
    update.mutate(
      { id: user.id, system_role },
      {
        onSuccess: () => {
          toast.success(`${user.email} updated.`);
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) update.reset();
        onOpenChange(next);
      }}
      title="Edit user"
      description={user.email}
      submitLabel="Save"
      onSubmit={onSubmit}
      pending={update.isPending}
      error={update.error}
    >
      <label className={labelClasses}>
        Email
        <Input value={user.email} readOnly />
      </label>
      <label className={labelClasses}>
        Display name
        <Input value={user.display_name} readOnly />
      </label>
      <label className={labelClasses}>
        System role
        <NativeSelect name="system_role" defaultValue={user.system_role}>
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </NativeSelect>
      </label>
    </EntityFormDialog>
  );
}

/**
 * Create a user with a temporary password (story 15a): they must change it at first
 * sign-in, so the admin never knows their real password.
 */
function CreateUserDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateUser();

  function onSubmit(form: FormData) {
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
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) create.reset();
        onOpenChange(next);
      }}
      title="Create user"
      description="Set up an account with a temporary password the user replaces at first sign-in."
      submitLabel="Create user"
      onSubmit={onSubmit}
      pending={create.isPending}
      error={create.error}
    >
      <label className={labelClasses}>
        Email
        <Input name="email" type="email" autoComplete="off" required />
      </label>
      <label className={labelClasses}>
        Display name
        <Input name="display_name" autoComplete="off" maxLength={200} required />
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
        <Input name="temporary_password" type="text" autoComplete="off" required />
      </label>
      <p className="text-sm text-muted-foreground">
        At least 10 characters, and not a common password. Give it to the user through a
        channel you trust; they must replace it when they first sign in.
      </p>
    </EntityFormDialog>
  );
}
