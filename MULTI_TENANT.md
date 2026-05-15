# GTAVCAD Multi-Tenant Architecture

GTAVCAD is the platform identity and public host for multiple isolated GTA V RP/CAD communities.

## Platform identity

- `platform_name = GTAVCAD`
- `platform_domain = gtavcad.app`
- Public/global pages use GTAVCAD branding.
- Global onboarding says **Welcome to GTAVCAD** and offers **Create a Community** or **Join Existing Community**.
- Global routes are `/`, `/login`, `/register`, `/communities`, and `/create-community`.

## Tenant/community identity

NThaCityRP is **not** the platform. NThaCityRP is the default migrated tenant/community hosted on GTAVCAD:

- `community_name = NThaCityRP`
- `community_slug = nthacityrp`
- `cad_name = NThaCityRP CAD`

Other tenants can be Metro RP, DOJ RP, Blaine County RP, or any other RP community.

## Route model

Global platform routes:

- `/`
- `/login`
- `/register`
- `/communities`
- `/create-community`

Community routes:

- `/c/nthacityrp`
- `/c/nthacityrp/cad`
- `/c/nthacityrp/police`
- `/c/nthacityrp/dmv`

Legacy single-community pages such as `police.html`, `rules.html`, and `join.html` redirect into `/c/nthacityrp/*` so NThaCityRP branding only appears in tenant context.

## Data isolation rules

Every CAD/community-owned table uses `community_id`. Query helpers must scope data to the selected or routed community so civilians, warrants, arrests, DMV records, businesses, config, applications, complaints, dispatch calls, officer sessions, and audit records never leak across tenants.

Users are global platform accounts and can belong to multiple communities through `community_members`. Roles and departments are community-scoped.

## Community creation rules

New communities are created through `POST /api/communities` with their own:

- community name
- slug
- CAD name
- logo
- primary/secondary colors
- owner membership

New tenants do not inherit NThaCityRP branding. Community-specific branding can override colors, logo, CAD name, departments, rules, and server name inside `/c/<slug>/*`.
