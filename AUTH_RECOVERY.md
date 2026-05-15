# PlatformOwner Authentication Recovery

## Required Railway environment variables

- `PLATFORM_OWNER_EMAIL`
- `PLATFORM_OWNER_RECOVERY_TOKEN`

## Optional environment variables

- `PLATFORM_OWNER_INITIAL_PASSWORD`
- `FORCE_ADMIN_PASSWORD_RESET` (default `false`)
- `PLATFORM_OWNER_USERNAME`

## Recovery endpoint

`POST /api/platform-owner/recovery/reset-password`

Body:

```json
{
  "email": "admin@govdirect.org",
  "token": "<PLATFORM_OWNER_RECOVERY_TOKEN>",
  "new_password": "NewStrongPassword",
  "confirm_password": "NewStrongPassword"
}
```

Success response:

```json
{
  "success": true,
  "message": "PlatformOwner password reset successfully"
}
```

## Recovery steps

1. Set `PLATFORM_OWNER_RECOVERY_TOKEN` in Railway.
2. Call `/api/platform-owner/recovery/reset-password` with matching token.
3. Confirm response success and log in with the new password.
4. Rotate or remove `PLATFORM_OWNER_RECOVERY_TOKEN` after recovery.

## CLI recovery

Use the built-in CLI (same auth hashing flow):

```bash
python manage_platform_owner.py reset-password --email admin@govdirect.org
```

Or pass explicit password:

```bash
python manage_platform_owner.py reset-password --email admin@govdirect.org --password "NewPasswordHere"
```

Add `--show-hash` only when explicitly needed.
