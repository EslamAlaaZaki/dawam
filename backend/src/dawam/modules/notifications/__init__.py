"""Notifications module: a ``Notification`` row per recipient, and each user's list.

Public interface. Other modules import only what is re-exported here:

- ``NotificationService``: ``notify`` raises one notification per recipient (any
  module, any kind: later epics add theirs), ``page`` lists a user's notifications (read
  and unread) a page at a time, ``unread`` just the unread ones, ``mark_read`` /
  ``mark_all_read`` mark them read. ``Notification``, ``NotificationPage``,
  ``UnreadNotifications`` and ``NotificationKind`` are what it returns and takes.
- ``router``: ``GET /notifications`` (the signed-in user's paged list),
  ``POST /notifications/read-all`` and ``POST /notifications/{notification_id}/read``.

Owns the ``notifications`` table. Imports only ``auth``.
"""

from .api import router
from .service import (
    NOTIFICATION_KINDS,
    Notification,
    NotificationKind,
    NotificationPage,
    NotificationService,
    UnreadNotifications,
)

__all__ = [
    "NOTIFICATION_KINDS",
    "Notification",
    "NotificationKind",
    "NotificationPage",
    "NotificationService",
    "UnreadNotifications",
    "router",
]
