# Security Regression Checklist

- Forgot-password response in production does not return reset token and does not reveal account existence.
- PlatformOwner access is based on persisted `role`/`platform_role` only; no runtime auto-promotion.
- Password policy enforced for reset/change/register/admin reset flows (min 10, upper/lower/number/special).
- Password hash values/prefixes are not logged.
- Invite-code audit logging stores only `invite_id` and masked invite code.
- Legacy `/api/admin/*` auth routes are disabled in production (and unless explicitly allowed in non-production).
