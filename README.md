# GTAVCAD

GTAVCAD is a multi-community GTA V RP/CAD platform for hosting multiple isolated roleplay communities on one shared deployment.

## Branding model

- **GTAVCAD** is the platform, global domain, navbar, login, onboarding, and community host.
- **NThaCityRP** is only the default migrated tenant/community.
- New users see **Welcome to GTAVCAD** with options to **Create a Community** or **Join Existing Community**.

## Routes

Global routes:

- `/`
- `/login`
- `/register`
- `/communities`
- `/create-community`

Tenant routes:

- `/c/nthacityrp`
- `/c/nthacityrp/cad`
- `/c/nthacityrp/police`
- `/c/nthacityrp/dmv`

## Configuration defaults

Global platform config:

- `platform_name = GTAVCAD`
- `platform_domain = gtavcad.app`

Default migrated tenant config:

- `community_name = NThaCityRP`
- `community_slug = nthacityrp`
- `cad_name = NThaCityRP CAD`

## Development checks

Run Python syntax checks with:

```bash
python3 -m py_compile server.py community_service.py community_routes.py platform_config.py bootstrap_multi_tenant.py tenant_schema.py
```
