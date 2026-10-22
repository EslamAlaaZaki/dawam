"use client";

import { Bell } from "lucide-react";
import Link from "next/link";

import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
} from "../api/notifications";
import { useMe } from "../api/queries";
import { Button } from "../components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";

/** The top bar's Notifications: a bell with an unread dot, and the list in a Popover. */
export function NotificationBell() {
  const me = useMe();
  const notifications = useNotifications(!!me.data && !me.data.must_change_password);
  const markRead = useMarkNotificationRead();
  const markAllRead = useMarkAllNotificationsRead();

  if (!notifications.data) {
    return null;
  }
  const { items, unread_count: unread } = notifications.data;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={`Notifications (${unread})`}
        >
          <Bell aria-hidden />
          {unread > 0 && (
            <span
              aria-hidden
              className="absolute top-1.5 end-1.5 size-2 rounded-full bg-danger ring-2 ring-background"
            />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-[min(22rem,calc(100vw-2rem))]"
        aria-label="Unread notifications"
        role="region"
      >
        {items.length === 0 ? (
          <p className="text-muted-foreground">You have no unread notifications.</p>
        ) : (
          <>
            <ul className="mb-2 flex flex-col">
              {items.map((notification) => (
                <li
                  key={notification.id}
                  className="flex items-start justify-between gap-3 border-b border-border py-2 first:pt-0 last:border-b-0"
                >
                  <span>
                    {notification.workspace_id ? (
                      <Link
                        href={`/workspaces/${notification.workspace_id}`}
                        className="text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary"
                      >
                        {notification.message}
                      </Link>
                    ) : (
                      notification.message
                    )}
                  </span>
                  <Button
                    size="sm"
                    disabled={markRead.isPending}
                    onClick={() => markRead.mutate(notification.id)}
                  >
                    Mark read
                  </Button>
                </li>
              ))}
            </ul>
            <Button size="sm" disabled={markAllRead.isPending} onClick={() => markAllRead.mutate()}>
              Mark all read
            </Button>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}
