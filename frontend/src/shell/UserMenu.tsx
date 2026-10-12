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
        <span role="alert" className="text-sm text-red-11">
          Could not sign out: {signOut.error.message}
        </span>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger className="ml-1 flex h-8 items-center gap-2 rounded-md pr-1.5 pl-1 text-sand-12 hover:bg-sand-3 data-[state=open]:bg-sand-3">
          <span
            aria-hidden
            className="grid size-7 place-items-center rounded-full bg-jade-4 text-2xs font-semibold text-jade-12"
          >
            {initials(me.data.display_name)}
          </span>
          <span className="hidden max-w-40 truncate text-sm font-medium sm:inline">
            {me.data.display_name}
          </span>
          <ChevronDown className="size-3.5 text-sand-11" aria-hidden />
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
