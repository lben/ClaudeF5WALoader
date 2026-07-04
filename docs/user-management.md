# User management

Two independent layers, one shared implementation (argon2 hashing everywhere;
plaintext passwords are never stored):

1. **WALoader platform users** — log into WALoader itself. Admins manage them
   (Admin → WALoader users, or `python -m waloader.tools.users`).
2. **App users** — per-app accounts for deployed child apps, managed by the
   app's owner (App users page). Enabled per app via the **Users Management
   Support** toggle (gear dialog, create screen, or the App users page).

## Platform users

- First run: WALoader shows a one-time setup screen to create the first admin
  (or use `uv run python -m waloader.tools.users create-admin <name>` which
  prompts for the password securely).
- Admins can create users/admins, deactivate/reactivate, and reset passwords.
  Everyone can change their own password on the Account page.
- The **owner's email** receives crash notifications for their apps — keep it
  filled in.

## App users (the reusable module)

When *Users Management Support* is enabled for an app, its visitors must log
in before using it; the login form is the default screen for unauthenticated
visitors. When disabled, no app-level login is required.

Owner capabilities per user: create, edit email + free-text **observations**
(who requested access, approvals…), deactivate/reactivate, delete (with
confirmation; removes their attachment files too), set password, and manage
**attachments** — files such as screenshots justifying access, stored under
`data/apps/<slug>/user_files/<user-id>/` with notes.

## SDK for child apps

WALoader installs `argon2-cffi` into every child venv automatically; the SDK
arrives via PYTHONPATH injection. Generated apps use:

```python
from waloader_sdk.auth import require_login, logout_button, change_password_form

user = require_login()      # None when the app has login disabled; otherwise
                            # renders the login form and stops until authenticated
st.sidebar.write(f"Hello {user['username'] if user else 'guest'}")
logout_button()             # renders only when someone is logged in
change_password_form()      # self-service password change
```

Login state lives in the app's `st.session_state` — a full browser refresh
requires logging in again (Streamlit has no official cookie API; accepted
limitation).

## Crash notifications

All email flows through **one function**: `send_mail(subject=, sender=,
recipients=, html_body=)` in `src/waloader/notifications/mailer.py`. The
shipped implementation is a **stub that only logs** — at work, replace that
one function body with the corporate mailer; nothing else changes.

An email is sent only when ALL hold: crash emails enabled
(`notifications.crash_emails_enabled`), the app had passed its initial health
checks, it survived `health.grace_period_seconds` (default 180 s), a later
health check found it dead/repeatedly unhealthy (state `running → failed`),
and no email was already sent for that failure event. Never emailed:
dependency/test/initial-deployment failures, user stop/restart, update
failures, or apps found dead during a WALoader restart (reconciliation marks
them stopped). Recipients: the app owner's email plus
`notifications.admin_cc`. A successful redeploy resets the dedupe.
