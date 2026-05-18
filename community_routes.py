"""
GTAVCAD Community Management Endpoints

Provides:
- Community creation/management
- Community joining
- Invite code system
- Member management
- Community selection
"""

import json
import secrets
import string
import logging
from datetime import datetime, timedelta
from flask import Blueprint, request, session, jsonify, g, current_app
from database import db
from models import (
    User, Config, Community, CommunityMember, CommunityInvite
)
from cad_access import evaluate_police_cad_access
from community_service import (
    get_current_community_id, get_user_communities,
    community_required, community_member_required,
    community_admin_required_scoped,
)
from security_service import require_auth

logger = logging.getLogger(__name__)
COMMUNITY_CONTEXT_CACHE = {}


def _ctx_cache_get(key):
    row = COMMUNITY_CONTEXT_CACHE.get(key)
    if not row:
        return None
    if row['expires_at'] < datetime.utcnow().timestamp():
        COMMUNITY_CONTEXT_CACHE.pop(key, None)
        return None
    return row['value']


def _ctx_cache_put(key, value, ttl_seconds=8):
    COMMUNITY_CONTEXT_CACHE[key] = {'value': value, 'expires_at': datetime.utcnow().timestamp() + max(int(ttl_seconds), 1)}

# Create blueprint
community_bp = Blueprint('communities', __name__, url_prefix='/api/communities')

DEFAULT_DEPARTMENTS = {
    'LSPD': 'Los Santos Police Department',
    'BCSO': "Blaine County Sheriff's Office",
    'SAST': 'San Andreas State Troopers',
    'SAFR': 'San Andreas Fire Rescue',
    'Dispatch': 'Communications / Dispatch',
}

DEFAULT_RANKS = ['Cadet', 'Officer', 'Corporal', 'Sergeant', 'Lieutenant', 'Captain', 'Chief']

DEFAULT_PENAL_CODES = {
    '1.01': 'Reckless Driving',
    '1.02': 'Speeding',
    '2.01': 'Assault',
    '2.02': 'Battery',
    '3.01': 'Theft',
    '3.02': 'Burglary',
}

DEFAULT_DISPATCH_CATEGORIES = ['Emergency', 'Non-Emergency', 'Traffic', 'Medical', 'Fire']



def can_access_police_cad(platform_role, community_role, user=None, membership=None):
    decision = evaluate_police_cad_access(
        user=user,
        role=community_role,
        membership=membership,
        session_values={
            'user_id': session.get('user_id'),
            'role': session.get('role'),
            'platform_role': platform_role or session.get('platform_role'),
            'email': session.get('email'),
            'is_platform_owner': session.get('is_platform_owner'),
            'community_id': getattr(membership, 'community_id', None) or get_current_community_id(),
            'selected_community_id': session.get('selected_community_id'),
        },
    )
    logger.debug(
        'Police CAD access decision route=%s user_id=%s community_id=%s role=%s normalized_role=%s platform_role=%s '
        'is_platform_owner=%s explicit_permission=%s final_can_access_police_cad=%s',
        request.path,
        decision.get('user_id'),
        decision.get('community_id'),
        decision.get('role'),
        decision.get('normalized_role'),
        decision.get('platform_role'),
        decision.get('is_platform_owner'),
        decision.get('explicit_permission'),
        decision.get('final_can_access_police_cad'),
    )
    return decision.get('final_can_access_police_cad') is True


def mask_invite_code(value):
    code = (value or '').strip()
    if len(code) <= 4:
        return '*' * len(code)
    return f"{code[:2]}***{code[-2:]}"


def generate_invite_code(length=8):
    """Generate a unique uppercase alphanumeric invite code."""
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(100):
        invite_code = ''.join(secrets.choice(alphabet) for _ in range(length))
        if not CommunityInvite.query.filter_by(invite_code=invite_code).first():
            return invite_code
    raise RuntimeError('Unable to generate a unique invite code')


def create_config_if_missing(key, community_id, value, description):
    """Create a tenant config row without overwriting existing configuration."""
    existing = Config.query.filter_by(key=key, community_id=community_id).first()
    if existing:
        return existing

    config = Config(
        key=key,
        community_id=community_id,
        value=json.dumps(value),
        description=description,
    )
    db.session.add(config)
    return config


def initialize_community_config(community):
    """Seed default tenant configuration so new communities never render empty UI."""
    branding = {
        'primary_color': community.primary_color,
        'secondary_color': community.secondary_color,
        'logo_url': community.logo_url,
    }
    defaults = {
        'server_name': (community.name, 'Community display name'),
        'cad_name': (community.cad_name, 'Community CAD name'),
        'branding_colors': (branding, 'Community branding colors and logo'),
        'departments': (DEFAULT_DEPARTMENTS, 'Available departments for this community'),
        'officer_ranks': (DEFAULT_RANKS, 'Available officer ranks for this community'),
        'penal_codes': (DEFAULT_PENAL_CODES, 'Starter penal code template for this community'),
        'call_types': (DEFAULT_DISPATCH_CATEGORIES, 'Dispatch call categories for this community'),
        'dispatch_categories': (DEFAULT_DISPATCH_CATEGORIES, 'Dispatch categories for this community'),
        'agency_names': (DEFAULT_DEPARTMENTS, 'Agency name mappings for this community'),
        'default_officers': ([
            {'id': '1L-01', 'name': 'Chief Unit', 'status': 'Available', 'department': 'LSPD'},
            {'id': '2L-12', 'name': 'Patrol Unit', 'status': 'Available', 'department': 'LSPD'},
            {'id': 'D-04', 'name': 'Dispatch', 'status': 'Active', 'department': 'Dispatch'},
        ], 'Starter officer/dispatch units for this community'),
    }

    for key, (value, description) in defaults.items():
        create_config_if_missing(key, community.community_id, value, description)


def is_persisted_platform_owner(user_id):
    """Return True only when the persisted user record is PlatformOwner."""
    user = User.query.get(user_id) if user_id else None
    return bool(user and (
        getattr(user, 'role', None) == 'PlatformOwner'
        or getattr(user, 'platform_role', None) == 'PlatformOwner'
    ))


def set_selected_community_session(community, membership=None):
    """Persist the active community and membership role in the current browser session."""
    session['selected_community_id'] = community.community_id
    session['selected_community_slug'] = community.slug
    if membership:
        session['current_role'] = membership.role
        session['current_department'] = membership.department
    session.modified = True


def get_active_or_create_invite(community, created_by=None, deactivate_existing=False):
    """Return an active globally unique 8-character invite for a community."""
    if deactivate_existing:
        CommunityInvite.query.filter_by(community_id=community.community_id, active=True).update({'active': False})
    invite = CommunityInvite.query.filter_by(community_id=community.community_id, active=True).first()
    if invite and invite.is_valid():
        return invite
    owner_membership = CommunityMember.query.filter_by(
        community_id=community.community_id,
        role='Owner',
        status='Active',
    ).first()
    creator_id = created_by or community.owner_user_id or (owner_membership.user_id if owner_membership else None)
    if not creator_id:
        fallback_user = User.query.order_by(User.id.asc()).first()
        creator_id = fallback_user.id if fallback_user else None
    if not creator_id:
        raise RuntimeError('Unable to create invite without a creator user')
    invite = CommunityInvite(
        invite_code=generate_invite_code(8),
        community_id=community.community_id,
        role='Civilian',
        created_by=creator_id,
        max_uses=None,
        uses=0,
        active=True,
    )
    db.session.add(invite)
    return invite


# ----------------------------------------
# User Community Management
# ----------------------------------------

@community_bp.route('', methods=['GET'])
@require_auth
def list_user_communities():
    """
    GET /api/communities
    
    List all communities for the current user.
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401

    if is_persisted_platform_owner(user_id):
        communities = Community.query.order_by(Community.name.asc()).all()
        communities_data = [{
            'community': community.to_dict(),
            'membership': {
                'community_id': community.community_id,
                'user_id': user_id,
                'role': 'PlatformOwner',
                'status': 'Active',
            },
            'can_manage_community': True,
        } for community in communities]
    else:
        memberships = (
            CommunityMember.query
            .join(Community, Community.community_id == CommunityMember.community_id)
            .filter(
                CommunityMember.user_id == user_id,
                CommunityMember.status == 'Active',
                Community.status == 'Active',
            )
            .all()
        )

        communities_data = []
        for membership in memberships:
            role = membership.role
            communities_data.append({
                'community': membership.community.to_dict(),
                'membership': membership.to_dict(),
                'can_manage_community': role in {'PlatformOwner', 'CommunityOwner', 'CommunityAdmin', 'Owner', 'Admin'},
            })

    return jsonify({
        'success': True,
        'communities': communities_data,
        'count': len(communities_data),
        'current_community_id': get_current_community_id(),
    }), 200


@community_bp.route('/select', methods=['POST'])
@require_auth
def select_community():
    """
    POST /api/communities/select
    
    Select active community for the user session.
    
    Body:
    {
        "community_id": "example-community"
    }
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    community_id = data.get('community_id')

    if not community_id:
        return jsonify({'error': 'community_id required'}), 400

    # Verify user is a member
    membership = CommunityMember.query.filter_by(
        user_id=user_id,
        community_id=community_id,
        status='Active'
    ).first()

    if not membership:
        return jsonify({
            'error': f'You are not a member of community {community_id}'
        }), 403

    community = Community.query.filter_by(community_id=community_id).first()
    if community:
        set_selected_community_session(community, membership)


    return jsonify({
        'success': True,
        'message': f'Selected community: {community.name if community else community_id}',
        'community_id': community_id,
    }), 200


# ----------------------------------------
# Community Creation
# ----------------------------------------

@community_bp.route('', methods=['POST'])
@community_bp.route('/create', methods=['POST'])
@require_auth
def create_community():
    """
    POST /api/communities
    
    Create a new community.
    
    Body:
    {
        "name": "Metro RP",
        "slug": "metro-rp",
        "cad_name": "Metro CAD",
        "logo_url": "https://...",
        "primary_color": "#1a1a1a",
        "secondary_color": "#0066cc"
    }
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}

    # Validate input
    required_fields = ['name', 'slug', 'cad_name']
    for field in required_fields:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400

    name = data['name'].strip()
    slug = data['slug'].strip().lower()
    cad_name = data['cad_name'].strip()

    # Validate slug format
    if not slug or not all(c.isalnum() or c == '-' for c in slug):
        return jsonify({'error': 'Slug must be alphanumeric with hyphens only'}), 400

    # Check slug uniqueness
    existing = Community.query.filter_by(slug=slug).first()
    if existing:
        return jsonify({'error': f'Slug {slug} already taken'}), 409

    # Create community
    try:
        community_id = f'community_{secrets.token_hex(6)}'

        community = Community(
            community_id=community_id,
            name=name,
            slug=slug,
            cad_name=cad_name,
            owner_user_id=user_id,
            logo_url=data.get('logo_url'),
            primary_color=data.get('primary_color', '#1a1a1a'),
            secondary_color=data.get('secondary_color', '#0066cc'),
            status='Active',
        )
        db.session.add(community)

        # Add creator as Owner. Owner is treated as an admin-capable role by community guards.
        membership = CommunityMember(
            community_id=community_id,
            user_id=user_id,
            role='Owner',
            department='Administration',
            status='Active',
        )
        db.session.add(membership)

        invite = get_active_or_create_invite(community, created_by=user_id)

        initialize_community_config(community)

        session.clear()
        session['user_id'] = user_id
        session['authenticated'] = True
        session['selected_community_id'] = community.community_id
        session['selected_community_slug'] = community.slug
        session['current_role'] = 'Owner'
        session['current_department'] = 'Administration'
        session.modified = True

        db.session.commit()
        logger.info(f'Community session set: {community.slug}')
        logger.info(f'Selected community ID: {session.get("selected_community_id")}')

        logger.info(f'✅ Created community {slug} (ID: {community_id}) by user {user_id}')

        redirect_url = f'/c/{community.slug}/'
        return jsonify({
            'success': True,
            'message': 'Community created successfully',
            'community': community.to_dict(),
            'membership': {
                'role': 'Owner',
                'department': 'Administration',
            },
            'invite': {
                **invite.to_dict(),
                'code': invite.invite_code,
            },
            'redirect_url': redirect_url,
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error creating community')
        return jsonify({'success': False, 'error': 'Unable to create community right now.'}), 500


# ----------------------------------------
# Public Community Lookup
# ----------------------------------------

@community_bp.route('/public/<slug>', methods=['GET'])
def get_public_community_by_slug(slug):
    """Public tenant branding lookup for /c/<slug> rendering."""
    community = Community.query.filter_by(slug=slug, status='Active').first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404

    return jsonify({
        'success': True,
        'community': community.to_dict(),
        'platform': {
            'name': 'GTAVCAD',
            'domain': 'gtavcad.app',
        },
    }), 200



@community_bp.route('/my', methods=['GET'])
@require_auth
def get_my_communities():
    """GET /api/communities/my - alias for the current user's community list."""
    return list_user_communities()


@community_bp.route('/invite', methods=['POST'])
@require_auth
def create_invite_for_selected_community():
    """POST /api/communities/invite - create an invite for a selected or supplied community."""
    user_id = session.get('user_id')
    data = request.get_json() or {}
    community_id = data.get('community_id') or get_current_community_id()

    if not community_id:
        return jsonify({'success': False, 'error': 'community_id required'}), 400

    membership = CommunityMember.query.filter_by(
        user_id=user_id,
        community_id=community_id,
        status='Active'
    ).first()
    authorized_invite_roles = {'Owner', 'Admin', 'CommunityOwner', 'CommunityAdmin'}
    is_owner = is_persisted_platform_owner(user_id)
    if not is_owner and (not membership or membership.role not in authorized_invite_roles):
        return jsonify({'success': False, 'error': 'Community admin or owner access required'}), 403

    role = data.get('role', 'Civilian')
    department = data.get('department')
    max_uses = data.get('max_uses')
    expires_in_days = data.get('expires_in_days')

    try:
        community = Community.query.filter_by(community_id=community_id).first()
        if not community:
            return jsonify({'success': False, 'error': 'Community not found'}), 404
        if data.get('regenerate'):
            invite = get_active_or_create_invite(community, created_by=user_id, deactivate_existing=True)
            db.session.commit()
            return jsonify({
                'success': True,
                'message': 'Invite code regenerated',
                'invite': invite.to_dict(),
            }), 201

        invite_code = generate_invite_code(8)
        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=int(expires_in_days))

        invite = CommunityInvite(
            invite_code=invite_code,
            community_id=community_id,
            role=role,
            department=department,
            created_by=user_id,
            expires_at=expires_at,
            max_uses=max_uses,
            uses=0,
            active=True,
        )
        db.session.add(invite)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Invite code created',
            'invite': invite.to_dict(),
        }), 201
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error creating invite')
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


@community_bp.route('/accept-invite', methods=['POST'])
@require_auth
def accept_invite_alias():
    """POST /api/communities/accept-invite - alias for joining by invite code."""
    return join_with_invite()


# ----------------------------------------
# Community Details
# ----------------------------------------

@community_bp.route('/context', methods=['GET'])
@community_bp.route('/current', methods=['GET'])
def get_current_community_context():
    """Backend-authoritative tenant context for every community page."""
    community_id = get_current_community_id()
    community = Community.query.filter_by(community_id=community_id, status='Active').first()
    if not community:
        return jsonify({'success': False, 'error': 'Community context not found'}), 404

    user_id = session.get('user_id')
    cache_key = (community.community_id, user_id, bool(session.get('impersonating_community_id')))
    cached = _ctx_cache_get(cache_key)
    if cached:
        return jsonify(cached), 200
    membership = None
    if user_id:
        membership = CommunityMember.query.filter_by(
            user_id=user_id,
            community_id=community.community_id,
            status='Active',
        ).first()
        # Avoid rewriting session on read-only context fetches.

    community_role = membership.role if membership else None
    is_owner = is_persisted_platform_owner(user_id)
    can_manage = bool(
        is_owner
        or (membership and community_role in {'CommunityOwner', 'CommunityAdmin', 'Owner', 'Admin'})
        or (user_id and community.owner_user_id == user_id)
    )

    config_rows = Config.query.filter(
        Config.community_id == community.community_id,
        Config.key.in_(['accent_color', 'background_color', 'text_color'])
    ).all()
    config_map = {row.key: row.value for row in config_rows}
    def config_value(key, default):
        raw = config_map.get(key)
        if raw in (None, ''):
            return default
        try:
            return json.loads(raw)
        except Exception:
            return raw

    payload = {
        'success': True,
        'platform': {'name': 'GTAVCAD', 'domain': 'gtavcad.app'},
        'community': {
            'community_id': community.community_id,
            'name': community.name,
            'slug': community.slug,
            'cad_name': community.cad_name,
            'primary_color': community.primary_color,
            'secondary_color': community.secondary_color,
            'accent_color': config_value('accent_color', '#ff2d2d'),
            'background_color': config_value('background_color', '#0b0b0d'),
            'text_color': config_value('text_color', '#f6f6f6'),
            'logo_url': community.logo_url,
        },
        'membership': {
            'role': membership.role,
            'department': membership.department,
        } if membership else None,
        'user': {
            'id': user_id,
            'username': session.get('username'),
            'role': session.get('role', 'Civilian'),
            'platform_role': session.get('platform_role'),
            'community_role': membership.role if membership else None,
            'is_platform_owner': is_owner,
            'impersonation_active': bool(session.get('impersonating_community_id')),
            'can_manage_community': can_manage,
            'is_community_admin': can_manage,
            'can_access_police_cad': can_access_police_cad(session.get('platform_role'), membership.role if membership else None, user=User.query.get(user_id) if user_id else None, membership=membership),
        } if user_id else None,
    }
    _ctx_cache_put(cache_key, payload, ttl_seconds=8)
    return jsonify(payload), 200


@community_bp.route('/<community_id>', methods=['GET'])
@require_auth
def get_community(community_id):
    """
    GET /api/communities/<community_id>
    
    Get community details.
    """
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        community = Community.query.filter_by(slug=community_id, status='Active').first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    community_id = community.community_id

    # Check membership
    user_id = session.get('user_id')
    membership = CommunityMember.query.filter_by(
        user_id=user_id,
        community_id=community_id
    ).first()

    is_member = membership is not None
    role = membership.role if membership else None

    return jsonify({
        'success': True,
        'community': community.to_dict(),
        'is_member': is_member,
        'user_role': role,
    }), 200


# ----------------------------------------
# Invite System
# ----------------------------------------

@community_bp.route('/join', methods=['POST'])
@require_auth
def join_with_invite():
    """
    POST /api/communities/join
    
    Join a community via invite code.
    
    Body:
    {
        "invite_code": "abc123def456"
    }
    """
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    invite_code = data.get('invite_code', '').strip()

    if not invite_code:
        return jsonify({'error': 'invite_code required'}), 400

    # Find and validate invite without trusting any selected tenant context.
    invite = CommunityInvite.query.filter(CommunityInvite.invite_code.ilike(invite_code)).with_for_update().first()
    if not invite or not invite.is_valid() or invite.role == 'PlatformOwner':
        return jsonify({'success': False, 'error': 'Invite is invalid, expired, revoked, or no longer available'}), 400

    community_id = invite.community_id
    community = Community.query.filter_by(community_id=community_id).first()
    if not community or (community.status or '').lower() != 'active':
        return jsonify({'success': False, 'error': 'Invite is invalid, expired, revoked, or no longer available'}), 400

    # Check if user already a member. Do not duplicate or silently alter role.
    existing = CommunityMember.query.filter_by(
        user_id=user_id,
        community_id=community_id
    ).first()

    if existing:
        existing.status = 'Active'
        set_selected_community_session(community, existing)
        session['community_id'] = community.community_id
        session['community_slug'] = community.slug
        session['community_role'] = existing.role
        session.modified = True
        db.session.commit()
        return jsonify({
            'success': True,
            'message': 'Already a member of this community',
            'community': community.to_dict(),
            'membership': existing.to_dict(),
            'redirect': f'/c/{community.slug}/',
        }), 200

    # Create membership
    try:
        membership = CommunityMember(
            community_id=community_id,
            user_id=user_id,
            role=invite.role,
            department=invite.department,
            status='Active',
        )
        db.session.add(membership)

        # Increment invite uses
        invite.uses += 1
        if invite.max_uses and invite.uses >= invite.max_uses:
            invite.active = False
        db.session.commit()

        set_selected_community_session(community, membership)
        session['community_id'] = community.community_id
        session['community_slug'] = community.slug
        session['community_role'] = membership.role
        session.modified = True

        logger.info(
            f'✅ User {user_id} joined community {community_id} via invite'
        )

        return jsonify({
            'success': True,
            'message': 'Joined community successfully',
            'community': community.to_dict(),
            'membership': membership.to_dict(),
            'role': membership.role,
            'redirect': f'/c/{community.slug}/',
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error joining community via invite')
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


# ----------------------------------------
# Community Admin Functions
# ----------------------------------------

@community_bp.route('/<community_id>/members', methods=['GET'])
@require_auth
@community_member_required
def list_community_members(community_id):
    """
    GET /api/communities/<community_id>/members
    
    List members of a community.
    """
    members = CommunityMember.query.filter_by(
        community_id=community_id,
        status='Active'
    ).all()

    return jsonify({
        'success': True,
        'members': [m.to_dict() for m in members],
        'count': len(members),
    }), 200


@community_bp.route('/<community_id>/invites', methods=['GET'])
@require_auth
@community_admin_required_scoped
def list_community_invites(community_id):
    """
    GET /api/communities/<community_id>/invites
    
    List active invite codes for community (admin only).
    """
    invites = CommunityInvite.query.filter_by(community_id=community_id).all()

    return jsonify({
        'success': True,
        'invites': [i.to_dict() for i in invites],
        'count': len(invites),
    }), 200


@community_bp.route('/<community_id>/invites', methods=['POST'])
@require_auth
@community_admin_required_scoped
def create_invite_code(community_id):
    """
    POST /api/communities/<community_id>/invites
    
    Create a new invite code (admin only).
    
    Body:
    {
        "role": "Civilian",
        "department": "LSPD",
        "max_uses": 5,
        "expires_in_days": 7
    }
    """
    user_id = session.get('user_id')
    data = request.get_json() or {}

    role = data.get('role', 'Civilian')
    department = data.get('department')
    max_uses = data.get('max_uses')
    expires_in_days = data.get('expires_in_days')

    try:
        invite_code = generate_invite_code()

        expires_at = None
        if expires_in_days:
            expires_at = datetime.utcnow() + timedelta(days=expires_in_days)

        invite = CommunityInvite(
            invite_code=invite_code,
            community_id=community_id,
            role=role,
            department=department,
            created_by=user_id,
            expires_at=expires_at,
            max_uses=max_uses,
            uses=0,
            active=True,
        )
        db.session.add(invite)
        db.session.commit()

        logger.info(f'✅ Created invite code for community {community_id}')

        return jsonify({
            'success': True,
            'message': 'Invite code created',
            'invite': invite.to_dict(),
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error creating community invite code')
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


@community_bp.route('/<community_id>/invites/<invite_code>', methods=['DELETE'])
@require_auth
@community_admin_required_scoped
def revoke_invite_code(community_id, invite_code):
    """
    DELETE /api/communities/<community_id>/invites/<invite_code>
    
    Revoke an invite code (admin only).
    """
    invite = CommunityInvite.query.filter_by(
        community_id=community_id,
        invite_code=invite_code
    ).first()

    if not invite:
        return jsonify({'error': 'Invite not found'}), 404

    try:
        invite.active = False
        db.session.commit()

        logger.info('✅ Revoked invite code %s', mask_invite_code(invite_code))

        return jsonify({
            'success': True,
            'message': 'Invite code revoked',
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error revoking invite code')
        return jsonify({'success': False, 'error': 'Internal server error'}), 500


# ----------------------------------------
# Export
# ----------------------------------------

def register_community_routes(app):
    """Register community blueprint with Flask app."""
    app.register_blueprint(community_bp)
    logger.info('✓ Community management routes registered')
