"use client";

import Link from "next/link";
import { useState } from "react";

import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
} from "../api/notifications";
import { useMe } from "../api/queries";

/** The header's unread notifications: a count, and the list when opened. */
export function NotificationBell() {
  const me = useMe();
  const notifications = useNotifications(!!me.data && !me.data.must_change_password);
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();
  const [open, setOpen] = useState(false);

  if (!notifications.data) {
    return null;
  }
  const { items, unread_count: unread } = notifications.data;
  return (
    <div className="shell-notifications">
      <button
        type="button"
        className="secondary"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        Notifications ({unread})
      </button>
      {open && (
        <section className="notification-list" aria-label="Unread notifications">
          {items.length === 0 ? (
            <p className="empty-state">You have no unread notifications.</p>
          ) : (
            <>
              <ul>
                {items.map((notification) => (
                  <li key={notification.id}>
                    <span>
                      {notification.workspace_id ? (
                        <Link href={`/workspaces/${notification.workspace_id}`}>
                          {notification.message}
                        </Link>
                      ) : (
                        notification.message
                      )}
                    </span>
                    <button
                      type="button"
                      className="secondary"
                      disabled={markRead.isPending}
                      onClick={() => markRead.mutate(notification.id)}
                    >
                      Mark read
                    </button>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                className="secondary"
                disabled={markAllRead.isPending}
                onClick={() => markAllRead.mutate()}
              >
                Mark all read
              </button>
            </>
          )}
        </section>
      )}
    </div>
  );
}
