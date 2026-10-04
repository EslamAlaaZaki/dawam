"""Mail module: the one email-delivery service, SMTP settings, and undelivered links.

Public interface. Other modules import only what is re-exported here:

- ``MailService``: ``send`` delivers an email through the SMTP server an admin saved;
  without one (or when it fails), a message carrying a one-time link keeps that link
  for an admin to copy (spec §6.1, "Without SMTP"). Every module sends email through
  it. It is the ``dawam.platform.email.Mailer`` port, which is how ``auth`` (which
  this module depends on, and so cannot import it) gets it from the composition root.
- ``router``: ``GET|PUT|DELETE /admin/smtp``, ``POST /admin/smtp/test``,
  ``GET /admin/undelivered-links``, ``DELETE /admin/undelivered-links/{id}``.

Owns the ``smtp_settings`` and ``undelivered_links`` tables. The SMTP password and the
links are stored sealed with ``dawam.platform.crypto.SecretBox`` (AES-256-GCM). The
transport itself (``EmailSender``, ``SmtpEmailSender``) is kernel:
``dawam.platform.email``.
"""

from .api import router
from .service import KEEP_PASSWORD, MailService, SmtpSettings, UndeliveredLink

__all__ = ["KEEP_PASSWORD", "MailService", "SmtpSettings", "UndeliveredLink", "router"]
