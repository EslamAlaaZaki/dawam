# DAWAM feature reference

How DAWAM's accounts, security controls, email, Workspaces and files behave, with the
settings and API endpoints behind them. For installing and running DAWAM see
[`deploy/README.md`](../deploy/README.md); for the full product specification see
[`spec.md`](spec.md). Every setting named here is described in
[`.env.example`](../.env.example).

- [HTTP basics](#http-basics)
- [Sign in and accounts](#sign-in-and-accounts)
- [Email](#email)
- [Workspaces](#workspaces)
- [Files and documents](#files-and-documents)

## HTTP basics

Endpoints for monitoring and the API:

- `GET /healthz`: 200 when the process is up.
- `GET /readyz`: 200 only when the database is reachable and migrations are at head
  (503 `not_ready` otherwise). Compose uses it as the `app` health check.
- `GET /api/v1/version`, and the API docs at `/api/v1/docs`
  (OpenAPI spec at `/api/v1/openapi.json`). The docs page loads Swagger UI from
  cdn.jsdelivr.net, so the browser needs internet access to show it.

Every response carries the security headers of the spec's baseline: a
Content-Security-Policy (with `frame-ancestors 'none'`), `X-Content-Type-Options:
nosniff` and `Referrer-Policy: same-origin`. FastAPI sets them on the API responses,
and `web` on the pages and files it serves. Next.js puts inline scripts in every page,
so the pages' policy allows scripts only from DAWAM itself or carrying a nonce that is
new on every request (`frontend/src/security/headers.ts`); that is why every page is
rendered per request.

For anything beyond local use, put DAWAM behind a TLS-terminating reverse proxy in
front of `edge` (`DAWAM_PORT`) and list the proxy's addresses in
`DAWAM_TRUSTED_PROXY_CIDRS`. From those addresses only, `edge` takes the client from
`X-Forwarded-For` and the scheme from `X-Forwarded-Proto`; requests that arrived over
HTTPS then get `Strict-Transport-Security` (pages and API) and `Secure` cookies (see
`.env.example`).

Logs are JSON lines on stdout (`docker compose logs -f app web edge`). Every request gets a
request id: send `X-Request-ID` to choose it, and it is returned in the response and
on every log line for that request.

## Sign in and accounts

- **First admin.** When the app starts and no admin exists, it creates one from
  `DAWAM_ADMIN_EMAIL` and `DAWAM_ADMIN_PASSWORD` (the spec's `ADMIN_EMAIL` /
  `ADMIN_PASSWORD`, with DAWAM's `DAWAM_` prefix). Once an admin exists, later starts
  never create or change one. If no admin exists but a non-admin user already has
  `DAWAM_ADMIN_EMAIL`, startup fails with an error naming that variable, and the user
  is left unchanged.
- **Sessions** are stored in the database. The browser holds only a random token in
  the `dawam_session` cookie (`HttpOnly`, `SameSite=Lax`, `Secure` when served over
  HTTPS). A session ends after `DAWAM_SESSION_IDLE_TIMEOUT_HOURS` (default 8) without
  a request, or `DAWAM_SESSION_ABSOLUTE_TIMEOUT_DAYS` (default 14) after sign-in.
  Behind a TLS-terminating proxy, cookies are `Secure` only once
  `DAWAM_FORWARDED_ALLOW_IPS` trusts it (see [HTTP basics](#http-basics)).
- **CSRF.** Every `POST`/`PUT`/`PATCH`/`DELETE` under `/api/v1` must send the value
  of the `dawam_csrf` cookie in the `X-CSRF-Token` header (double submit); any
  response sets that cookie for a client without one.
- **Lockout.** After `DAWAM_LOGIN_MAX_FAILURES` (default 5) consecutive failed
  sign-ins an account is locked for `DAWAM_LOGIN_LOCKOUT_MINUTES` (default 15): even
  the right password gets `429 account_locked` (with `Retry-After`) until then. A
  successful sign-in resets the count, and failures more than a day apart do not add
  up. An email without an account behaves the same, so the lock does not reveal which
  emails have accounts.
- **Rate limiting per address.** A client address with `DAWAM_LOGIN_IP_MAX_FAILURES`
  (default 20) failed sign-ins in the last `DAWAM_LOGIN_IP_WINDOW_MINUTES` (default
  15) gets `429 too_many_attempts` until the oldest leaves the window. Only that
  address waits; users behind one shared NAT address share its limit. Both limits are
  kept in PostgreSQL, so they need no extra service and hold across restarts.
- **Security events.** Successful and failed sign-ins, lockouts, sign-ups, password
  changes, signing out everywhere and changes to the registration settings are
  recorded (who, if known, the account, the client address and the time; never a
  password) in the `security_events` table, for the admin's security log.
- **Passwords** must be at least 10 characters long and not on DAWAM's list of
  common passwords (compared ignoring case), whenever one is set: sign-up, the first
  admin, and every password change. The list ships with DAWAM, so it works offline: it
  is zxcvbn's list of 47,000 common passwords, under the MIT licence
  (`backend/src/dawam/modules/auth/internal/common_passwords.LICENSE.txt`).
- **Self-registration** is off until an admin turns it on (Admin settings, or
  `PUT /api/v1/admin/settings`), optionally only for some email domains (exact
  matches; list subdomains separately). While it is off the login page shows no
  sign-up link and `POST /api/v1/auth/register` answers `403 registration_closed`; an
  email outside the allowed domains gets `403 email_domain_not_allowed`. An address
  that tried to sign up `DAWAM_REGISTER_IP_MAX_ATTEMPTS` (default 10) times in the
  last `DAWAM_REGISTER_IP_WINDOW_MINUTES` (default 60) gets `429 too_many_attempts`.
- **Profile.** Users edit their display name, change their password (which needs the
  current one and signs out every other session of theirs) and sign out everywhere. A
  wrong current password counts towards the lockout like a failed sign-in.
- Endpoints: `POST /api/v1/auth/login`, `/auth/logout`, `/auth/logout-all`,
  `/auth/register`, `/auth/password/change`, `/auth/password/forgot`,
  `/auth/password/reset`; `GET /api/v1/auth/registration` (is
  sign-up open?); `GET|PATCH /api/v1/me`; `GET|PUT /api/v1/admin/settings` (admins
  only).
- **Forgotten passwords.** The sign-in page links to "Forgot your password?", which
  emails a reset link (`POST /api/v1/auth/password/forgot`; the answer is the same
  whether or not the email belongs to a user). The link is single-use and valid
  for 30 minutes; DAWAM stores only its SHA-256. Setting a new password with it
  (`POST /api/v1/auth/password/reset`) ends every session of that user. Links start
  with `DAWAM_PUBLIC_URL` (default `http://localhost:8000`), never with the
  request's host.
- **Invitations.** An admin invites someone by email from **Users → Invite user**
  (`POST /api/v1/admin/users/invite`); an email that already has an account gets `409
  email_taken`, and inviting the same email again replaces the pending invitation. The
  link is single-use, valid 7 days and bound to that email; DAWAM stores only its
  SHA-256. The invitee opens it, chooses a display name and password (same policy) and
  is signed in (`POST /api/v1/auth/invitations/lookup` and `/accept`, the token in the
  body), whether or not self-registration is open. Admins see pending invitations and
  revoke them (`GET /api/v1/admin/invitations`, `DELETE
  /api/v1/admin/invitations/{invitation_id}`). Without SMTP the link waits in **Links
  to share**.

## Email

An admin sets up SMTP in the web UI (**Email settings**, `GET|PUT|DELETE
/api/v1/admin/smtp`): host, port, `none` / `starttls` / `tls` (certificates are
always verified), optional username and password, and the From address. **Send test
email** (`POST /api/v1/admin/smtp/test`) sends one right away and shows the server's
error if it fails. The SMTP password is stored encrypted with `DAWAM_ENCRYPTION_KEY`
(AES-256-GCM) and never returned (the API says `has_password` instead); changing the
host, port or username needs it entered again, so it cannot be sent to another
server unseen. The SMTP host is whatever the admin enters, so DAWAM will connect to
any address it can reach, internal ones included (the test email shows the server's
error). That is accepted because only admins can set it; keep admin accounts few.

Forgot-password is throttled without telling anyone: at most one reset email per
account per `DAWAM_PASSWORD_RESET_COOLDOWN_MINUTES` (5), and at most
`DAWAM_PASSWORD_RESET_IP_MAX_REQUESTS` (10) per client address per
`DAWAM_PASSWORD_RESET_IP_WINDOW_MINUTES` (60). A throttled request gets the same
answer, sends nothing and stores nothing.

**Without SMTP** (air-gapped installs), or when sending fails, links DAWAM would
email are kept for admins instead: **Links to share** (`GET
/api/v1/admin/undelivered-links`) lists each with its recipient, so an admin can
copy it to them. A link leaves the list when it expires, when it stops working
(a used or voided reset link, an accepted or revoked invitation) or when an admin
removes it;
it is stored encrypted too.

## Workspaces

Any signed-in user can create a Workspace and becomes its owner. The list shows only
the Workspaces you are a member of, with your role (owner, editor or viewer); only
owners edit a Workspace's details. Anyone who is not a member, admins included, gets
404 for a Workspace, so its existence is not revealed. Edits carry the `version` the
client last saw, and a stale one gets `409 version_conflict`. List endpoints are
paged with `limit` and an opaque `cursor` (the previous page's `next_cursor`).

- Endpoints: `GET|POST /api/v1/workspaces`, `GET|PATCH /api/v1/workspaces/{workspace_id}`,
  `GET /api/v1/workspaces/{workspace_id}/progress` (stage progress: Source Analysis per
  Source System, KPIs, DW Modeling per Layer; every member sees the same).
- A Workspace opens as a folder tree (Systems; Data Warehouse with KPIs, Staging, Core and
  Mart, each with Model, Mappings and Evaluation; Lineage, Score, DDL). The selected folder
  is the `folder` URL parameter, so it can be linked and reloaded.
- Members: every member sees who belongs (`GET .../members`, not paged: a team's worth
  of members, by display name) and can leave (`POST
  .../leave`). Owners add an existing user by email with a role (`POST .../members`),
  change roles and remove members (`PATCH|DELETE .../members/{member_id}`; access ends at
  once), and transfer ownership (`POST .../transfer-ownership`: the other member becomes
  an owner, you an editor). An email with no account gets an invitation into the Workspace
  instead, which only an admin may send, or anyone while self-registration is open to that
  email (`403 invite_not_allowed` otherwise); accepting it joins the Workspace, unless its
  sender is no longer an owner there (`400 invalid_invitation`). A Workspace
  always keeps an owner: the last one cannot leave, be removed or be demoted (`409
  last_owner`), backed by a database trigger.

## Files and documents

Owners and editors upload documents (PDF, DOCX, XLSX, Markdown, text, and PNG, JPEG, GIF
or WebP images) to a Source System's file area, shown as its Documents folder; any member
lists and downloads them. A *document* is simply an uploaded file with extracted text
(pypdf, python-docx, openpyxl, or the raw text); a PDF with no text layer, such as a scan,
is marked "no text found". Uploads are limited to `DAWAM_UPLOAD_MAX_MB` (default 25; the
`edge` proxy allows up to 100). The type is sniffed from the content and must match the
extension (`415 unsupported_file_type` otherwise; SVG and HTML are never accepted), files
are stored under random keys (the uploaded name is only a label), and downloads are always
`Content-Disposition: attachment`. A file with the same name in an area is replaced.

- Endpoints: `GET|POST /api/v1/workspaces/{workspace_id}/systems/{system_id}/files` (the
  upload is `multipart/form-data` with a `file` part), `GET
  /api/v1/workspaces/{workspace_id}/files/{file_id}/download`.
- Storage: by default a directory, `DAWAM_STORAGE_PATH` (Compose mounts the `file-data`
  volume there; back it up with the database). For an S3-compatible bucket set
  `DAWAM_STORAGE_BACKEND=s3`, `DAWAM_S3_BUCKET` and optionally `DAWAM_S3_ENDPOINT_URL`,
  `DAWAM_S3_REGION`, `DAWAM_S3_ACCESS_KEY_ID` and `DAWAM_S3_SECRET_ACCESS_KEY`, and install
  DAWAM with the `s3` extra (`pip install "dawam[s3]"`).
