import type { FormEventHandler, ReactNode } from "react";

import { Label } from "../components/ui/label";
import { Wordmark } from "../components/Wordmark";
import { cn } from "../lib/utils";
import { ApiStatus } from "../shell/ApiStatus";

/** The pages someone uses to get in (or, for /change-password, to finish getting in). */
export const AUTH_PATHS: ReadonlySet<string> = new Set([
  "/login",
  "/signup",
  "/forgot-password",
  "/reset-password",
  "/accept-invitation",
  "/change-password",
]);

/**
 * The frame of those pages instead of the app's (prototype "Sign in"): a centred card on
 * a plain background with the wordmark above it, following the system's theme.
 */
export function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <div className="dawam-ui grid min-h-screen place-items-center bg-sand-2 px-4 pt-6 pb-24 scheme-light dark:scheme-dark">
      <div className="flex w-full max-w-[360px] flex-col gap-5">
        <Wordmark className="justify-center text-md [&_svg]:size-[26px]" />
        <main>{children}</main>
        <footer className="flex flex-col gap-1 text-center text-mono text-sand-11">
          <p>Data Analysis &amp; Warehouse Architecture Modeler</p>
          <p>
            <ApiStatus />
          </p>
        </footer>
      </div>
    </div>
  );
}

/** The card: a form (or, with no `onSubmit`, a notice) under its heading. */
export function AuthCard({
  title,
  titleId,
  description,
  onSubmit,
  children,
}: {
  title: string;
  titleId: string;
  description?: ReactNode;
  onSubmit?: FormEventHandler<HTMLFormElement>;
  children: ReactNode;
}) {
  const body = (
    <>
      <h1 id={titleId} className="text-xl font-semibold">
        {title}
      </h1>
      {description && <p className="-mt-2.5 text-sand-11">{description}</p>}
      {children}
    </>
  );
  const classes = "flex flex-col gap-4 rounded-xl border border-sand-5 bg-sand-1 p-6";
  return onSubmit ? (
    <form className={classes} onSubmit={onSubmit} aria-labelledby={titleId}>
      {body}
    </form>
  ) : (
    <section className={classes} aria-labelledby={titleId}>
      {body}
    </section>
  );
}

/** A labelled field; `aside` sits at the label's end (e.g. "Forgot password?"). */
export function AuthField({
  id,
  label,
  aside,
  children,
}: {
  id: string;
  label: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <Label htmlFor={id}>{label}</Label>
        {aside}
      </div>
      {children}
    </div>
  );
}

/** A form's problem, next to the form (errors are never toasts). */
export function AuthError({ children }: { children: ReactNode }) {
  return (
    <p className="text-sm text-red-11" role="alert">
      {children}
    </p>
  );
}

export function AuthHint({
  id,
  className,
  children,
}: {
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <p id={id} className={cn("text-xs text-sand-11", className)}>
      {children}
    </p>
  );
}

/**
 * A link in the card. Underlined: next to body text the accent alone is not 3:1 apart
 * from it (WCAG 1.4.1).
 */
export const authLinkClasses =
  "text-sm font-medium text-jade-11 underline decoration-jade-7 underline-offset-2 hover:decoration-current";
