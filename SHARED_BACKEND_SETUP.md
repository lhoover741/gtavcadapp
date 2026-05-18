# Shared Production Backend Setup

This repository now treats the Flask/Socket.IO app as the shared backend for both `gtavcad.app` and `gtavcad.com`. Both frontends should call the same `API_BASE_URL`, use the same PostgreSQL `DATABASE_URL`, and connect to the same Socket.IO endpoint so logins, CAD records, dispatch calls, unit statuses, BOLOs, warrants, reports, and notifications are shared.

## What is in this repository

- **Frontend:** Static HTML/CSS/JavaScript files served from the repository root, with main browser logic in `assets/js/main.js` and notification UI/socket handling in `assets/js/push-notifications.js`.
- **Backend:** `server.py`, a Flask application with REST endpoints, session authentication, Socket.IO realtime events, and community-aware authorization.
- **Database:** SQLAlchemy models in `models.py`, configured by `database.py` using `DATABASE_URL`.
- **Authentication:** Secure Flask sessions remain enabled, and login/register responses also return a signed Bearer API token for shared app clients that cannot rely on one cookie across unrelated domains.
- **Notifications:** Database-backed notifications are exposed at `/api/notifications`; realtime delivery uses Socket.IO rooms; mobile push registration is prepared through `/api/push/register` and `/api/push/unregister`.

## Required environment variables

Copy `.env.example` to your host configuration and fill in real values there. Never commit real secrets.

### Runtime and sessions

- `SECRET_KEY`: Required. Strong random secret used for Flask sessions and signed API tokens. Use the same value on all backend instances.
- `FLASK_ENV`: Set to `production` in production.
- `PORT`: Port the Flask/Socket.IO server listens on.
- `SESSION_DAYS`: Session lifetime in days.
- `SESSION_COOKIE_SECURE`: Use `true` in production so cookies are HTTPS-only.
- `SESSION_COOKIE_DOMAIN`: Usually blank. Cookies cannot be shared between `gtavcad.app` and `gtavcad.com`; use the shared API token or host both frontends against one API origin.
- `SESSION_COOKIE_SAMESITE`: Usually `Lax`. If you intentionally embed cross-site credentialed requests, configure this deliberately with HTTPS.

### Shared database

- `DATABASE_URL`: Required. The single production PostgreSQL connection string used by both domains.
- `SQLALCHEMY_POOL_SIZE`, `SQLALCHEMY_MAX_OVERFLOW`, `SQLALCHEMY_POOL_RECYCLE`: Production connection pool tuning.

### Shared API/origins

- `API_BASE_URL`: Public URL for the backend, such as `https://api.gtavcad.com`.
- `PUBLIC_BASE_URL`: Primary public website URL.
- `PLATFORM_DOMAIN`: Public platform domain displayed by the app.
- `WEB_ALLOWED_ORIGINS` / `CORS_ALLOWED_ORIGINS`: Comma-separated allowed browser origins. Include `https://gtavcad.app`, `https://www.gtavcad.app`, `https://gtavcad.com`, and `https://www.gtavcad.com`.
- `SOCKETIO_ALLOWED_ORIGINS`: Comma-separated Socket.IO origins for the same frontend domains.

### Bearer API tokens

- `JWT_ISSUER`: Expected token issuer.
- `JWT_AUDIENCE`: Expected token audience.
- `JWT_MAX_AGE_SECONDS`: Signed token lifetime.
- `JWT_SALT`: Stable secret salt for token signing. Use the same value on every backend instance.

### Platform owner bootstrap

- `PLATFORM_OWNER_EMAIL`, `PLATFORM_OWNER_USERNAME`: First owner account identity.
- `PLATFORM_OWNER_PASSWORD` or `PLATFORM_OWNER_INITIAL_PASSWORD`: Set only when bootstrapping/rotating; do not leave permanent plaintext credentials in hosting dashboards longer than needed.
- `FORCE_ADMIN_PASSWORD_RESET`: Use only for intentional resets.

### Email and push

- `SMTP_HOST`, `SMTP_PORT`, `SMTP_EMAIL`, `SMTP_PASSWORD`, `NOTIFY_EMAIL`: Existing email notification settings.
- `PUSH_PROVIDER`: Defaults to `fcm`.
- `FCM_SERVER_KEY`, `FCM_SERVICE_ACCOUNT_JSON`, `FCM_PROJECT_ID`, `FCM_WEB_VAPID_PUBLIC_KEY`: Firebase Cloud Messaging values. Leave blank until you create real Firebase credentials.

## Shared endpoints for frontends

- `GET /api/shared/config`: Public backend metadata, allowed origins, auth modes, and push provider status.
- `POST /api/auth/login`: Existing login; now also returns `api_token`, `token_type`, and `expires_in`.
- `POST /api/auth/token`: Refresh a signed Bearer token from an authenticated session.
- `GET /api/users`: Community members/users for authorized CAD/admin roles.
- `GET /api/departments`: Active/configured community departments.
- `GET /api/calls`: Shared alias for dispatch calls.
- `GET /api/vehicles`: Shared vehicle records.
- `GET /api/warrants`: Shared warrants.
- `GET /api/units`: Shared unit/officer sessions.
- `GET /api/reports`: Shared incident/arrest/citation/use-of-force report bundle.
- Existing endpoints remain available for `/api/cad`, `/api/bolos`, `/api/dispatch/calls`, `/api/notifications`, and other modules.

## Realtime updates

Use Socket.IO against the same backend origin configured by `API_BASE_URL`. Browser clients can connect with credentials for session auth, or pass the login `api_token` in the Socket.IO `auth` payload as `{ token: 'Bearer <api_token>' }`. The server joins authenticated users to community, role, and user rooms and emits updates for dispatch calls, unit changes, panic alerts, BOLOs, notifications, and presence.

## Mobile push preparation

Register device/browser tokens with:

```http
POST /api/push/register
Authorization: Bearer <api_token>
Content-Type: application/json

{
  "provider": "fcm",
  "token": "firebase-device-token",
  "platform": "web",
  "device_name": "Chrome on iPhone"
}
```

The backend stores tokens but does not include real Firebase credentials. Configure FCM environment variables before enabling actual send workers.

## Deploy/run commands

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Run database migrations if your deployment uses Alembic:

```bash
flask db upgrade
```

Start the shared backend locally:

```bash
SECRET_KEY=dev-only-change-me DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/gtavcad python server.py
```

Production Procfile command:

```bash
gunicorn server:app --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT
```

## Frontend connection checklist

1. Set both `gtavcad.app` and `gtavcad.com` to use the same `API_BASE_URL`.
2. Add both domains to `WEB_ALLOWED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, and `SOCKETIO_ALLOWED_ORIGINS`.
3. Use the same `SECRET_KEY`, `JWT_SALT`, and `DATABASE_URL` on every backend instance.
4. Do not put database URLs, server keys, or private credentials into frontend JavaScript.
5. Use `/api/shared/config` at startup if a frontend needs to discover backend capabilities.
