"use client";

import { ChevronDown, KeyRound, LogOut, User } from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";

import { useMe, useSignOut } from "../api/queries";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";

const THEMES = [
  ["system", "System"],
  ["light", "Light"],
  ["dark", "Dark"],
] as const;

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((word) => word[0]!.toUpperCase())
      .join("") || "?"
  );
}

/** The top bar's user menu: profile, change password, the theme, and sign out. */
export function UserMenu() {
  const me = useMe();
  const signOut = useSignOut();
  const { theme = "system", setTheme } = useTheme();
  if (!me.data) {
    return null;
  }
  return (
    <>
      {signOut.isError && (
        <span role="alert" className="text-sm text-danger-strong">
          Could not sign out: {signOut.error.message}
        </span>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger className="ms-1 flex h-8 items-center gap-2 rounded-md ps-1 pe-1.5 text-foreground transition-colors duration-120 outline-primary hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2 data-[state=open]:bg-muted">
          <span
            aria-hidden
            className="grid size-6 place-items-center rounded-full bg-primary-subtle text-2xs font-semibold text-primary"
          >
            {initials(me.data.display_name)}
          </span>
          <span className="hidden max-w-40 truncate text-sm font-medium sm:inline">
            {me.data.display_name}
          </span>
          <ChevronDown className="size-3.5 text-subtle-foreground" aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-52">
          <DropdownMenuLabel>{me.data.email}</DropdownMenuLabel>
          <DropdownMenuItem asChild>
            <Link href="/profile">
              <User aria-hidden />
              Profile
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href="/profile#password-title">
              <KeyRound aria-hidden />
              Change password
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>Theme</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={theme} onValueChange={setTheme} aria-label="Theme">
            {THEMES.map(([value, label]) => (
              <DropdownMenuRadioItem key={value} value={value}>
                {label}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="danger"
            disabled={signOut.isPending}
            onSelect={() => signOut.mutate()}
          >
            <LogOut aria-hidden />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
