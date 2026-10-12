"use client";

import { useState, type FormEvent } from "react";

import {
  useSecurityEvents,
  type SecurityEvent,
  type SecurityEventFilters,
} from "../api/users";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
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
  labelClasses, EmptyState } from "./parts";

/** The event types DAWAM records, for the filter. */
const EVENT_TYPES = [
  "login_succeeded",
  "login_failed",
  "account_locked",
  "user_registered",
  "user_created",
  "user_role_changed",
  "user_deactivated",
  "user_reactivated",
  "password_changed",
  "password_change_failed",
  "password_reset_requested",
  "password_reset",
  "password_reset_forced",
  "signed_out_everywhere",
  "registration_settings_changed",
  "smtp_settings_changed",
  "smtp_settings_removed",
  "workspace_deleted",
  "workspace_ownership_reassigned",
];

/** Midnight at the start of a `YYYY-MM-DD` day in the admin's time zone, `days` later. */
function startOfDay(day: string, days = 0): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year!, month! - 1, date! + days).toISOString();
}

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

/** The security-event log (story 23): sign-ins, role changes, deactivations, ... */
export function SecurityEventsPage() {
  const [filters, setFilters] = useState<SecurityEventFilters>({});
  const events = useSecurityEvents(filters);

  function onFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const eventType = String(form.get("event_type") ?? "");
    const actor = String(form.get("actor") ?? "").trim();
    const from = String(form.get("from") ?? "");
    const to = String(form.get("to") ?? "");
    setFilters({
      ...(eventType && { event_type: eventType }),
      ...(actor && { actor }),
      ...(from && { since: startOfDay(from) }),
      ...(to && { until: startOfDay(to, 1) }), // the whole of the "To" day
    });
  }

  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Security events"
        description="Sign-ins, role changes, deactivations and other security-relevant activity."
      />
      <form
        className="flex flex-wrap items-end gap-2"
        aria-label="Filter security events"
        onSubmit={onFilter}
      >
        <label className={labelClasses}>
          Event type
          <NativeSelect name="event_type" defaultValue="">
            <option value="">Any event</option>
            {EVENT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Actor email
          <Input name="actor" type="search" />
        </label>
        <label className={labelClasses}>
          From
          <Input name="from" type="date" />
        </label>
        <label className={labelClasses}>
          To
          <Input name="to" type="date" />
        </label>
        <Button type="submit" className="h-[34px]">
          Filter
        </Button>
      </form>
      {events.isPending ? (
        <Loading />
      ) : events.isError ? (
        <ErrorText>
          Could not load the security events: {events.error.message}
        </ErrorText>
      ) : (
        <>
          <TableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">When</TableHead>
                  <TableHead scope="col">Event</TableHead>
                  <TableHead scope="col">Actor</TableHead>
                  <TableHead scope="col">Details</TableHead>
                  <TableHead scope="col">IP address</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.data.pages.flatMap((page) =>
                  page.items.map((event) => (
                    <EventRow key={event.id} event={event} />
                  )),
                )}
              </TableBody>
            </Table>
          </TableCard>
          {events.data.pages[0]?.items.length === 0 && (
            <EmptyState>No events match.</EmptyState>
          )}
          {events.hasNextPage && (
            <Button
              className="self-start"
              onClick={() => void events.fetchNextPage()}
              disabled={events.isFetchingNextPage}
            >
              Load more
            </Button>
          )}
        </>
      )}
    </section>
  );
}

function EventRow({ event }: { event: SecurityEvent }) {
  const details = Object.entries(event.metadata)
    .map(
      ([key, value]) =>
        `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`,
    )
    .join(", ");
  return (
    <TableRow aria-label={`${event.event_type} at ${when(event.created_at)}`}>
      <TableCell className="text-sand-11">{when(event.created_at)}</TableCell>
      <TableCell>{event.event_type}</TableCell>
      <TableCell>{event.actor_email ?? event.actor_id ?? "—"}</TableCell>
      <TableCell className="whitespace-normal break-words text-sand-11">
        {details}
      </TableCell>
      <TableCell className="text-sand-11">{event.ip ?? "—"}</TableCell>
    </TableRow>
  );
}
