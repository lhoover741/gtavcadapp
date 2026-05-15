import logging
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask import session, jsonify, request

logger = logging.getLogger(__name__)

ROLES = {
    'Owner': ['read', 'write', 'delete', 'admin', 'owner'],
    'Admin': ['read', 'write', 'delete', 'admin'],
    'Police': ['read', 'write', 'police'],
    'EMS': ['read', 'write', 'police', 'ems'],
    'DOJ': ['read', 'write', 'police', 'judge'],
    'Staff': ['read', 'write', 'police', 'staff'],
    'LEO': ['read', 'write', 'police'],
    'Dispatch': ['read', 'write', 'dispatch'],
    'Judge': ['read', 'write', 'judge'],
    'DMV': ['read', 'write', 'dmv'],
    'Civilian': ['read'],
    'BusinessOwner': ['read', 'write', 'business'],
}

def validate_password_policy(password):
    """Validate baseline password complexity requirements."""
    if not isinstance(password, str):
        return False
    if len(password) < 10:
        return False
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(not c.isalnum() for c in password)
    return has_upper and has_lower and has_digit and has_special

def hash_password(password):
    """Hash a password using Werkzeug."""
    return generate_password_hash(password, method='pbkdf2:sha256')

def verify_password(password_hash, password):
    """Verify a password against its hash with defensive legacy handling."""
    if not password_hash or not password:
        logger.info("Password verification skipped: missing hash or password")
        return False

    normalized_hash = password_hash.strip() if isinstance(password_hash, str) else password_hash
    hash_prefix = ''
    if isinstance(normalized_hash, str):
        hash_prefix = normalized_hash.split('$', 1)[0]

    verify_function = 'werkzeug.check_password_hash'
    try:
        result = check_password_hash(normalized_hash, password)
        logger.info(
            "Password verify diagnostics: hash_present=%s function=%s result=%s",
            bool(normalized_hash),
            verify_function,
            result,
        )
        return result
    except Exception as exc:
        logger.warning(
            "Password verification failure: hash_present=%s function=%s result=False error=%s",
            bool(normalized_hash),
            verify_function,
            exc,
        )
        return False

def require_role(*roles):
    """Decorator to require specific roles."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get('role', 'Civilian')
            if user_role not in roles:
                return jsonify({'success': False, 'error': 'Insufficient permissions'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def require_auth(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator to require admin role."""
    return require_role('Admin', 'Owner')(f)

def police_required(f):
    """Decorator to require police or admin role."""
    return require_role('Police', 'EMS', 'Dispatch', 'DOJ', 'Staff', 'LEO', 'Admin', 'Owner')(f)

def dispatch_required(f):
    """Decorator to require dispatch or admin role."""
    return require_role('Dispatch', 'Police', 'EMS', 'DOJ', 'Staff', 'LEO', 'Admin', 'Owner')(f)

def judge_required(f):
    """Decorator to require judge or admin role."""
    return require_role('Judge', 'DOJ', 'Admin', 'Owner')(f)

def dmv_required(f):
    """Decorator to require DMV or admin role."""
    return require_role('DMV', 'Admin')(f)

def authenticated_required(f):
    """Decorator to require any authenticated user."""
    return require_auth(f)

def get_current_user():
    """Get current user info from session."""
    from flask import session, request
    return {
        'user_id': session.get('user_id'),
        'role': session.get('role', 'Civilian'),
        'ip': request.remote_addr if request else None
    }

def get_user_permissions(role):
    """Get permissions for a role."""
    return ROLES.get(role, ['read'])

def has_permission(role, permission):
    """Check if a role has a permission."""
    permissions = get_user_permissions(role)
    return permission in permissions

def sanitize_text(text, max_length=1000):
    """Sanitize text input by stripping and limiting length."""
    if not isinstance(text, str):
        return ''
    return text.strip()[:max_length]

def validate_id(id_str, prefix=None):
    """Validate ID format."""
    if not id_str or not isinstance(id_str, str):
        return False
    if prefix and not id_str.startswith(prefix):
        return False
    # Basic check for length and characters
    return len(id_str) > 10 and len(id_str) < 100 and all(c.isalnum() or c in '-_' for c in id_str)


# ----------------------------------------
# COMMUNITY-SCOPED RBAC DECORATORS
# ----------------------------------------
# These are the NEW decorators for multi-tenant mode.
# They check both role AND community membership.
# Use g.current_role and g.community_id from community_context_middleware.

def community_role_required(*community_roles):
    """
    Decorator to require specific roles WITHIN the current community.
    
    Usage:
        @community_role_required('Admin', 'Police')
        def my_route():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            from flask import g
            
            # Check authentication
            if 'user_id' not in session:
                return jsonify({'success': False, 'error': 'Authentication required'}), 401
            
            # Check community context
            if not hasattr(g, 'community_id') or not g.community_id:
                return jsonify({'success': False, 'error': 'Invalid community context'}), 400
            
            # Check community membership
            if not hasattr(g, 'current_role') or not g.current_role:
                return jsonify({
                    'success': False,
                    'error': f'Not a member of community {g.community_id}'
                }), 403
            
            # Check role in community
            if g.current_role not in community_roles:
                return jsonify({
                    'success': False,
                    'error': f'Insufficient permissions in this community. Required: {community_roles}'
                }), 403
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def community_admin_required_scoped(f):
    """Require Admin or Owner role IN THE CURRENT COMMUNITY."""
    return community_role_required('Owner', 'Admin')(f)


def community_police_required_scoped(f):
    """Require Police or Admin role IN THE CURRENT COMMUNITY."""
    return community_role_required('Police', 'EMS', 'Dispatch', 'DOJ', 'Staff', 'LEO', 'Admin', 'Owner')(f)


def community_dispatch_required_scoped(f):
    """Require Dispatch or Admin role IN THE CURRENT COMMUNITY."""
    return community_role_required('Dispatch', 'Police', 'EMS', 'DOJ', 'Staff', 'LEO', 'Admin', 'Owner')(f)


def community_judge_required_scoped(f):
    """Require Judge or Admin role IN THE CURRENT COMMUNITY."""
    return community_role_required('Judge', 'DOJ', 'Admin', 'Owner')(f)


def community_dmv_required_scoped(f):
    """Require DMV or Admin role IN THE CURRENT COMMUNITY."""
    return community_role_required('DMV', 'Admin', 'Owner')(f)


def community_member_required_scoped(f):
    """Require membership IN THE CURRENT COMMUNITY (any role)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import g
        
        # Check authentication
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401
        
        # Check community context
        if not hasattr(g, 'community_id') or not g.community_id:
            return jsonify({'success': False, 'error': 'Invalid community context'}), 400
        
        # Check community membership
        if not hasattr(g, 'current_role') or not g.current_role:
            return jsonify({
                'success': False,
                'error': 'Not a member of this community'
            }), 403
        
        return f(*args, **kwargs)
    return decorated_function
