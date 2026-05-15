"""Canonical Police CAD access policy helpers."""

import logging

logger = logging.getLogger(__name__)

CAD_ACCESS_ROLES = {
    "PlatformOwner",
    "CommunityOwner",
    "CommunityAdmin",
    "Owner",
    "Admin",
    "Police",
    "Officer",
    "LEO",
    "Dispatch",
    "Dispatcher",
    "EMS",
    "DOJ",
    "Staff",
}

_ROLE_NORMALIZATION = {
    "platformowner": "PlatformOwner",
    "communityowner": "CommunityOwner",
    "communityadmin": "CommunityAdmin",
    "owner": "Owner",
    "admin": "Admin",
    "police": "Police",
    "officer": "Officer",
    "leo": "LEO",
    "dispatch": "Dispatch",
    "dispatcher": "Dispatcher",
    "ems": "EMS",
    "doj": "DOJ",
    "staff": "Staff",
    "civilian": "Civilian",
    "member": "Member",
    "businessowner": "BusinessOwner",
}

EXPLICIT_CAD_PERMISSION_FIELDS = (
    "can_access_police_cad",
    "police_cad_access",
    "cad_access",
    "has_cad_access",
)


def normalize_community_role(role):
    """Normalize legacy/community role names before access checks."""
    value = str(role or "").strip()
    return _ROLE_NORMALIZATION.get(value.lower(), value)


def role_allows_police_cad(role):
    """Return True if the supplied role is in the canonical CAD allowlist."""
    normalized = normalize_community_role(role)
    return normalized in CAD_ACCESS_ROLES


def _read_attr(subject, field_name, default=None):
    if not subject:
        return default
    if isinstance(subject, dict):
        return subject.get(field_name, default)
    try:
        value = getattr(subject, field_name)
    except Exception:
        return default
    return default if value is None else value


def _truthy_permission(value):
    if value is True:
        return True
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    return False


def get_explicit_police_cad_permission(*subjects):
    """Honor optional explicit CAD permission fields when present on known objects."""
    for subject in subjects:
        for field_name in EXPLICIT_CAD_PERMISSION_FIELDS:
            if _truthy_permission(_read_attr(subject, field_name, None)):
                return True
    return False


def platform_owner_override(user=None, session_values=None):
    """Return True only for persisted/session PlatformOwner roles."""
    session_values = session_values or {}
    user_role = normalize_community_role(_read_attr(user, "role", session_values.get("role")))
    user_platform_role = normalize_community_role(_read_attr(user, "platform_role", session_values.get("platform_role")))

    is_owner_role = user_role == "PlatformOwner" or user_platform_role == "PlatformOwner"
    if session_values.get("is_platform_owner") is True and not is_owner_role:
        logger.warning("Ignoring stale session PlatformOwner flag without persisted PlatformOwner role")
    return is_owner_role


def evaluate_police_cad_access(user=None, role=None, membership=None, session_values=None):
    """Return a consistent, inspectable Police CAD access decision."""
    session_values = session_values or {}
    effective_role = role if role is not None else _read_attr(membership, "role", session_values.get("role"))
    normalized_role = normalize_community_role(effective_role)
    platform_role = _read_attr(user, "platform_role", session_values.get("platform_role"))
    is_owner = platform_owner_override(user=user, session_values=session_values)
    explicit_permission = get_explicit_police_cad_permission(user, membership)
    role_allowed = role_allows_police_cad(normalized_role)
    explicit_permission_effective = bool(explicit_permission and role_allowed)
    final = bool(is_owner or role_allowed or explicit_permission_effective)
    return {
        "user_id": _read_attr(user, "id", session_values.get("user_id")),
        "community_id": _read_attr(membership, "community_id", session_values.get("community_id") or session_values.get("selected_community_id")),
        "role": effective_role,
        "normalized_role": normalized_role,
        "platform_role": platform_role,
        "is_platform_owner": bool(is_owner),
        "explicit_permission": bool(explicit_permission),
        "explicit_permission_effective": bool(explicit_permission_effective),
        "role_allowed": bool(role_allowed),
        "final_can_access_police_cad": final,
    }
