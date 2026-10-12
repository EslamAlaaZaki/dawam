"use client";

import Link from "next/link";
import { MailPlus, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useMe } from "../api/queries";
import {
  useForcePasswordReset,
  useUpdateUser,
  useUsers,
  type AdminUser,
  type UserFilters,
} from "../api/users";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import {
  ErrorText,
  NativeSelect,
  PageHead,
  TableCard,
  labelClasses,
} from "./parts";

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
    <section className="flex flex-col gap-5">
      <PageHead
        title="Users"
        description="Everyone who can sign in to this DAWAM server."
        actions={
          <>
            <Button asChild>
              <Link href="/admin/users/new">
                <UserPlus aria-hidden />
                Create user
              </Link>
            </Button>
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
      {users.isPending ? (
        <Loading />
      ) : users.isError ? (
        <ErrorText>Could not load the users: {users.error.message}</ErrorText>
      ) : (
        <>
          <TableCard
            footer={
              <span>
                {users.data.pages.reduce((n, page) => n + page.items.length, 0)}{" "}
                users
                {users.hasNextPage && " so far"}
              </span>
            }
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">Email</TableHead>
                  <TableHead scope="col">Name</TableHead>
                  <TableHead scope="col">Role</TableHead>
                  <TableHead scope="col">Status</TableHead>
                  <TableHead scope="col">Last sign-in</TableHead>
                  <TableHead scope="col">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {users.data.pages.flatMap((page) =>
                  page.items.map((user) => (
                    <UserRow key={user.id} user={user} />
                  )),
                )}
              </TableBody>
            </Table>
          </TableCard>
          {users.data.pages[0]?.items.length === 0 && (
            <p className="text-sand-11">No users match.</p>
          )}
          {users.hasNextPage && (
            <Button
              className="self-start"
              onClick={() => void users.fetchNextPage()}
              disabled={users.isFetchingNextPage}
            >
              Load more
            </Button>
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
  const problem = update.isError
    ? update.error
    : reset.isError
      ? reset.error
      : null;

  let status = user.is_active ? "Active" : "Deactivated";
  if (user.is_active && user.must_change_password) {
    status += " (temporary password)";
  }

  return (
    <TableRow aria-label={user.email}>
      <TableCell>{user.email}</TableCell>
      <TableCell>{user.display_name}</TableCell>
      <TableCell>
        {user.system_role === "admin" ? (
          <Badge variant="accent">{ROLES.admin}</Badge>
        ) : (
          <span className="text-sand-11">{ROLES.user}</span>
        )}
      </TableCell>
      <TableCell>
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
          {status}
        </Badge>
      </TableCell>
      <TableCell className="text-sand-11">{when(user.last_login_at)}</TableCell>
      <TableCell className="whitespace-normal">
        <div className="flex flex-wrap gap-2">
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
              update.mutate({ id: user.id, is_active: !user.is_active })
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
        {problem && <ErrorText>{problem.message}</ErrorText>}
        {reset.data?.delivery === "link_for_admin" && (
          <p className="mt-1.5 text-sm text-sand-11">
            Sessions ended. The reset link could not be emailed: share it from{" "}
            <Link
              href="/admin/email/links"
              className="font-medium text-sand-12 underline"
            >
              Links to share
            </Link>
            .
          </p>
        )}
      </TableCell>
    </TableRow>
  );
}
