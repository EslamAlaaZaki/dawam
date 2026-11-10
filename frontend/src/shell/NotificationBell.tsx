"use client";

import {
  AtSign,
  BellOff,
  Bell,
  Check,
  CheckCheck,
  Cog,
  RefreshCw,
  RotateCw,
  TriangleAlert,
  UserRound,
  UserRoundSearch,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotifications,
  type Notification,
} from "../api/notifications";
import { useMe } from "../api/queries";
import { Button } from "../components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import { isToday, relativeTime } from "../lib/relativeTime";

const KINDS: Record<Notification["kind"], { icon: LucideIcon; tile: string }> = {
  mention: { icon: AtSign, tile: "bg-muted text-muted-foreground" },
  impact_alert: { icon: TriangleAlert, tile: "bg-warning-subtle text-warning-strong" },
  sync_alert: { icon: RefreshCw, tile: "bg-info-subtle text-info-strong" },
  ownership: { icon: UserRound, tile: "bg-muted text-muted-foreground" },
  needs_owner: { icon: UserRoundSearch, tile: "bg-warning-subtle text-warning-strong" },
  job: { icon: Cog, tile: "bg-muted text-muted-foreground" },
  regeneration: { icon: RotateCw, tile: "bg-muted text-muted-foreground" },
};

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
  const today = items.filter((item) => isToday(item.created_at));
  const earlier = items.filter((item) => !isToday(item.created_at));
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
        className="flex max-h-[70vh] w-[min(24rem,calc(100vw-2rem))] flex-col p-0"
        aria-label="Unread notifications"
        role="region"
      >
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <h3 className="text-base font-semibold">Notifications</h3>
          <span className="rounded-full bg-muted px-1.5 text-xs font-medium text-muted-foreground tabular-nums">
            {unread}
          </span>
          {items.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="ms-auto"
              disabled={markAllRead.isPending}
              onClick={() => markAllRead.mutate()}
            >
              <CheckCheck aria-hidden />
              Mark all read
            </Button>
          )}
        </div>
        {items.length === 0 ? (
          <div className="flex flex-col items-center gap-1 px-3 py-8 text-center">
            <BellOff aria-hidden className="mb-1 size-6 text-subtle-foreground" />
            <p className="font-medium">You&apos;re all caught up</p>
            <p className="text-sm text-muted-foreground">You have no unread notifications.</p>
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            <Group label="Today" items={today} onRead={markRead.mutate} pending={markRead.isPending} />
            <Group label="Earlier" items={earlier} onRead={markRead.mutate} pending={markRead.isPending} />
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}

function Group({
  label,
  items,
  onRead,
  pending,
}: {
  label: string;
  items: Notification[];
  onRead: (id: string) => void;
  pending: boolean;
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <section>
      <h4 className="px-3 pt-2 pb-1 text-xs font-medium text-subtle-foreground">{label}</h4>
      <ul className="flex flex-col">
        {items.map((notification) => {
          const { icon: Icon, tile } = KINDS[notification.kind];
          return (
            <li
              key={notification.id}
              className="group relative flex items-start gap-3 px-3 py-2 hover:bg-muted focus-within:bg-muted"
            >
              <span className={`grid size-7 shrink-0 place-items-center rounded-md ${tile}`}>
                <Icon aria-hidden className="size-4" />
              </span>
              <div className="min-w-0 flex-1">
                {notification.workspace_id ? (
                  <Link
                    href={`/workspaces/${notification.workspace_id}`}
                    title={notification.message}
                    className="line-clamp-2 text-sm text-foreground no-underline after:absolute after:inset-0 after:content-['']"
                  >
                    {notification.message}
                  </Link>
                ) : (
                  <p title={notification.message} className="line-clamp-2 text-sm">
                    {notification.message}
                  </p>
                )}
                <p className="text-xs text-subtle-foreground">
                  {relativeTime(notification.created_at)}
                </p>
              </div>
              <span aria-hidden className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Mark read"
                className="relative z-10 size-7 shrink-0 pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:focus-visible:opacity-100"
                disabled={pending}
                onClick={() => onRead(notification.id)}
              >
                <Check aria-hidden />
              </Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
