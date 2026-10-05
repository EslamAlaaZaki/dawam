"use client";

import { useState, type FormEvent } from "react";

import { useSecurityEvents, type SecurityEvent, type SecurityEventFilters } from "../api/users";
import { Loading } from "../shell/Loading";

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
    <section className="page admin-page">
      <h2>Security events</h2>
      <form className="admin-filters" onSubmit={onFilter}>
        <label>
          Event type
          <select name="event_type" defaultValue="">
            <option value="">Any event</option>
            {EVENT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <label>
          Actor email
          <input name="actor" type="search" />
        </label>
        <label>
          From
          <input name="from" type="date" />
        </label>
        <label>
          To
          <input name="to" type="date" />
        </label>
        <button type="submit">Filter</button>
      </form>
      {events.isPending ? (
        <Loading />
      ) : events.isError ? (
        <p role="alert">Could not load the security events: {events.error.message}</p>
      ) : (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Event</th>
                <th scope="col">Actor</th>
                <th scope="col">Details</th>
                <th scope="col">IP address</th>
              </tr>
            </thead>
            <tbody>
              {events.data.pages.flatMap((page) =>
                page.items.map((event) => <EventRow key={event.id} event={event} />),
              )}
            </tbody>
          </table>
          {events.data.pages[0]?.items.length === 0 && <p>No events match.</p>}
          {events.hasNextPage && (
            <button
              type="button"
              onClick={() => void events.fetchNextPage()}
              disabled={events.isFetchingNextPage}
            >
              Load more
            </button>
          )}
        </>
      )}
    </section>
  );
}

function EventRow({ event }: { event: SecurityEvent }) {
  const details = Object.entries(event.metadata)
    .map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`)
    .join(", ");
  return (
    <tr aria-label={`${event.event_type} at ${when(event.created_at)}`}>
      <td>{when(event.created_at)}</td>
      <td>{event.event_type}</td>
      <td>{event.actor_email ?? event.actor_id ?? "—"}</td>
      <td>{details}</td>
      <td>{event.ip ?? "—"}</td>
    </tr>
  );
}
