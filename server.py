import os
import json
import random
import re
from io import BytesIO
import smtplib
import logging
import secrets
import string
import uuid
import time
import urllib.request
import base64
import hashlib
import hmac
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, jsonify, send_from_directory, send_file, session, redirect, abort, g
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_migrate import Migrate
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import text, or_, and_, func
from security_service import (
    admin_required,
    police_required,
    dispatch_required,
    judge_required,
    dmv_required,
    require_auth,
    verify_password,
    hash_password,
    validate_password_policy,
    ROLES
)
from performance_service import cache, paginate_query
from cad_access import (
    CAD_ACCESS_ROLES as CANONICAL_CAD_ACCESS_ROLES,
    evaluate_police_cad_access,
    normalize_community_role as canonical_normalize_community_role,
    role_allows_police_cad,
)
from ai_service import get_ai_config, ai_runtime_or_error, chat_json
from evidence_storage import (
    LINK_ONLY_DISABLED_MESSAGE, get_storage_config, validate_upload,
    relative_storage_path, save_local_file, resolve_local_path,
)
from warrant_pdf import (
    TYPE_PREFIXES, WARRANT_TYPES, build_warrant_pdf, safe_warrant_pdf_filename,
)
from platform_config import (
    PLATFORM_NAME,
    PLATFORM_DOMAIN,
    PLATFORM_TAGLINE,
    PLATFORM_CTA,
    DEFAULT_COMMUNITY_NAME,
    DEFAULT_COMMUNITY_SLUG,
    DEFAULT_COMMUNITY_CAD_NAME,
    DEFAULT_COMMUNITY_DEPARTMENTS,
)

# Force clear SQLAlchemy metadata cache to ensure fresh schema detection
import sqlalchemy
from sqlalchemy import inspect as sa_inspect

# This ensures we don't use cached metadata
if hasattr(sqlalchemy, '_sa_registry'):
    sqlalchemy._sa_registry.clear()

# Import database and models FIRST
from database import db, configure_database
from models import (
    User, Config, Complaint, Application, Civilian, Vehicle, License,
    Warrant, Arrest, Incident, Evidence, TrafficStop, Call911,
    ActivityLog, Bolo, OfficerSession, Alert, RadioLog,
    ServerStatus, Inmate, Hearing, DispatchCall,
    KnownAssociate, Business, Citation, JailBooking,
    UseOfForceReport, OfficerNote, CaseFile, CaseCharge, CadAuditLog,
    AIGenerationLog, AuditLog, EvidenceAttachment,
    Community, CommunityMember, CommunityInvite,
    PlatformAdminLog, PlatformActivityLog, PasswordResetToken, CommunityStatus, UserSession, Notification, NotificationRecipient, MobilePushToken
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
PROCESS_START_TIME = time.time()

def get_openrouter_http_referer():
    domain = (os.getenv('OPENROUTER_HTTP_REFERER') or PLATFORM_DOMAIN or 'gtavcad.app').strip()
    if domain.startswith(('http://', 'https://')):
        return domain
    return f'https://{domain}'


def _safe_log_path(path):
    parts = path.strip('/').split('/')
    if len(parts) >= 3 and parts[0] == 'api' and parts[1] == 'invites':
        parts[2] = '[redacted-invite-code]'
        return '/' + '/'.join(parts)
    if len(parts) >= 5 and parts[0] == 'api' and parts[1] == 'communities' and parts[3] == 'invites':
        parts[4] = '[redacted-invite-code]'
        return '/' + '/'.join(parts)
    return path


ACTIVE_SOCKET_CONNECTIONS = {}
SOCKET_AUTH_CONTEXT = {}
SOCKET_RATE_LIMITS = {}
WEBSOCKET_EVENTS_PER_MINUTE = 120


def _safe_json_error(message, code, status=400, details=None):
    return jsonify({
        'success': False,
        'error': message,
        'code': code,
        'request_id': getattr(g, 'request_id', None),
        'details': details or {}
    }), status


def parse_bool(value, default=False):
    """Strictly parse booleans from JSON/form payload values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {'true', '1', 'yes', 'y', 'on'}:
            return True
        if lowered in {'false', '0', 'no', 'n', 'off', ''}:
            return False
    return default



def get_platform_ai_config():
    return get_ai_config()


def get_platform_ai_runtime_or_error():
    cfg = get_platform_ai_config()
    if not cfg['enabled'] or not cfg['has_api_key']:
        return None, (jsonify({'success': False, 'error': 'AI assistant is not configured by the platform owner'}), 503)
    return cfg, None


def log_ai_request(route_name, success, model, error_message=None):
    try:
        uid = session.get('user_id')
        log_row = AIGenerationLog(
            community_id=community_id,
            user_id=uid if isinstance(uid, int) else None,
            generation_type=route_name,
            provider='OpenRouter',
            model=model,
            status='success' if success else 'failure',
            error_message=(error_message or '')[:500] if not success else None,
        )
        db.session.add(log_row)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f'AI usage log failed for {route_name}: {e}')

def _user_field(user, field_name, default=None):
    """Read a user field safely to tolerate optional/nullable columns."""
    try:
        value = getattr(user, field_name)
    except Exception:
        return default
    return default if value is None else value


def _session_hydrate_user(user):
    """Hydrate auth session with required user fields and defensive fallbacks."""
    user_id = _user_field(user, 'id')
    username = _user_field(user, 'username', '') or ''
    email = _user_field(user, 'email', None)
    role = (_user_field(user, 'role', 'Civilian') or 'Civilian').strip() or 'Civilian'
    platform_role = (_user_field(user, 'platform_role', None) or role).strip() or role

    owner_email = (os.getenv('PLATFORM_OWNER_EMAIL') or '').strip().lower()
    email_matches_owner_env = bool(owner_email and (email or '').strip().lower() == owner_email)
    is_platform_owner = role == 'PlatformOwner' or platform_role == 'PlatformOwner'

    if email_matches_owner_env and not is_platform_owner:
        logger.warning(
            "Platform owner email matched but persisted role is not PlatformOwner user_id=%s",
            user_id,
        )

    session['user_id'] = user_id
    session['username'] = username
    session['email'] = email
    session['role'] = role
    session['platform_role'] = platform_role
    session['is_platform_owner'] = is_platform_owner
    session['active_community_id'] = session.get('selected_community_id')

    missing = [k for k in ('user_id', 'username', 'role', 'platform_role', 'is_platform_owner') if session.get(k) in (None, '')]
    if missing:
        logger.warning("Session hydration missing required fields user_id=%s missing=%s", user_id, missing)

    session.modified = True
    logger.info("Session created user_id=%s username=%s role=%s platform_role=%s is_platform_owner=%s",
                user_id, username, role, platform_role, is_platform_owner)

    return is_platform_owner


def ensure_civilians_user_id_schema():
    """Safely add the nullable civilians.user_id column/index on drifted databases."""
    try:
        inspector = sa_inspect(db.engine)
        if 'civilians' not in inspector.get_table_names():
            return False

        columns = {column['name'] for column in inspector.get_columns('civilians')}
        dialect = db.engine.dialect.name
        statements = []
        if 'user_id' not in columns:
            if dialect == 'postgresql':
                statements.append('ALTER TABLE civilians ADD COLUMN IF NOT EXISTS user_id INTEGER')
            else:
                statements.append('ALTER TABLE civilians ADD COLUMN user_id INTEGER')

        index_name = 'idx_civilians_user_id'
        try:
            index_names = {index.get('name') for index in inspector.get_indexes('civilians')}
        except Exception:
            index_names = set()
        if index_name not in index_names:
            if dialect == 'postgresql':
                statements.append(f'CREATE INDEX IF NOT EXISTS {index_name} ON civilians (user_id)')
            else:
                statements.append(f'CREATE INDEX {index_name} ON civilians (user_id)')

        if not statements:
            return True

        with db.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        logger.info('civilians.user_id schema sync completed statements=%s', len(statements))
        return True
    except Exception as exc:
        logger.warning(
            'civilians.user_id schema sync skipped request_id=%s error=%s',
            getattr(g, 'request_id', None),
            exc,
        )
        return False


def _safe_commit_last_login(user):
    """Update last_login without allowing schema drift to break successful auth."""
    try:
        user.last_login = datetime.utcnow()
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        logger.warning(
            'Auth last_login update skipped request_id=%s user_id=%s error=%s',
            getattr(g, 'request_id', None),
            getattr(user, 'id', None),
            exc,
        )
        return False


def _safe_get_user_community_membership(user_id):
    """Resolve active membership without letting optional context break login."""
    try:
        return get_user_community_membership(user_id)
    except Exception as exc:
        db.session.rollback()
        logger.warning(
            'Auth membership lookup skipped request_id=%s user_id=%s error=%s',
            getattr(g, 'request_id', None),
            user_id,
            exc,
        )
        return None, None


def _safe_user_can_access_police_cad(owner=False, community_role=None, user=None, membership=None):
    """Serialize CAD access defensively so auth/session endpoints do not 500."""
    try:
        return user_can_access_police_cad(owner, community_role, user=user, membership=membership)
    except Exception as exc:
        logger.warning(
            'Auth CAD access serialization skipped request_id=%s user_id=%s error=%s',
            getattr(g, 'request_id', None),
            getattr(user, 'id', None),
            exc,
        )
        return False

# Production logging configuration
if os.environ.get('FLASK_ENV') == 'production':
    logging.getLogger('werkzeug').setLevel(logging.WARNING)  # Reduce Flask request logging
    logger.info('🔒 Production mode: Reduced logging verbosity')

app = Flask(__name__, static_folder='.', static_url_path='')
if not os.environ.get('SECRET_KEY'):
    raise RuntimeError('SECRET_KEY environment variable is required')

app.config['SECRET_KEY'] = os.environ['SECRET_KEY']
app.secret_key = app.config['SECRET_KEY']
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configure secure session cookies
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production' or parse_bool(os.environ.get('SESSION_COOKIE_SECURE'), False)
app.config['SESSION_COOKIE_DOMAIN'] = os.environ.get('SESSION_COOKIE_DOMAIN') or None
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=int(os.environ.get('SESSION_DAYS', '7')))
JWT_ISSUER = os.environ.get('JWT_ISSUER', 'gtavcad-shared-backend')
JWT_AUDIENCE = os.environ.get('JWT_AUDIENCE', 'gtavcad-web-clients')
JWT_MAX_AGE_SECONDS = int(os.environ.get('JWT_MAX_AGE_SECONDS', '604800'))
JWT_SECRET = f"{app.config['SECRET_KEY']}:{os.environ.get('JWT_SALT', 'gtavcad-api-token')}".encode('utf-8')


def _split_csv_env(name):
    return [item.strip() for item in (os.environ.get(name) or '').split(',') if item.strip()]


def _allowed_web_origins():
    origins = set(_split_csv_env('WEB_ALLOWED_ORIGINS') + _split_csv_env('CORS_ALLOWED_ORIGINS'))
    for value in (PLATFORM_DOMAIN, os.environ.get('PUBLIC_BASE_URL'), os.environ.get('API_BASE_URL')):
        if not value:
            continue
        value = value.strip()
        origins.add(value if value.startswith(('http://', 'https://')) else f'https://{value}')
    origins.update({'https://gtavcad.app', 'https://www.gtavcad.app', 'https://gtavcad.com', 'https://www.gtavcad.com'})
    return {origin.rstrip('/') for origin in origins}


@app.after_request
def apply_shared_backend_cors(response):
    origin = request.headers.get('Origin')
    allowed = _allowed_web_origins()
    if origin and origin.rstrip('/') in allowed:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    return response


@app.before_request
def handle_shared_backend_preflight():
    if request.method == 'OPTIONS' and request.path.startswith('/api/'):
        return ('', 204)


def _jwt_b64encode(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def _jwt_b64decode(value):
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode('ascii'))


def issue_api_token(user, community_id=None):
    now = int(time.time())
    header = {'alg': 'HS256', 'typ': 'JWT'}
    payload = {
        'sub': str(user.id),
        'username': user.username,
        'role': user.role,
        'platform_role': user.platform_role,
        'community_id': community_id,
        'iss': JWT_ISSUER,
        'aud': JWT_AUDIENCE,
        'iat': now,
        'exp': now + JWT_MAX_AGE_SECONDS,
    }
    signing_input = f"{_jwt_b64encode(header)}.{_jwt_b64encode(payload)}"
    signature = hmac.new(JWT_SECRET, signing_input.encode('ascii'), hashlib.sha256).digest()
    return f"{signing_input}.{_jwt_b64encode(signature)}"


def verify_api_token(token):
    try:
        header_b64, payload_b64, signature_b64 = token.split('.', 2)
        signing_input = f'{header_b64}.{payload_b64}'
        expected = _jwt_b64encode(hmac.new(JWT_SECRET, signing_input.encode('ascii'), hashlib.sha256).digest())
        if not hmac.compare_digest(signature_b64, expected):
            return None
        header = json.loads(_jwt_b64decode(header_b64))
        payload = json.loads(_jwt_b64decode(payload_b64))
    except Exception:
        return None
    if header.get('alg') != 'HS256' or payload.get('iss') != JWT_ISSUER or payload.get('aud') != JWT_AUDIENCE:
        return None
    if int(payload.get('exp') or 0) < int(time.time()):
        return None
    try:
        user_id = int(payload.get('sub'))
    except (TypeError, ValueError):
        return None
    user = User.query.get(user_id)
    if not user or not getattr(user, 'active', False):
        return None
    return user, payload


@app.before_request
def hydrate_bearer_token_session():
    if not request.path.startswith('/api/'):
        return None
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.lower().startswith('bearer '):
        return None
    verified = verify_api_token(auth_header.split(None, 1)[1].strip())
    if not verified:
        return None
    user, payload = verified
    if session.get('user_id') != user.id:
        session.clear()
    _session_hydrate_user(user)
    community_id = payload.get('community_id')
    if community_id:
        session['selected_community_id'] = community_id
        session['active_community_id'] = community_id
    session.modified = True
    return None


configure_database(app)


# Bootstrap and validation
def bootstrap_system():
    """Perform system bootstrap and validation on startup."""
    logger.info('🔧 Starting system bootstrap...')

    # Validate required environment variables
    required_vars = ['DATABASE_URL']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        logger.error(f'❌ Missing required environment variables: {", ".join(missing_vars)}')
        logger.error('Please set these variables and restart the application.')
        return False

    # Check for weak secrets
    flask_secret = os.environ.get('FLASK_SECRET')
    if flask_secret and len(flask_secret) < 32:
        logger.warning('⚠️  FLASK_SECRET is shorter than 32 characters. Consider using a longer secret for better security.')

    admin_password_hash = os.environ.get('ADMIN_PASSWORD_HASH')
    if admin_password_hash and len(admin_password_hash) < 60:  # bcrypt hashes are ~60 chars
        logger.warning('⚠️  ADMIN_PASSWORD_HASH appears to be weak or incorrectly set.')

    with app.app_context():
        # Check database connection
        try:
            with db.engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            logger.info('✅ Database connection successful')
        except Exception as e:
            logger.error(f'❌ Database connection failed: {e}')
            return False

        # Check migration system
        try:
            inspector = sa_inspect(db.engine)
            if 'alembic_version' in inspector.get_table_names():
                logger.info('✅ Migration system initialized')
            else:
                logger.warning('⚠️  Migration system not initialized. Run `flask db upgrade` to apply migrations.')
        except Exception as e:
            logger.error(f'❌ Migration check failed: {e}')

        # Check if users table exists and has admin
        try:
            inspector = sa_inspect(db.engine)
            if 'users' in inspector.get_table_names():
                admin_count = User.query.filter_by(role='Admin', active=True).count()
                if admin_count == 0:
                    logger.warning('⚠️  No active admin users found. System will run in setup mode.')
                    logger.info('To create the first admin user, use the bootstrap endpoint or create manually.')
                else:
                    logger.info(f'✅ Found {admin_count} active admin user(s)')
            else:
                logger.warning('⚠️  Users table not found. Run migrations or create tables.')

            # Initialize default config if config table exists
            if 'config' in inspector.get_table_names():
                initialize_default_config()
                logger.info('✅ Config initialized')
        except Exception as e:
            logger.error(f'❌ Error checking admin users: {e}')

        # Run PlatformOwner migration to ensure admin account is properly configured
        try:
            _run_platform_owner_migration()
        except Exception as e:
            logger.warning(f'⚠️  PlatformOwner migration skipped (non-fatal): {e}')

    logger.info('✅ System bootstrap completed')
    return True


def _run_platform_owner_migration():
    """Ensure the PlatformOwner account exists and has a valid password_hash.

    This replicates the logic from migrate_admin_password.py inline so it can
    run inside the already-established app context without a circular import.
    Gracefully skips user creation when the required password env var is absent.
    """
    from werkzeug.security import generate_password_hash as _gen_hash

    def _env_true(value):
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    logger.info('🔑 Running PlatformOwner migration...')

    platform_owner_email = (
        os.getenv('PLATFORM_OWNER_EMAIL') or
        os.getenv('ADMIN_EMAIL') or
        'admin@govdirect.org'
    ).strip().lower()
    platform_owner_username = (
        os.getenv('PLATFORM_OWNER_USERNAME') or
        os.getenv('ADMIN_USERNAME') or
        'platformowner'
    ).strip()
    initial_password = (
        os.getenv('PLATFORM_OWNER_PASSWORD') or
        os.getenv('PLATFORM_OWNER_INITIAL_PASSWORD') or
        os.getenv('ADMIN_PASSWORD')
    )
    force_reset = _env_true(os.environ.get('FORCE_ADMIN_PASSWORD_RESET', 'false'))

    connection = db.engine.raw_connection()
    try:
        cursor = connection.cursor()

        # Ensure users table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'users'
            )
        """)
        if not cursor.fetchone()[0]:
            logger.warning('⚠️  PlatformOwner migration skipped: users table does not exist yet')
            cursor.close()
            return

        # Ensure platform_role column exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'platform_role'
            )
        """)
        if not cursor.fetchone()[0]:
            logger.info('   Adding platform_role column to users table...')
            cursor.execute('ALTER TABLE users ADD COLUMN platform_role VARCHAR(64)')
            connection.commit()
            logger.info('   ✓ platform_role column added')

        # Look up existing PlatformOwner by email
        cursor.execute("""
            SELECT id, email, password_hash, role, platform_role, active
            FROM users
            WHERE LOWER(email) = %s
        """, (platform_owner_email,))
        admin_user = cursor.fetchone()

        if not admin_user:
            # No user found by email — check if username is already taken
            cursor.execute("""
                SELECT id FROM users WHERE LOWER(username) = LOWER(%s)
            """, (platform_owner_username,))
            username_conflict = cursor.fetchone()

            if username_conflict:
                # Username exists under a different email — update that user instead
                conflict_id = username_conflict[0]
                logger.warning(
                    '⚠️  Username "%s" already exists (id=%s) under a different email — '
                    'updating that user to PlatformOwner role instead of inserting a duplicate',
                    platform_owner_username, conflict_id,
                )
                cursor.execute("""
                    UPDATE users
                    SET role = 'PlatformOwner', platform_role = 'PlatformOwner', active = true
                    WHERE id = %s
                """, (conflict_id,))
                connection.commit()
                logger.info('✅ PlatformOwner role assigned to existing user id=%s', conflict_id)
                cursor.close()
                return

            # No user found — only create one if we have a password to set
            if not initial_password:
                logger.warning(
                    '⚠️  PlatformOwner user not found for email=%s and no password env var '
                    '(PLATFORM_OWNER_PASSWORD / ADMIN_PASSWORD) is set — skipping creation '
                    'to avoid inserting a NULL password_hash',
                    platform_owner_email,
                )
                cursor.close()
                return

            password_hash = _gen_hash(initial_password, method='pbkdf2:sha256')
            cursor.execute("""
                INSERT INTO users (username, email, password_hash, role, platform_role, active)
                VALUES (%s, %s, %s, 'PlatformOwner', 'PlatformOwner', true)
            """, (platform_owner_username, platform_owner_email, password_hash))
            connection.commit()
            logger.info('✅ PlatformOwner user created: %s', platform_owner_email)
            cursor.close()
            return

        user_id, email, current_hash, current_role, current_platform_role, current_active = admin_user
        should_set_password = (not current_hash) or force_reset

        if should_set_password:
            if not initial_password:
                logger.warning(
                    '⚠️  PlatformOwner (id=%s) has no password_hash and no password env var is set — '
                    'updating role/status only to avoid NULL constraint violation',
                    user_id,
                )
                cursor.execute("""
                    UPDATE users
                    SET role = 'PlatformOwner', platform_role = 'PlatformOwner', active = true
                    WHERE LOWER(email) = %s AND password_hash IS NOT NULL
                """, (platform_owner_email,))
                if cursor.rowcount == 0:
                    logger.warning(
                        '⚠️  Skipped UPDATE for PlatformOwner (id=%s): password_hash is NULL '
                        'and no password provided — set PLATFORM_OWNER_PASSWORD to fix this',
                        user_id,
                    )
            else:
                new_hash = _gen_hash(initial_password, method='pbkdf2:sha256')
                cursor.execute("""
                    UPDATE users
                    SET password_hash = %s, role = 'PlatformOwner', platform_role = 'PlatformOwner', active = true
                    WHERE LOWER(email) = %s
                """, (new_hash, platform_owner_email))
                logger.info('✅ PlatformOwner password initialized/reset for: %s', email)
        else:
            cursor.execute("""
                UPDATE users
                SET role = 'PlatformOwner', platform_role = 'PlatformOwner', active = true
                WHERE LOWER(email) = %s
            """, (platform_owner_email,))
            logger.info('✅ PlatformOwner role/status confirmed for: %s (existing password preserved)', email)

        connection.commit()
        logger.info('✅ PlatformOwner migration completed successfully')
        cursor.close()
    except Exception as e:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_default_config():
    """Initialize default configuration values."""
    defaults = {
        'platform_name': (PLATFORM_NAME, 'Global platform name'),
        'platform_domain': (PLATFORM_DOMAIN, 'Global platform domain'),
        'platform_tagline': (PLATFORM_TAGLINE, 'Global platform positioning'),
        'platform_cta': (PLATFORM_CTA, 'Global onboarding call to action'),
        'server_name': (PLATFORM_NAME, 'Legacy public alias for the platform name'),
        'server_id': ('platform', 'Unique identifier for this platform instance'),
        'departments': (DEFAULT_COMMUNITY_DEPARTMENTS, 'Available police departments for the default tenant'),
        'officer_ranks': (['Officer', 'Sergeant', 'Lieutenant', 'Captain', 'Chief'], 'Available officer ranks'),
        'penal_codes': ({
            '1.01': 'Reckless Driving',
            '1.02': 'Speeding',
            '2.01': 'Assault',
            '2.02': 'Battery',
            '3.01': 'Theft',
            '3.02': 'Burglary'
        }, 'Penal code definitions'),
        'call_types': (['Emergency', 'Non-Emergency', 'Traffic', 'Medical', 'Fire'], 'Available call types'),
        'vehicle_categories': (['Sedan', 'SUV', 'Truck', 'Motorcycle', 'Commercial'], 'Vehicle categories'),
        'evidence_categories': (['Physical', 'Digital', 'Witness', 'Surveillance'], 'Evidence categories'),
        'agency_names': ({
            'LSPD': 'Los Santos Police Department',
            'BCSO': 'Blaine County Sheriff\'s Office',
            'SWAT': 'Special Weapons and Tactics'
        }, 'Agency name mappings'),
        'default_officers': ([
            {'id': '1L-01', 'name': 'Chief Unit', 'status': 'Available', 'department': 'LSPD'},
            {'id': '2L-12', 'name': 'Patrol Unit', 'status': 'En Route', 'department': 'LSPD'},
            {'id': '3L-22', 'name': 'Traffic Unit', 'status': 'On Scene', 'department': 'Traffic Division'},
            {'id': 'D-04', 'name': 'Dispatch', 'status': 'Active', 'department': 'Dispatch'},
            {'id': 'K9-02', 'name': 'K9 Unit', 'status': 'Available', 'department': 'K9 Unit'},
            {'id': 'GU-01', 'name': 'Gang Unit 1', 'status': 'Available', 'department': 'Gang Enforcement'},
            {'id': 'GU-02', 'name': 'Gang Unit 2', 'status': 'Available', 'department': 'Gang Enforcement'},
            {'id': 'BCSO-1', 'name': 'BCSO Deputy 1', 'status': 'Available', 'department': 'BCSO'},
            {'id': 'BCSO-2', 'name': 'BCSO Deputy 2', 'status': 'Off Duty', 'department': 'BCSO'},
            {'id': 'SWT-1', 'name': 'SWAT Unit', 'status': 'Off Duty', 'department': 'SWAT'}
        ], 'Default officer units')
    }

    import json
    for key, (value, description) in defaults.items():
        config = Config.query.filter_by(key=key, community_id=None).first()
        serialized_value = json.dumps(value)
        if config:
            if config.value != serialized_value or config.description != description:
                config.value = serialized_value
                config.description = description
        else:
            config = Config(
                key=key,
                community_id=None,
                value=serialized_value,
                description=description
            )
            db.session.add(config)
    db.session.commit()


def get_config(key, default=None, community_id=None):
    """Get configuration value by key, preferring tenant config when supplied."""
    config = None
    if community_id:
        config = Config.query.filter_by(key=key, community_id=community_id).first()
    if not config:
        config = Config.query.filter_by(key=key, community_id=None).first()
    if config and config.value:
        import json
        try:
            return json.loads(config.value)
        except:
            return config.value
    return default

# Run bootstrap
if not bootstrap_system():
    logger.error('❌ Bootstrap failed. Application may not function correctly.')

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Initialize cache
cache.init_app(app)


def _warn_invalid_socketio_origin():
    logger.warning('Skipping invalid Socket.IO origin')


def _normalize_socketio_origin(value):
    """Normalize a configured URL/domain to an Engine.IO origin."""
    raw_value = (value or '').strip().rstrip('/')
    if not raw_value:
        _warn_invalid_socketio_origin()
        return None
    if raw_value == '*':
        return '*'

    has_supported_scheme = raw_value.lower().startswith(('http://', 'https://'))
    if '://' in raw_value and not has_supported_scheme:
        _warn_invalid_socketio_origin()
        return None

    url_value = raw_value if has_supported_scheme else f'https://{raw_value}'
    try:
        parsed = urlsplit(url_value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        _warn_invalid_socketio_origin()
        return None

    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc or not hostname:
        _warn_invalid_socketio_origin()
        return None

    # Browsers send the Origin header as scheme + host (+ port), without any path,
    # query string, or fragment. Engine.IO compares against that exact origin string.
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), '', '', ''))


def _add_unique_socketio_origin(origins, seen, value):
    origin = _normalize_socketio_origin(value)
    if origin and origin not in seen:
        origins.append(origin)
        seen.add(origin)


def _is_local_socketio_hostname(hostname):
    value = (hostname or '').strip().lower().strip('[]')
    return value in {'localhost', '127.0.0.1', '::1'} or value.startswith('127.')


def _should_add_socketio_www_variant(hostname):
    """Add www only for apex custom domains, not local/hosted subdomains."""
    value = (hostname or '').strip().lower().strip('[]')
    if not value or value.startswith('www.') or _is_local_socketio_hostname(value):
        return False
    if value.endswith('.railway.app') or ':' in value:
        return False
    return value.count('.') == 1


def _add_socketio_origin_with_variants(origins, seen, value):
    origin = _normalize_socketio_origin(value)
    if not origin or origin == '*':
        return

    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname
        port_value = parsed.port
    except ValueError:
        _warn_invalid_socketio_origin()
        return

    if not hostname:
        _warn_invalid_socketio_origin()
        return

    _add_unique_socketio_origin(origins, seen, origin)

    if not _should_add_socketio_www_variant(hostname):
        return

    port = f':{port_value}' if port_value else ''
    www_origin = urlunsplit((parsed.scheme, f'www.{hostname}{port}', '', '', ''))
    _add_unique_socketio_origin(origins, seen, www_origin)


def _configured_socketio_domain_values():
    """Collect public domain/base-url settings that can safely seed CORS origins."""
    values = []
    platform_domain_env = (os.environ.get('PLATFORM_DOMAIN') or '').strip()
    if platform_domain_env:
        values.append(platform_domain_env)
    elif PLATFORM_DOMAIN:
        values.append(PLATFORM_DOMAIN)

    for key in ('PUBLIC_BASE_URL', 'APP_BASE_URL', 'BASE_URL', 'RAILWAY_PUBLIC_DOMAIN'):
        value = (os.environ.get(key) or '').strip()
        if value:
            values.append(value)
    return values


def _parse_socketio_allowed_origins():
    """Return a Socket.IO CORS allowlist without production wildcards."""
    env_name = (os.environ.get('FLASK_ENV') or os.environ.get('APP_ENV') or '').strip().lower()
    is_production = env_name == 'production'
    configured = os.environ.get('SOCKETIO_ALLOWED_ORIGINS')
    production_defaults = ['https://gtavcad.app', 'https://www.gtavcad.app', 'https://gtavcad.com', 'https://www.gtavcad.com']
    development_defaults = [
        'http://localhost:5000',
        'http://127.0.0.1:5000',
        'http://localhost:3000',
        'http://127.0.0.1:3000',
    ]
    origins = []
    seen = set()

    if configured is not None:
        for raw_origin in configured.split(','):
            origin = _normalize_socketio_origin(raw_origin)
            if not origin:
                continue
            if origin == '*':
                logger.warning('Ignoring wildcard Socket.IO CORS origin')
                continue
            _add_unique_socketio_origin(origins, seen, origin)
        if origins:
            logger.info('Socket.IO CORS allowlist configured count=%s origins=%s', len(origins), origins)
            return origins

    if is_production:
        for value in _configured_socketio_domain_values():
            _add_socketio_origin_with_variants(origins, seen, value)
        if not origins:
            for fallback_origin in production_defaults:
                _add_unique_socketio_origin(origins, seen, fallback_origin)
    else:
        for fallback_origin in development_defaults:
            _add_unique_socketio_origin(origins, seen, fallback_origin)
        for value in _configured_socketio_domain_values():
            _add_socketio_origin_with_variants(origins, seen, value)

    logger.info('Socket.IO CORS allowlist configured count=%s origins=%s', len(origins), origins)
    return origins


# Initialize Flask-Migrate
migrate = Migrate(app, db)

socketio = SocketIO(
    app,
    cors_allowed_origins=_parse_socketio_allowed_origins(),
    async_mode=os.environ.get('SOCKETIO_ASYNC_MODE', 'eventlet'),
    ping_interval=25,
    ping_timeout=60,
    manage_session=True,
)


def community_room_name(community_slug):
    return f"community:{community_slug}"


def get_community_by_any_id(value):
    """Resolve a community by tenant community_id first, then legacy numeric row id."""
    if not value:
        return None
    community = Community.query.filter_by(community_id=value).first()
    if community:
        return community
    try:
        numeric_id = int(value)
    except (TypeError, ValueError):
        return None
    return Community.query.filter_by(id=numeric_id).first()


def get_user_room_context():
    user_id = session.get('user_id')
    community_id = get_current_community_id()
    if not user_id or not community_id:
        return None, None, None
    community = get_community_by_any_id(community_id)
    if not community:
        return None, None, None
    return user_id, community.community_id, community_room_name(community.slug)


def emit_community_event(event_name, payload, community_id=None):
    target_community_id = community_id or get_current_community_id()
    if not target_community_id:
        return
    community = get_community_by_any_id(target_community_id)
    if not community:
        return
    socketio.emit(event_name, payload, room=community_room_name(community.slug))


def _socket_bearer_context(auth):
    if not isinstance(auth, dict):
        return None, None, None
    raw_token = (auth.get('token') or auth.get('api_token') or auth.get('Authorization') or auth.get('authorization') or '').strip()
    if raw_token.lower().startswith('bearer '):
        raw_token = raw_token.split(None, 1)[1].strip()
    if not raw_token:
        return None, None, None
    verified = verify_api_token(raw_token)
    if not verified:
        return None, None, None
    user, payload = verified
    community_id = payload.get('community_id') or auth.get('community_id')
    community = get_community_by_any_id(community_id)
    if not community:
        return None, None, None
    return user.id, community.community_id, community_room_name(community.slug)


@socketio.on('connect')
def socket_connect(auth=None):
    sid = getattr(request, 'sid', None)
    user_id, community_id, room_name = get_user_room_context()
    if not user_id or not community_id or not room_name:
        user_id, community_id, room_name = _socket_bearer_context(auth)
    if not user_id or not community_id or not room_name:
        logger.warning('Socket auth failed: missing user/session context')
        return False
    membership = CommunityMember.query.filter_by(user_id=user_id, community_id=community_id, status='Active').first()
    if not membership:
        logger.warning(f'Socket auth failed: user {user_id} is not active in community {community_id}')
        return False
    existing_sid = ACTIVE_SOCKET_CONNECTIONS.get(user_id)
    if existing_sid and sid and existing_sid != sid:
        emit('socket:warning', {'message': 'Duplicate session detected; replacing older socket.'})
    if sid:
        ACTIVE_SOCKET_CONNECTIONS[user_id] = sid
        SOCKET_AUTH_CONTEXT[sid] = {'user_id': user_id, 'community_id': community_id, 'room_name': room_name}
    join_room(room_name)
    join_room(f'user:{user_id}')
    join_room(f'community:{community_id}')
    normalized_role = normalize_community_role(membership.role)
    if normalized_role in ('Police', 'LEO', 'Officer'):
        join_room(f'community:{community_id}:police')
    if normalized_role in ('Dispatch',):
        join_room(f'community:{community_id}:dispatch')
    if normalized_role in ('DMV',):
        join_room(f'community:{community_id}:dmv')
    if normalized_role in ('Admin', 'Owner', 'CommunityAdmin', 'CommunityOwner'):
        join_room(f'community:{community_id}:admin')
    if session.get('is_platform_owner'):
        join_room('platform:owners')
    from cad_helpers import log_audit
    log_audit(str(user_id), 'websocket_join', 'Socket', sid or 'unknown', actor_role=session.get('role'), ip_address=request.remote_addr)
    emit('socket:ready', {'success': True, 'room': room_name, 'community_id': community_id})
    emit_community_event('presence:update', {'user_id': user_id, 'state': 'ONLINE', 'community_id': community_id}, community_id=community_id)


@socketio.on('disconnect')
def socket_disconnect():
    sid = getattr(request, 'sid', None)
    user_id, community_id, room_name = get_user_room_context()
    if (not user_id or not community_id or not room_name) and sid in SOCKET_AUTH_CONTEXT:
        ctx = SOCKET_AUTH_CONTEXT.get(sid) or {}
        user_id = ctx.get('user_id')
        community_id = ctx.get('community_id')
        room_name = ctx.get('room_name')
    if room_name:
        leave_room(room_name)
    if user_id in ACTIVE_SOCKET_CONNECTIONS and ACTIVE_SOCKET_CONNECTIONS.get(user_id) == sid:
        ACTIVE_SOCKET_CONNECTIONS.pop(user_id, None)
    if sid:
        SOCKET_AUTH_CONTEXT.pop(sid, None)
    if user_id and community_id:
        from cad_helpers import log_audit
        log_audit(str(user_id), 'websocket_leave', 'Socket', sid or 'unknown', actor_role=session.get('role'), ip_address=request.remote_addr)
        emit_community_event('presence:update', {'user_id': user_id, 'state': 'OFFLINE', 'community_id': community_id}, community_id=community_id)


@socketio.on('community:join')
def socket_join_community(data):
    try:
        sid = getattr(request, 'sid', 'unknown')
        rate_key = f'{sid}:community:join'
        bucket = SOCKET_RATE_LIMITS.setdefault(rate_key, {'window': time.time(), 'count': 0})
        if time.time() - bucket['window'] > 60:
            bucket['window'] = time.time()
            bucket['count'] = 0
        bucket['count'] += 1
        if bucket['count'] > 30:
            return emit('socket:error', {'error': 'Rate limit exceeded'})

        if not isinstance(data, dict):
            return emit('socket:error', {'error': 'Invalid payload'})

        user_id, community_id, room_name = get_user_room_context()
        if (not user_id or not community_id or not room_name) and getattr(request, 'sid', None) in SOCKET_AUTH_CONTEXT:
            ctx = SOCKET_AUTH_CONTEXT.get(getattr(request, 'sid', None)) or {}
            user_id = ctx.get('user_id')
            community_id = ctx.get('community_id')
            room_name = ctx.get('room_name')
        requested_slug = (data or {}).get('community_slug', '')
        if not user_id or not room_name:
            return emit('socket:error', {'error': 'Unauthorized'})
        membership = CommunityMember.query.filter_by(user_id=user_id, community_id=community_id, status='Active').first()
        if not membership:
            return emit('socket:error', {'error': 'Unauthorized'})
        if room_name != community_room_name(requested_slug):
            logger.warning(f"Tenant spoof attempt by user {user_id}: requested_slug={requested_slug}")
            return emit('socket:error', {'error': 'Invalid tenant room'})
        join_room(room_name)
        emit('community:joined', {'room': room_name, 'community_id': community_id, 'request_id': getattr(g, 'request_id', None)})
    except Exception as e:
        logger.exception(f'Websocket join failed: {e}')
        emit('socket:error', {'error': 'Unable to join room right now'})

from community_service import community_context_middleware, get_current_community_id, scoped_query, resolve_active_community, resolve_community_slug_from_path
from community_routes import register_community_routes

@app.before_request
def inject_community_context():
    """Attach tenant context for /c/<slug> routes and selected community sessions."""
    g.request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())
    g.request_started_at = time.time()
    g.client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    if request.path.startswith('/static/') or request.path.startswith('/assets/'):
        return None
    community_context_middleware()
    return None


@app.after_request
def enrich_response_metadata(response):
    response.headers['X-Request-ID'] = getattr(g, 'request_id', 'unknown')
    duration_ms = int((time.time() - getattr(g, 'request_started_at', time.time())) * 1000)
    if request.path.startswith('/api/'):
        logger.info(json.dumps({
            'event': 'api_request',
            'request_id': getattr(g, 'request_id', None),
            'path': _safe_log_path(request.path),
            'method': request.method,
            'status': response.status_code,
            'duration_ms': duration_ms,
            'ip': getattr(g, 'client_ip', request.remote_addr),
            'user_id': session.get('user_id'),
            'community_id': get_current_community_id(),
        }))
    return response

register_community_routes(app)
logger.info("✓ Community routes registered")


def _cad_session_values():
    return {
        'user_id': session.get('user_id'),
        'role': session.get('role'),
        'platform_role': session.get('platform_role'),
        'email': session.get('email'),
        'is_platform_owner': session.get('is_platform_owner'),
        'community_id': get_current_community_id(),
        'selected_community_id': session.get('selected_community_id'),
    }


def _log_police_cad_access_decision(route, decision):
    logger.debug(
        'Police CAD access decision route=%s user_id=%s community_id=%s role=%s normalized_role=%s '
        'platform_role=%s is_platform_owner=%s explicit_permission=%s final_can_access_police_cad=%s',
        route,
        decision.get('user_id'),
        decision.get('community_id'),
        decision.get('role'),
        decision.get('normalized_role'),
        decision.get('platform_role'),
        decision.get('is_platform_owner'),
        decision.get('explicit_permission'),
        decision.get('final_can_access_police_cad'),
    )


def _current_police_cad_access_decision(route=None, role=None, user=None, membership=None):
    user_id = session.get('user_id')
    if user is None and user_id:
        user = getattr(g, 'current_user', None) or User.query.get(user_id)
    if membership is None:
        membership = getattr(g, 'current_membership', None)
        community_id = get_current_community_id()
        if membership is None and user_id and community_id:
            membership = CommunityMember.query.filter_by(user_id=user_id, community_id=community_id, status='Active').first()
    if membership is not None:
        try:
            _community_admin_apply_cad_permission_attrs(membership)
        except NameError:
            pass
    effective_role = role
    if effective_role is None:
        effective_role = getattr(g, 'current_role', None) or getattr(membership, 'role', None) or session.get('current_role') or session.get('role', 'Civilian')
    decision = evaluate_police_cad_access(
        user=user,
        role=effective_role,
        membership=membership,
        session_values=_cad_session_values(),
    )
    _log_police_cad_access_decision(route or request.path, decision)
    return decision


def current_role_allows_police_cad():
    """True when the active user may access police CAD data/tools."""
    return _current_police_cad_access_decision(request.path).get('final_can_access_police_cad') is True


def require_police_cad_access():
    decision = _current_police_cad_access_decision(request.path)
    if decision.get('final_can_access_police_cad') is not True:
        return jsonify({'success': False, 'error': 'Police CAD access required'}), 403
    return None


def get_user_community_membership(user_id):
    if not user_id:
        return None, None
    selected_community_id = session.get('selected_community_id') or session.get('active_community_id')
    if selected_community_id:
        membership = CommunityMember.query.filter_by(
            user_id=user_id,
            community_id=selected_community_id,
            status='Active',
        ).first()
        if membership:
            community = Community.query.filter_by(community_id=membership.community_id).first()
            if community:
                return membership, community
    membership = CommunityMember.query.filter_by(user_id=user_id, status='Active').first()
    if not membership:
        return None, None
    community = Community.query.filter_by(community_id=membership.community_id).first()
    return membership, community


def user_can_access_police_cad(owner=False, community_role=None, user=None, membership=None):
    session_values = _cad_session_values()
    if owner:
        session_values['is_platform_owner'] = True
    if membership is not None:
        try:
            _community_admin_apply_cad_permission_attrs(membership)
        except NameError:
            pass
    decision = evaluate_police_cad_access(
        user=user,
        role=community_role,
        membership=membership,
        session_values=session_values,
    )
    _log_police_cad_access_decision(request.path if request else 'serialization', decision)
    return decision.get('final_can_access_police_cad') is True


def get_post_login_redirect(owner, community_slug, requires_community_setup):
    if owner:
        return '/admin'
    if community_slug:
        return f'/c/{community_slug}/'
    if requires_community_setup:
        return '/community-setup'
    return '/community-setup'


@app.errorhandler(500)
def internal_error(error):
    """Handle internal server errors without exposing stack traces."""
    logger.error(f'Internal server error: {error}')
    return jsonify({
        'success': False,
        'error': 'Internal server error',
        'code': 'INTERNAL_ERROR'
    }), 500



@app.route('/c/<slug>/civilian-portal', methods=['GET'])
@app.route('/c/<slug>/civilian-dashboard', methods=['GET'])
def tenant_civilian_dashboard_page(slug):
    return send_from_directory('.', 'civilian-dashboard.html')


@app.route('/civilian-portal', methods=['GET'])
@app.route('/civilian-dashboard', methods=['GET'])
def civilian_dashboard_page():
    return send_from_directory('.', 'civilian-dashboard.html')


@app.errorhandler(404)
def not_found_error(error):
    """Return JSON 404s only for API requests; let frontend paths render HTML."""
    if request.path.startswith('/api/'):
        return jsonify({
            'success': False,
            'error': 'Endpoint not found',
            'code': 'NOT_FOUND'
        }), 404

    requested_path = request.path.lstrip('/')
    if requested_path and requested_path.endswith('.html') and os.path.exists(os.path.join('.', requested_path)):
        return send_from_directory('.', requested_path)

    return send_from_directory('.', 'index.html')


@app.errorhandler(403)
def forbidden_error(error):
    """Handle 403 errors."""
    return jsonify({
        'success': False,
        'error': 'Forbidden',
        'code': 'FORBIDDEN'
    }), 403


@app.errorhandler(401)
def unauthorized_error(error):
    """Handle 401 errors."""
    return jsonify({
        'success': False,
        'error': 'Unauthorized',
        'code': 'UNAUTHORIZED'
    }), 401


@app.errorhandler(Exception)
def unhandled_exception(error):
    logger.exception(json.dumps({
        'event': 'unhandled_exception',
        'request_id': getattr(g, 'request_id', None),
        'path': request.path if request else None,
        'method': request.method if request else None,
        'user_id': session.get('user_id') if session else None,
        'community_id': get_current_community_id() if request else None,
        'error': str(error),
    }))
    if request.path.startswith('/api/'):
        return _safe_json_error('An unexpected error occurred.', 'UNEXPECTED_ERROR', 500)
    return send_from_directory('.', 'index.html')


def ensure_arrest_automation_schema():
    """Add columns needed by arrest-to-court automation on existing databases."""
    inspector = sa_inspect(db.engine)
    dialect = db.engine.dialect.name
    column_specs = {
        'dispatch_calls': {
            'call_id': 'VARCHAR(64)',
            'community_id': 'VARCHAR(64)',
            'caller_user_id': 'INTEGER',
            'created_by_user_id': 'INTEGER',
            'caller_name': 'VARCHAR(255)',
            'phone': 'VARCHAR(64)',
            'location': 'TEXT',
            'description': 'TEXT',
            'call_type': 'VARCHAR(255)',
            'priority': 'VARCHAR(64)',
            'status': 'VARCHAR(64)',
            'assigned_unit': 'VARCHAR(255)',
            'notes': 'TEXT',
            'created_at': 'TIMESTAMP',
            'updated_at': 'TIMESTAMP',
        },
        'warrants': {
            'warrant_id': 'VARCHAR(64)',
            'community_id': 'VARCHAR(64)',
            'civilian_id': 'VARCHAR(64)',
            'warrant_name': 'VARCHAR(255)',
            'warrant_charges': 'TEXT',
            'warrant_issuer': 'VARCHAR(255)',
            'warrant_notes': 'TEXT',
            'warrant_status': 'VARCHAR(64)',
            'justification': 'TEXT',
            'warrant_type': "VARCHAR(64) DEFAULT 'Arrest Warrant'",
            'warrant_number': 'VARCHAR(64)',
            'judge_or_authority': 'VARCHAR(255)',
            'issuing_agency': 'VARCHAR(255)',
            'subject_name': 'VARCHAR(255)',
            'subject_dob': 'VARCHAR(64)',
            'subject_address': 'TEXT',
            'charges_or_basis': 'TEXT',
            'probable_cause': 'TEXT',
            'search_location': 'TEXT',
            'items_to_seize': 'TEXT',
            'court_case_number': 'VARCHAR(128)',
            'bench_failure_reason': 'TEXT',
            'administrative_basis': 'TEXT',
            'inspection_scope': 'TEXT',
            'originating_jurisdiction': 'VARCHAR(255)',
            'extradition_location': 'VARCHAR(255)',
            'fugitive_last_known_location': 'TEXT',
            'alias_names': 'TEXT',
            'execution_instructions': 'TEXT',
            'expiration_date': 'VARCHAR(64)',
            'status': 'VARCHAR(64)',
            'created_by_user_id': 'INTEGER',
            'approved_by_user_id': 'INTEGER',
            'pdf_attachment_id': 'VARCHAR(64)',
            'pdf_generated_at': 'TIMESTAMP',
            'created_at': 'TIMESTAMP',
            'updated_at': 'TIMESTAMP',
        },
        'arrests': {
            'arrest_id': 'VARCHAR(64)',
            'civilian_id': 'VARCHAR(64)',
            'suspect_name': 'VARCHAR(255)',
            'charges': 'TEXT',
            'arresting_officer': 'VARCHAR(255)',
            'arrest_location': 'VARCHAR(255)',
            'evidence_attached': 'TEXT',
            'penalty': 'VARCHAR(255)',
            'report_notes': 'TEXT',
            'narrative': 'TEXT',
            'status': 'VARCHAR(64)',
            'created_at': 'TIMESTAMP',
            'updated_at': 'TIMESTAMP',
        },
        'jail_bookings': {
            'booking_id': 'VARCHAR(64)',
            'civilian_id': 'VARCHAR(64)',
            'arrest_id': 'VARCHAR(64)',
            'suspect_name': 'VARCHAR(255)',
            'charges': 'TEXT',
            'booking_officer': 'VARCHAR(255)',
            'cell_assignment': 'VARCHAR(64)',
            'bond_amount': 'FLOAT',
            'sentence_length': 'VARCHAR(255)',
            'status': 'VARCHAR(64)',
            'release_date': 'TIMESTAMP',
            'released_by': 'VARCHAR(255)',
            'release_reason': 'TEXT',
            'notes': 'TEXT',
            'created_at': 'TIMESTAMP',
            'updated_at': 'TIMESTAMP',
        },
        'hearings': {
            'hearing_id': 'VARCHAR(64)',
            'civilian_id': 'VARCHAR(64)',
            'arrest_id': 'VARCHAR(64)',
            'suspect_name': 'VARCHAR(255)',
            'charges': 'TEXT',
            'hearing_type': 'VARCHAR(64)',
            'status': 'VARCHAR(64)',
            'filing_officer': 'VARCHAR(255)',
            'scheduled_at': 'VARCHAR(64)',
            'judge': 'VARCHAR(255)',
            'notes': 'TEXT',
            'outcome': 'TEXT',
            'sentence_length': 'VARCHAR(255)',
            'fine_amount': 'VARCHAR(255)',
            'outcome_notes': 'TEXT',
            'created_at': 'TIMESTAMP',
            'updated_at': 'TIMESTAMP',
        },
        'inmates': {
            'civilian_id': 'VARCHAR(64)',
        },
        'case_files': {
            'case_number': 'VARCHAR(64)',
            'title': 'VARCHAR(255)',
            'case_type': 'VARCHAR(64)',
            'priority': 'VARCHAR(64)',
            'location': 'TEXT',
            'involved_civilians': 'TEXT',
            'involved_officers': 'TEXT',
            'linked_911_call_id': 'VARCHAR(64)',
            'linked_arrest_id': 'VARCHAR(64)',
            'linked_warrant_id': 'VARCHAR(64)',
            'linked_evidence_ids': 'TEXT',
            'report_notes': 'TEXT',
            'created_by': 'VARCHAR(255)',
            'assigned_to': 'VARCHAR(255)',
        },
        'civilians': {
            'user_id': 'INTEGER',
        },
        'evidence': {
            'evidence_type': 'VARCHAR(128)',
            'officer': 'VARCHAR(255)',
            'clip_link': 'TEXT',
            'screenshot_link': 'TEXT',
            'storage_status': 'VARCHAR(64)',
            'chain_of_custody': 'TEXT',
        },
    }
    for table, columns in column_specs.items():
        try:
            existing = {col['name'] for col in inspector.get_columns(table)}
        except Exception:
            continue
        for column, col_type in columns.items():
            if column in existing:
                continue
            if dialect == 'postgresql':
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}'))
            else:
                db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}'))
    db.session.commit()




def ensure_notification_schema():
    """Safely ensure backend notification tables exist for additive rollout."""
    try:
        db.create_all()
        inspector = sa_inspect(db.engine)
        tables = set(inspector.get_table_names())
        if 'notifications' not in tables or 'notification_recipients' not in tables:
            logger.warning('Notification tables still missing after create_all; check migration permissions.')
            return False
        return True
    except Exception as exc:
        logger.warning('Notification schema verification skipped: %s', exc)
        return False

def ensure_evidence_attachment_schema():
    """Safely create/sync additive evidence attachment storage table."""
    db.create_all()
    inspector = sa_inspect(db.engine)
    try:
        existing = {col['name'] for col in inspector.get_columns('evidence_attachments')}
    except Exception:
        return
    dialect = db.engine.dialect.name
    specs = {
        'attachment_id': 'VARCHAR(64)',
        'community_id': 'VARCHAR(64)',
        'case_id': 'VARCHAR(64)',
        'evidence_id': 'VARCHAR(64)',
        'arrest_id': 'VARCHAR(64)',
        'warrant_id': 'VARCHAR(64)',
        'court_packet_id': 'VARCHAR(64)',
        'uploaded_by_user_id': 'INTEGER',
        'original_filename': 'VARCHAR(255)',
        'stored_filename': 'VARCHAR(255)',
        'file_type': 'VARCHAR(64)',
        'mime_type': 'VARCHAR(255)',
        'file_size': 'INTEGER',
        'storage_mode': 'VARCHAR(32)',
        'storage_path': 'TEXT',
        'external_url': 'TEXT',
        'description': 'TEXT',
        'category': 'VARCHAR(128)',
        'review_status': "VARCHAR(64) DEFAULT 'submitted'",
        'created_at': 'TIMESTAMP',
        'updated_at': 'TIMESTAMP',
        'deleted_at': 'TIMESTAMP',
        'is_deleted': 'BOOLEAN DEFAULT FALSE',
    }
    for column, col_type in specs.items():
        if column in existing:
            continue
        if dialect == 'postgresql':
            db.session.execute(text(f'ALTER TABLE evidence_attachments ADD COLUMN IF NOT EXISTS {column} {col_type}'))
        else:
            db.session.execute(text(f'ALTER TABLE evidence_attachments ADD COLUMN {column} {col_type}'))
    db.session.commit()


def backfill_criminal_record_links():
    """Safely link legacy arrest/custody/court rows to civilians by arrest ID or full name."""
    dialect = db.engine.dialect.name
    try:
        if dialect == 'postgresql':
            statements = [
                """
                UPDATE arrests AS a
                SET civilian_id = c.civilian_id, updated_at = CURRENT_TIMESTAMP
                FROM civilians AS c
                WHERE COALESCE(NULLIF(TRIM(a.civilian_id), ''), '') = ''
                  AND LOWER(TRIM(a.suspect_name)) = LOWER(TRIM(CONCAT(c.first_name, ' ', c.last_name)))
                """,
                """
                UPDATE jail_bookings AS j
                SET civilian_id = a.civilian_id,
                    suspect_name = COALESCE(NULLIF(j.suspect_name, ''), a.suspect_name),
                    updated_at = CURRENT_TIMESTAMP
                FROM arrests AS a
                WHERE COALESCE(NULLIF(TRIM(j.civilian_id), ''), '') = ''
                  AND j.arrest_id = a.arrest_id
                  AND COALESCE(NULLIF(TRIM(a.civilian_id), ''), '') <> ''
                """,
                """
                UPDATE hearings AS h
                SET civilian_id = a.civilian_id,
                    suspect_name = COALESCE(NULLIF(h.suspect_name, ''), a.suspect_name),
                    updated_at = CURRENT_TIMESTAMP
                FROM arrests AS a
                WHERE COALESCE(NULLIF(TRIM(h.civilian_id), ''), '') = ''
                  AND h.arrest_id = a.arrest_id
                  AND COALESCE(NULLIF(TRIM(a.civilian_id), ''), '') <> ''
                """,
                """
                UPDATE jail_bookings AS j
                SET civilian_id = c.civilian_id, updated_at = CURRENT_TIMESTAMP
                FROM civilians AS c
                WHERE COALESCE(NULLIF(TRIM(j.civilian_id), ''), '') = ''
                  AND LOWER(TRIM(j.suspect_name)) = LOWER(TRIM(CONCAT(c.first_name, ' ', c.last_name)))
                """,
                """
                UPDATE hearings AS h
                SET civilian_id = c.civilian_id, updated_at = CURRENT_TIMESTAMP
                FROM civilians AS c
                WHERE COALESCE(NULLIF(TRIM(h.civilian_id), ''), '') = ''
                  AND LOWER(TRIM(h.suspect_name)) = LOWER(TRIM(CONCAT(c.first_name, ' ', c.last_name)))
                """,
                "UPDATE jail_bookings SET bond_amount = NULL WHERE bond_amount::text = 'Pending'",
            ]
        else:
            statements = [
                """
                UPDATE arrests
                SET civilian_id = (
                    SELECT civilians.civilian_id FROM civilians
                    WHERE LOWER(TRIM(arrests.suspect_name)) = LOWER(TRIM(civilians.first_name || ' ' || civilians.last_name))
                    LIMIT 1
                ), updated_at = CURRENT_TIMESTAMP
                WHERE COALESCE(TRIM(civilian_id), '') = ''
                  AND EXISTS (
                    SELECT 1 FROM civilians
                    WHERE LOWER(TRIM(arrests.suspect_name)) = LOWER(TRIM(civilians.first_name || ' ' || civilians.last_name))
                  )
                """,
                """
                UPDATE jail_bookings
                SET civilian_id = (
                    SELECT arrests.civilian_id FROM arrests
                    WHERE arrests.arrest_id = jail_bookings.arrest_id AND COALESCE(TRIM(arrests.civilian_id), '') <> ''
                    LIMIT 1
                ), updated_at = CURRENT_TIMESTAMP
                WHERE COALESCE(TRIM(civilian_id), '') = ''
                  AND EXISTS (SELECT 1 FROM arrests WHERE arrests.arrest_id = jail_bookings.arrest_id AND COALESCE(TRIM(arrests.civilian_id), '') <> '')
                """,
                """
                UPDATE hearings
                SET civilian_id = (
                    SELECT arrests.civilian_id FROM arrests
                    WHERE arrests.arrest_id = hearings.arrest_id AND COALESCE(TRIM(arrests.civilian_id), '') <> ''
                    LIMIT 1
                ), updated_at = CURRENT_TIMESTAMP
                WHERE COALESCE(TRIM(civilian_id), '') = ''
                  AND EXISTS (SELECT 1 FROM arrests WHERE arrests.arrest_id = hearings.arrest_id AND COALESCE(TRIM(arrests.civilian_id), '') <> '')
                """,
            ]
        for statement in statements:
            db.session.execute(text(statement))
        db.session.commit()
        logger.info('✓ Criminal record link backfill completed')
    except Exception as e:
        db.session.rollback()
        logger.warning(f'Criminal record link backfill skipped: {e}')

# Initialize database on startup
with app.app_context():
    try:
        db.create_all()
        ensure_civilians_user_id_schema()
        ensure_evidence_attachment_schema()
        ensure_arrest_automation_schema()
        ensure_notification_schema()
        backfill_criminal_record_links()
        logger.info('✓ Database tables verified on startup')
    except Exception as e:
        logger.error(f'Database initialization error: {e}')

# Ensure schema is synced on startup
try:
    from database import verify_schema
    verify_schema(app)
except Exception as e:
    logger.warning(f'Schema verification on startup: {e}')

DEFAULT_OFFICERS = [
    {'id': '1L-01',  'name': 'Chief Unit',      'status': 'Available', 'department': 'LSPD'},
    {'id': '2L-12',  'name': 'Patrol Unit',     'status': 'En Route',  'department': 'LSPD'},
    {'id': '3L-22',  'name': 'Traffic Unit',    'status': 'On Scene',  'department': 'Traffic Division'},
    {'id': 'D-04',   'name': 'Dispatch',        'status': 'Active',    'department': 'Dispatch'},
    {'id': 'K9-02',  'name': 'K9 Unit',         'status': 'Available', 'department': 'K9 Unit'},
    {'id': 'GU-01',  'name': 'Gang Unit 1',     'status': 'Available', 'department': 'Gang Enforcement'},
    {'id': 'GU-02',  'name': 'Gang Unit 2',     'status': 'Available', 'department': 'Gang Enforcement'},
    {'id': 'BCSO-1', 'name': 'BCSO Deputy 1',   'status': 'Available', 'department': 'BCSO'},
    {'id': 'BCSO-2', 'name': 'BCSO Deputy 2',   'status': 'Off Duty',  'department': 'BCSO'},
    {'id': 'SWT-1',  'name': 'SWAT Unit',       'status': 'Off Duty',  'department': 'SWAT'},
]


# ---------------------------------------------------------------------------
# Helper: convert model instances to dicts matching the original JSON shape
# ---------------------------------------------------------------------------

def bolo_to_dict(b):
    return {
        'id': b.bolo_id,
        'suspectName': b.suspect_name,
        'description': b.description,
        'lastLocation': b.last_location,
        'vehicle': b.vehicle or '',
        'charges': b.charges or '',
        'threatLevel': b.threat_level,
        'issuedBy': b.issued_by,
        'issuedAt': b.created_at.isoformat() if b.created_at else None,
        'status': b.status,
        'autoGenerated': b.auto_generated or False,
    }


def complaint_to_dict(c):
    return {
        'id': c.complaint_id,
        'complaintDiscord': c.complaint_discord,
        'reportedName': c.reported_name,
        'complaintType': c.complaint_type,
        'incidentDate': c.incident_date,
        'incidentLocation': c.incident_location,
        'witnesses': c.witnesses,
        'evidenceLink': c.evidence_link,
        'description': c.description,
        'resolution': c.resolution,
        'status': c.status,
        'staffNotes': c.staff_notes or '',
        'submittedAt': c.submitted_at.isoformat() if c.submitted_at else None,
        'updatedAt': c.updated_at.isoformat() if c.updated_at else None,
    }


def application_to_dict(a):
    return {
        'id': a.application_id,
        'appDiscord': a.app_discord,
        'appCharacter': a.app_character,
        'applicationType': a.application_type,
        'ageConfirmation': a.age_confirmation,
        'experience': a.experience,
        'roleReason': a.role_reason,
        'availability': a.availability,
        'status': a.status,
        'staffNotes': a.staff_notes or '',
        'submittedAt': a.submitted_at.isoformat() if a.submitted_at else None,
        'updatedAt': a.updated_at.isoformat() if a.updated_at else None,
    }


def session_to_dict(s):
    officer_name = s.officer_name or ''
    return {
        'callsign': s.callsign,
        'name': officer_name,
        'officerName': officer_name,
        'department': s.department or 'LSPD',
        'loggedInAt': s.logged_in_at.isoformat() if s.logged_in_at else None,
        'updatedAt': s.updated_at.isoformat() if s.updated_at else None,
        'status': s.status or 'On Duty',
    }


def officer_session_response(s):
    return {
        'callsign': s.callsign,
        'officerName': s.officer_name or '',
        'department': s.department or '',
        'status': s.status or 'On Duty',
    }


def ensure_officer_sessions_schema():
    """Safely add any missing officer session columns before CAD login queries."""
    if db.engine.dialect.name != 'postgresql':
        db.create_all()
        return

    statements = [
        """
        CREATE TABLE IF NOT EXISTS officer_sessions (
            id SERIAL PRIMARY KEY,
            callsign VARCHAR(64) UNIQUE NOT NULL,
            officer_name VARCHAR(255),
            department VARCHAR(255) DEFAULT 'LSPD',
            status VARCHAR(64) DEFAULT 'On Duty',
            logged_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "ALTER TABLE officer_sessions ADD COLUMN IF NOT EXISTS id SERIAL",
        "ALTER TABLE officer_sessions ADD COLUMN IF NOT EXISTS callsign VARCHAR(64)",
        "ALTER TABLE officer_sessions ADD COLUMN IF NOT EXISTS officer_name VARCHAR(255)",
        "ALTER TABLE officer_sessions ADD COLUMN IF NOT EXISTS department VARCHAR(255) DEFAULT 'LSPD'",
        "ALTER TABLE officer_sessions ADD COLUMN IF NOT EXISTS status VARCHAR(64) DEFAULT 'On Duty'",
        "ALTER TABLE officer_sessions ADD COLUMN IF NOT EXISTS logged_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE officer_sessions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    ]

    try:
        with db.engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    except Exception as e:
        logger.error(f'officer_sessions schema sync failed: {e}')
        raise


def alert_to_dict(a):
    return {
        'id': a.alert_id,
        'type': a.alert_type,
        'message': a.message,
        'issuedBy': a.issued_by,
        'issuedAt': a.created_at.isoformat() if a.created_at else None,
    }


def radio_to_dict(r):
    return {
        'id': r.log_id,
        'unit': r.unit,
        'channel': r.channel,
        'message': r.message,
        'timestamp': r.created_at.isoformat() if r.created_at else None,
    }


def status_to_dict(s):
    return {
        'cityStatus': s.city_status,
        'playerCount': s.player_count,
        'maxPlayers': s.max_players,
        'customMessage': s.custom_message,
        'lastUpdated': s.last_updated.isoformat() if s.last_updated else None,
    }



PENDING_SENTENCE = 'Pending Court Hearing'
PENDING_FINE = 'Pending'
AUTO_HEARING_NOTE = 'Automatically scheduled after arrest booking.'


def _default_hearing_time():
    """Return a deterministic default arraignment time for arrest automation."""
    scheduled = datetime.utcnow() + timedelta(days=1)
    scheduled = scheduled.replace(hour=9, minute=0, second=0, microsecond=0)
    return scheduled.isoformat()


def _parse_fine_amount(value):
    if value in (None, ''):
        return None
    try:
        return float(str(value).replace('$', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def _normalize_name(value):
    return ' '.join(str(value or '').strip().lower().split())


def _civilian_full_name(civilian):
    return f'{civilian.first_name or ""} {civilian.last_name or ""}'.strip()


def _same_person_name_filter(column, civilian):
    full_name = _civilian_full_name(civilian)
    first = (civilian.first_name or '').strip()
    last = (civilian.last_name or '').strip()
    filters = []
    if full_name:
        filters.append(column == full_name)
        filters.append(sqlalchemy.func.lower(column) == full_name.lower())
    if first and last:
        filters.append(sqlalchemy.and_(column.ilike(f'%{first}%'), column.ilike(f'%{last}%')))
    return sqlalchemy.or_(*filters) if filters else sqlalchemy.false()


def _civilian_related_history_exists(civilian):
    """Fast profile-card check for any criminal/CAD history tied to a civilian."""
    if not civilian:
        return False

    civilian_id = civilian.civilian_id or ''
    name_filter = _same_person_name_filter
    full_name = _civilian_full_name(civilian)
    first = (civilian.first_name or '').strip()
    last = (civilian.last_name or '').strip()

    community_id = civilian.community_id or get_current_community_id()
    arrest_query = scoped_query(Arrest, community_id).filter(sqlalchemy.or_(
        Arrest.civilian_id == civilian_id,
        name_filter(Arrest.suspect_name, civilian),
    ))
    if arrest_query.first():
        return True

    arrest_ids = [row[0] for row in arrest_query.with_entities(Arrest.arrest_id).all() if row[0]]
    if scoped_query(JailBooking, community_id).filter(sqlalchemy.or_(
        JailBooking.civilian_id == civilian_id,
        JailBooking.arrest_id.in_(arrest_ids) if arrest_ids else sqlalchemy.false(),
        name_filter(JailBooking.suspect_name, civilian),
    )).first():
        return True
    if scoped_query(Hearing, community_id).filter(sqlalchemy.or_(
        Hearing.civilian_id == civilian_id,
        Hearing.arrest_id.in_(arrest_ids) if arrest_ids else sqlalchemy.false(),
        name_filter(Hearing.suspect_name, civilian),
    )).first():
        return True
    if scoped_query(Warrant, community_id).filter(sqlalchemy.or_(
        Warrant.civilian_id == civilian_id,
        name_filter(Warrant.warrant_name, civilian),
    )).first():
        return True
    if scoped_query(Citation, community_id).filter(Citation.civilian_id == civilian_id).first():
        return True

    traffic_filters = []
    if full_name:
        traffic_filters.extend([TrafficStop.driver_name == full_name, sqlalchemy.func.lower(TrafficStop.driver_name) == full_name.lower()])
    if first and last:
        traffic_filters.append(sqlalchemy.and_(TrafficStop.driver_name.ilike(f'%{first}%'), TrafficStop.driver_name.ilike(f'%{last}%')))
    if civilian.plate_number:
        traffic_filters.append(TrafficStop.plate.ilike(civilian.plate_number))
    return scoped_query(TrafficStop, community_id).filter(sqlalchemy.or_(*traffic_filters) if traffic_filters else sqlalchemy.false()).first() is not None


def _find_civilian_for_arrest(civilian_id='', suspect_name=''):
    """Resolve an arrest to a civilian by explicit ID first, then case-insensitive full name."""
    civilian_id = (civilian_id or '').strip()
    if civilian_id:
        match = scoped_query(Civilian).filter(Civilian.civilian_id == civilian_id).first()
        if match:
            return match

    normalized_name = _normalize_name(suspect_name)
    if not normalized_name:
        return None

    for civilian in scoped_query(Civilian).all():
        full_name = _normalize_name(f'{civilian.first_name or ""} {civilian.last_name or ""}')
        if full_name == normalized_name:
            return civilian
    return None


def _apply_arrest_payload(arrest, data):
    civilian = _find_civilian_for_arrest(
        data.get('civilianId') or data.get('civilian_id') or arrest.civilian_id,
        data.get('suspectName') or data.get('suspect_name') or arrest.suspect_name,
    )
    arrest.civilian_id = civilian.civilian_id if civilian else (data.get('civilianId') or data.get('civilian_id') or arrest.civilian_id or '')
    arrest.suspect_name = (data.get('suspectName') or data.get('suspect_name') or arrest.suspect_name or '').strip()
    arrest.charges = (data.get('charges') or arrest.charges or '').strip()
    arrest.arresting_officer = (data.get('arrestingOfficer') or data.get('arresting_officer') or arrest.arresting_officer or '').strip()
    arrest.arrest_location = (data.get('arrestLocation') or data.get('arrest_location') or arrest.arrest_location or '').strip()
    arrest.evidence_attached = (data.get('evidenceAttached') or data.get('evidence_attached') or arrest.evidence_attached or '').strip()
    arrest.penalty = (data.get('penalty') or arrest.penalty or '').strip()
    arrest.report_notes = (data.get('reportNotes') or data.get('report_notes') or arrest.report_notes or '').strip()
    arrest.narrative = (data.get('narrative') or arrest.narrative or '').strip()
    arrest.status = (data.get('status') or arrest.status or 'Active').strip()
    arrest.updated_at = datetime.utcnow()
    return civilian


def _compose_arrest_notes(arrest):
    summary = (arrest.report_notes or arrest.narrative or '').strip()
    if not summary:
        return 'Automatically booked after arrest submission.'
    return f'Automatically booked after arrest submission. Arrest summary: {summary}'


def _ensure_arrest_custody_and_hearing(arrest):
    """Create the linked custody booking and court hearing for a new arrest once."""
    if not arrest or not arrest.arrest_id:
        return None, None, None

    if not arrest.civilian_id:
        civilian = _find_civilian_for_arrest('', arrest.suspect_name)
        if civilian:
            arrest.civilian_id = civilian.civilian_id
            logger.info(f'Arrest {arrest.arrest_id} linked to civilian {civilian.civilian_id} by suspect name')

    community_id = arrest.community_id or get_current_community_id()
    inmate = scoped_query(Inmate, community_id).filter_by(arrest_id=arrest.arrest_id).first()
    if inmate is None:
        ts = int(datetime.utcnow().timestamp() * 1000)
        inmate = Inmate(
            community_id=community_id,
            inmate_id=f'inmate-{ts}-{secrets.token_hex(4)}',
            civilian_id=arrest.civilian_id or '',
            suspect_name=arrest.suspect_name or '',
            charges=arrest.charges or '',
            penalty=PENDING_SENTENCE,
            cell='',
            booked_by=arrest.arresting_officer or 'Unknown',
            arrest_id=arrest.arrest_id,
            estimated_release='',
            notes=f'{_compose_arrest_notes(arrest)} Fine: {PENDING_FINE}. Court Hearing: Scheduled.',
            status='In Custody',
            booked_at=datetime.utcnow(),
        )
        db.session.add(inmate)
        logger.info(f'Jail tracker inmate auto-created for arrest {arrest.arrest_id}')
    else:
        if not inmate.civilian_id and arrest.civilian_id:
            inmate.civilian_id = arrest.civilian_id
        if not inmate.suspect_name and arrest.suspect_name:
            inmate.suspect_name = arrest.suspect_name
        if not inmate.charges and arrest.charges:
            inmate.charges = arrest.charges
        logger.info(f'Duplicate inmate booking prevented for arrest {arrest.arrest_id}')

    booking = scoped_query(JailBooking, community_id).filter_by(arrest_id=arrest.arrest_id).first()
    if booking is None:
        ts = int(datetime.utcnow().timestamp() * 1000)
        booking = JailBooking(
            community_id=community_id,
            booking_id=f'booking-{ts}-{secrets.token_hex(4)}',
            civilian_id=arrest.civilian_id or '',
            arrest_id=arrest.arrest_id,
            suspect_name=arrest.suspect_name or '',
            charges=arrest.charges or '',
            booking_officer=arrest.arresting_officer or 'Unknown',
            cell_assignment='',
            bond_amount=None,
            sentence_length=PENDING_SENTENCE,
            status='In Custody',
            notes=f'{_compose_arrest_notes(arrest)} Fine: {PENDING_FINE}. Court Hearing: Scheduled.',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(booking)
        logger.info(f'Jail booking auto-created for arrest {arrest.arrest_id}')
    else:
        if not booking.civilian_id and arrest.civilian_id:
            booking.civilian_id = arrest.civilian_id
        if not booking.suspect_name and arrest.suspect_name:
            booking.suspect_name = arrest.suspect_name
        if not booking.charges and arrest.charges:
            booking.charges = arrest.charges
        if not booking.arrest_id and arrest.arrest_id:
            booking.arrest_id = arrest.arrest_id
        if booking.bond_amount == 'Pending':
            booking.bond_amount = None
        booking.updated_at = datetime.utcnow()
        logger.info(f'Duplicate jail booking prevented for arrest {arrest.arrest_id}')

    hearing = scoped_query(Hearing, community_id).filter_by(arrest_id=arrest.arrest_id).first()
    if hearing is None:
        ts = int(datetime.utcnow().timestamp() * 1000)
        hearing = Hearing(
            community_id=community_id,
            hearing_id=f'hearing-{ts}-{secrets.token_hex(5)}',
            civilian_id=arrest.civilian_id or '',
            suspect_name=arrest.suspect_name or '',
            charges=arrest.charges or '',
            hearing_type='Arraignment',
            scheduled_at=_default_hearing_time(),
            judge='',
            notes=AUTO_HEARING_NOTE,
            arrest_id=arrest.arrest_id,
            filing_officer=arrest.arresting_officer or 'Unknown',
            outcome='',
            sentence_length='',
            fine_amount='',
            outcome_notes='',
            status='Scheduled',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(hearing)
        logger.info(f'Court hearing auto-created for arrest {arrest.arrest_id}')
    else:
        if not hearing.civilian_id and arrest.civilian_id:
            hearing.civilian_id = arrest.civilian_id
        if not hearing.suspect_name and arrest.suspect_name:
            hearing.suspect_name = arrest.suspect_name
        if not hearing.charges and arrest.charges:
            hearing.charges = arrest.charges
        if not hearing.arrest_id and arrest.arrest_id:
            hearing.arrest_id = arrest.arrest_id
        hearing.updated_at = datetime.utcnow()
        logger.info(f'Duplicate court hearing prevented for arrest {arrest.arrest_id}')

    return inmate, booking, hearing

def _sync_custody_from_completed_hearing(hearing):
    """Apply a completed/continued hearing result to linked jail records."""
    if not hearing or not hearing.arrest_id:
        return
    normalized = (hearing.outcome or '').strip().lower()
    completed = (hearing.status or '').strip().lower() in {'completed', 'dismissed', 'continued'}
    if not completed and normalized not in {'dismissed', 'not guilty', 'continued'}:
        return

    community_id = hearing.community_id or get_current_community_id()
    booking = scoped_query(JailBooking, community_id).filter_by(arrest_id=hearing.arrest_id).first()
    inmate = scoped_query(Inmate, community_id).filter_by(arrest_id=hearing.arrest_id).first()
    arrest = scoped_query(Arrest, community_id).filter_by(arrest_id=hearing.arrest_id).first()

    if normalized in {'dismissed', 'not guilty'}:
        if booking:
            booking.status = 'Released'
            booking.sentence_length = 'Dismissed'
            booking.bond_amount = None
            booking.release_date = datetime.utcnow()
            booking.release_reason = f'Hearing outcome: {hearing.outcome}'
            booking.updated_at = datetime.utcnow()
        if inmate:
            inmate.status = 'Released'
            inmate.penalty = 'Dismissed'
            inmate.released_at = datetime.utcnow()
            inmate.released_by = 'Court System'
            inmate.release_reason = f'Hearing outcome: {hearing.outcome}'
            inmate.updated_at = datetime.utcnow()
        if arrest:
            arrest.status = 'Closed - Released'
            arrest.penalty = hearing.outcome or 'Dismissed'
            arrest.updated_at = datetime.utcnow()
        logger.info(f'Court completion released custody for arrest {hearing.arrest_id}')
        return

    if normalized == 'continued':
        if booking:
            booking.status = 'In Custody'
            booking.sentence_length = PENDING_SENTENCE
            booking.bond_amount = None
            booking.updated_at = datetime.utcnow()
        if inmate:
            inmate.status = 'In Custody'
            inmate.penalty = PENDING_SENTENCE
            inmate.updated_at = datetime.utcnow()
        if arrest:
            arrest.status = 'Continued'
            arrest.updated_at = datetime.utcnow()
        logger.info(f'Court completion continued pending custody for arrest {hearing.arrest_id}')
        return

    if normalized in {'guilty', 'sentenced', 'no contest'} or completed:
        sentence = (hearing.sentence_length or '').strip() or PENDING_SENTENCE
        fine = _parse_fine_amount(hearing.fine_amount)
        if booking:
            booking.status = 'In Custody'
            booking.sentence_length = sentence
            booking.bond_amount = fine
            booking.updated_at = datetime.utcnow()
        if inmate:
            inmate.status = 'In Custody'
            inmate.penalty = sentence
            inmate.updated_at = datetime.utcnow()
        if arrest:
            arrest.status = 'Sentenced'
            arrest.penalty = sentence
            arrest.updated_at = datetime.utcnow()
        logger.info(f'Court completion applied sentence for arrest {hearing.arrest_id}')

def inmate_to_dict(i):
    return {
        'id': i.inmate_id,
        'civilianId': i.civilian_id or '',
        'suspectName': i.suspect_name,
        'charges': i.charges or '',
        'penalty': i.penalty or '',
        'sentenceLength': i.penalty or '',
        'fineAmount': PENDING_FINE if (i.penalty == PENDING_SENTENCE and i.status == 'In Custody') else '',
        'courtHearingStatus': 'Scheduled' if (i.penalty == PENDING_SENTENCE and i.status == 'In Custody') else '',
        'cell': i.cell or '',
        'bookedBy': i.booked_by,
        'arrestId': i.arrest_id or '',
        'estimatedRelease': i.estimated_release or '',
        'notes': i.notes or '',
        'status': i.status,
        'bookedAt': (i.booked_at.isoformat() + 'Z') if i.booked_at else None,
        'releasedAt': (i.released_at.isoformat() + 'Z') if i.released_at else None,
        'releasedBy': i.released_by or '',
        'releaseReason': i.release_reason or '',
        'updatedAt': (i.updated_at.isoformat() + 'Z') if i.updated_at else None,
    }


def _display_hearing_type(value):
    hearing_type = (value or 'Arraignment').strip()
    if hearing_type.lower() == 'arrigment':
        return 'Arraignment'
    return hearing_type


def hearing_to_dict(h):
    return {
        'id': h.hearing_id,
        'civilianId': h.civilian_id or '',
        'suspectName': h.suspect_name,
        'charges': h.charges or '',
        'hearingType': _display_hearing_type(h.hearing_type),
        'scheduledAt': h.scheduled_at or '',
        'judge': h.judge or '',
        'notes': h.notes or '',
        'arrestId': h.arrest_id or '',
        'filingOfficer': h.filing_officer or '',
        'outcome': h.outcome or '',
        'sentenceLength': h.sentence_length or '',
        'fineAmount': h.fine_amount or '',
        'outcomeNotes': h.outcome_notes or '',
        'status': h.status,
        'createdAt': (h.created_at.isoformat() + 'Z') if h.created_at else None,
        'updatedAt': (h.updated_at.isoformat() + 'Z') if h.updated_at else None,
    }


def civilian_to_dict(c):
    return _civilian_response(c)


def vehicle_to_dict(v):
    return {
        'plate': v.plate,
        'ownerName': v.owner_name or '',
        'model': v.model or '',
        'color': v.color or '',
        'registrationStatus': v.registration_status or 'Valid',
    }


def license_to_dict(l):
    return {
        'id': l.license_id,
        'ownerName': l.owner_name or '',
        'licenseType': l.license_type or '',
        'status': l.status or 'Valid',
        'issuedDate': l.issued_date or '',
        'expiryDate': l.expiry_date or '',
        'notes': l.notes or '',
    }


def _warrant_value(w, primary, legacy=None, default=''):
    value = getattr(w, primary, None)
    if value in (None, '') and legacy:
        value = getattr(w, legacy, None)
    return default if value in (None, '') else value


def _warrant_status(w):
    return _warrant_value(w, 'status', 'warrant_status', 'Active') or 'Active'


def _set_warrant_status(warrant, new_status, *, touch=True):
    status = new_status or 'Active'
    warrant.status = status
    warrant.warrant_status = status
    if touch:
        warrant.updated_at = datetime.utcnow()
    return status


def _warrant_pdf_download_url(w):
    if getattr(w, 'pdf_attachment_id', None):
        return f'/api/cad/warrants/{w.warrant_id}/download-pdf'
    return None


def warrant_to_dict(w):
    warrant_type = _warrant_value(w, 'warrant_type', default='Arrest Warrant') or 'Arrest Warrant'
    warrant_number = _warrant_value(w, 'warrant_number', 'warrant_id')
    subject_name = _warrant_value(w, 'subject_name', 'warrant_name')
    charges_or_basis = _warrant_value(w, 'charges_or_basis', 'warrant_charges')
    probable_cause = _warrant_value(w, 'probable_cause', 'justification') or _warrant_value(w, 'warrant_notes')
    status = _warrant_status(w)
    payload = {
        'id': w.warrant_id,
        'warrant_id': w.warrant_id,
        'warrant_number': warrant_number,
        'warrant_type': warrant_type,
        'subject_name': subject_name,
        'subject_dob': _warrant_value(w, 'subject_dob'),
        'subject_address': _warrant_value(w, 'subject_address'),
        'charges_or_basis': charges_or_basis,
        'probable_cause': probable_cause,
        'issuing_agency': _warrant_value(w, 'issuing_agency', 'warrant_issuer'),
        'judge_or_authority': _warrant_value(w, 'judge_or_authority'),
        'search_location': _warrant_value(w, 'search_location'),
        'items_to_seize': _warrant_value(w, 'items_to_seize'),
        'court_case_number': _warrant_value(w, 'court_case_number'),
        'bench_failure_reason': _warrant_value(w, 'bench_failure_reason'),
        'administrative_basis': _warrant_value(w, 'administrative_basis'),
        'inspection_scope': _warrant_value(w, 'inspection_scope'),
        'originating_jurisdiction': _warrant_value(w, 'originating_jurisdiction'),
        'extradition_location': _warrant_value(w, 'extradition_location'),
        'fugitive_last_known_location': _warrant_value(w, 'fugitive_last_known_location'),
        'alias_names': _warrant_value(w, 'alias_names'),
        'execution_instructions': _warrant_value(w, 'execution_instructions'),
        'expiration_date': _warrant_value(w, 'expiration_date'),
        'status': status,
        'pdf_generated_at': w.pdf_generated_at.isoformat() if getattr(w, 'pdf_generated_at', None) else None,
        'pdf_download_url': _warrant_pdf_download_url(w),
        'created_at': w.created_at.isoformat() if w.created_at else None,
        'updated_at': w.updated_at.isoformat() if getattr(w, 'updated_at', None) else None,
        # Legacy frontend aliases.
        'warrantName': subject_name,
        'warrantCharges': charges_or_basis,
        'warrantIssuer': _warrant_value(w, 'issuing_agency', 'warrant_issuer'),
        'warrantNotes': probable_cause,
        'warrantStatus': status,
        'expirationDate': _warrant_value(w, 'expiration_date'),
        'justification': _warrant_value(w, 'justification') or probable_cause,
        'suspectName': subject_name,
        'charges': charges_or_basis,
        'issuer': _warrant_value(w, 'issuing_agency', 'warrant_issuer'),
        'expiration': _warrant_value(w, 'expiration_date'),
        'notes': probable_cause,
    }
    return payload


def arrest_to_dict(a):
    return {
        'id': a.arrest_id,
        'civilianId': a.civilian_id or '',
        'suspectName': a.suspect_name or '',
        'charges': a.charges or '',
        'arrestingOfficer': a.arresting_officer or '',
        'arrestLocation': a.arrest_location or '',
        'evidenceAttached': a.evidence_attached or '',
        'penalty': a.penalty or '',
        'reportNotes': a.report_notes or '',
        'narrative': a.narrative or '',
        'status': a.status or 'Active',
        'createdAt': a.created_at.isoformat() if a.created_at else None,
    }


def incident_to_dict(i):
    return {
        'id': i.incident_id,
        'incidentType': i.incident_type or '',
        'location': i.location or '',
        'description': i.description or '',
        'officersInvolved': i.officers_involved or '',
        'suspects': i.suspects or '',
        'status': i.status or 'Open',
        'priority': i.priority or 'Medium',
        'notes': i.notes or '',
        'createdAt': i.created_at.isoformat() if i.created_at else None,
    }


def evidence_to_dict(e):
    return {
        'id': e.evidence_id,
        'caseNumber': e.case_number or '',
        'evidenceDescription': e.evidence_description or '',
        'collectedBy': e.collected_by or '',
        'locationFound': e.location_found or '',
        'status': e.status or 'Active',
        'notes': e.notes or '',
        'createdAt': e.created_at.isoformat() if e.created_at else None,
    }


def traffic_stop_to_dict(t):
    return {
        'id': t.stop_id,
        'driverName': t.driver_name or '',
        'trafficPlate': t.plate or '',
        'plate': t.plate or '',
        'trafficReason': t.reason or '',
        'reason': t.reason or '',
        'trafficOutcome': t.outcome or '',
        'outcome': t.outcome or '',
        'officer': t.officer or '',
        'location': t.location or '',
        'notes': t.notes or '',
        'createdAt': t.created_at.isoformat() if t.created_at else None,
    }

def citation_to_dict(c):
    return {
        'id': c.citation_id,
        'civilianId': c.civilian_id or '',
        'issuingOfficer': c.issuing_officer or '',
        'violation': c.violation or '',
        'location': c.location or '',
        'fineAmount': c.fine_amount,
        'status': c.status or 'Issued',
        'notes': c.notes or '',
        'createdAt': c.created_at.isoformat() if c.created_at else None,
    }


def jail_booking_to_dict(j):
    return {
        'id': j.booking_id,
        'civilianId': j.civilian_id or '',
        'arrestId': j.arrest_id or '',
        'suspectName': j.suspect_name or '',
        'charges': j.charges or '',
        'bookingOfficer': j.booking_officer or '',
        'cellAssignment': j.cell_assignment or '',
        'bondAmount': PENDING_FINE if j.sentence_length == PENDING_SENTENCE else (j.bond_amount if j.bond_amount is not None else ''),
        'fineAmount': PENDING_FINE if j.sentence_length == PENDING_SENTENCE else (j.bond_amount if j.bond_amount is not None else ''),
        'sentenceLength': j.sentence_length or '',
        'status': j.status or 'Booked',
        'releaseDate': j.release_date.isoformat() if j.release_date else None,
        'releasedBy': j.released_by or '',
        'releaseReason': j.release_reason or '',
        'notes': j.notes or '',
        'createdAt': j.created_at.isoformat() if j.created_at else None,
    }



def call911_to_dict(c):
    return {
        'id': c.call_id,
        'callerName': c.caller_name or '',
        'location': c.location or '',
        'description': c.description or '',
        'incidentType': c.incident_type or '',
        'priority': c.priority or 'Medium',
        'assignedUnit': c.assigned_unit or '',
        'status': c.status or 'New',
        'dispatchNotes': c.dispatch_notes or '',
        'createdAt': c.created_at.isoformat() if c.created_at else None,
    }


def activity_log_to_dict(a):
    return {
        'id': a.log_id,
        'action': a.action or '',
        'officer': a.officer or '',
        'details': a.details or '',
        'timestamp': a.created_at.isoformat() if a.created_at else None,
    }



# ---------------------------------------------------------------------------
# Civilian PostgreSQL source-of-truth helpers
# ---------------------------------------------------------------------------

def _pick(data, *keys, default=''):
    """Return the first present, non-None payload value from frontend or DB-style keys."""
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)
    return default


def _parse_date(value):
    if not value:
        return None
    if hasattr(value, 'isoformat') and not isinstance(value, str):
        return value
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


def _civilian_from_payload(data):
    """Map Civilian Registration form fields onto PostgreSQL Civilian columns."""
    vehicle_year = _pick(data, 'vehicleYear', 'vehicle_year', default=None)
    if vehicle_year in ('', None):
        vehicle_year = None
    else:
        try:
            vehicle_year = int(vehicle_year)
        except (TypeError, ValueError):
            vehicle_year = None

    return {
        'first_name': str(_pick(data, 'firstName', 'first_name')).strip(),
        'last_name': str(_pick(data, 'lastName', 'last_name')).strip(),
        'date_of_birth': _parse_date(_pick(data, 'dob', 'date_of_birth', default=None)),
        'gender': _pick(data, 'gender'),
        'phone_number': _pick(data, 'phone', 'phone_number'),
        'address': _pick(data, 'address'),
        'occupation': _pick(data, 'occupation'),
        'gang_affiliation': _pick(data, 'faction', 'gang_affiliation', default='None') or 'None',
        'emergency_contact_name': _pick(data, 'emergencyName', 'emergency_contact_name'),
        'emergency_contact_phone': _pick(data, 'emergencyPhone', 'emergency_contact_phone'),
        'driver_license_status': _pick(data, 'driverLicense', 'driver_license_status', default='Valid') or 'Valid',
        'firearm_license_status': _pick(data, 'firearmLicense', 'firearm_license_status', default='None') or 'None',
        'business_license_status': _pick(data, 'businessLicense', 'business_license_status', default='None') or 'None',
        'vehicle_make': _pick(data, 'vehicleMake', 'vehicle_make'),
        'vehicle_model': _pick(data, 'vehicleModel', 'vehicle_model'),
        'vehicle_year': vehicle_year,
        'vehicle_color': _pick(data, 'vehicleColor', 'vehicle_color'),
        'plate_number': _pick(data, 'plate', 'plate_number'),
        'insurance_status': _pick(data, 'insurance', 'insurance_status', default='Valid') or 'Valid',
        'criminal_background_notes': _pick(data, 'background', 'criminal_background_notes'),
        'character_backstory': _pick(data, 'backstory', 'character_backstory'),
    }


def _civilian_response(c):
    base = c.to_dict()
    base.update({
        'id': c.civilian_id,
        'name': f'{c.first_name or ""} {c.last_name or ""}'.strip(),
        'firstName': c.first_name or '',
        'lastName': c.last_name or '',
        'dob': c.date_of_birth.isoformat() if c.date_of_birth else '',
        'phone': c.phone_number or '',
        'faction': c.gang_affiliation or 'None',
        'emergencyName': c.emergency_contact_name or '',
        'emergencyPhone': c.emergency_contact_phone or '',
        'driverLicense': c.driver_license_status or 'Valid',
        'firearmLicense': c.firearm_license_status or 'None',
        'businessLicense': c.business_license_status or 'None',
        'vehicleMake': c.vehicle_make or '',
        'vehicleModel': c.vehicle_model or '',
        'vehicleYear': c.vehicle_year,
        'vehicleColor': c.vehicle_color or '',
        'plate': c.plate_number or '',
        'insurance': c.insurance_status or 'Valid',
        'background': c.criminal_background_notes or '',
        'backstory': c.character_backstory or '',
        'hasCriminalHistory': _civilian_related_history_exists(c),
    })
    return base


def _civilian_search_query(query, name=None, dob=None, community_id=None):
    q = (query or '').strip()
    name = (name or '').strip()
    dob = (dob or '').strip()
    db_query = scoped_query(Civilian, community_id)

    if name:
        db_query = db_query.filter(_civilian_name_filter(name))
    if dob:
        parsed_dob = _parse_date(dob)
        if parsed_dob:
            db_query = db_query.filter(Civilian.date_of_birth == parsed_dob)
    if q:
        filters = [
            Civilian.first_name.ilike(f'%{q}%'),
            Civilian.last_name.ilike(f'%{q}%'),
            Civilian.civilian_id.ilike(f'%{q}%'),
            Civilian.phone_number.ilike(f'%{q}%'),
            Civilian.plate_number.ilike(f'%{q}%'),
        ]
        parsed_q_dob = _parse_date(q)
        if parsed_q_dob:
            filters.append(Civilian.date_of_birth == parsed_q_dob)
        filters.append(_civilian_name_filter(q))
        db_query = db_query.filter(sqlalchemy.or_(*filters))

    return db_query


# ---------------------------------------------------------------------------
# Civilian Dashboard (civilian-safe, owner-scoped portal)
# ---------------------------------------------------------------------------

CIVILIAN_UNPAID_STATUSES = {'issued', 'unpaid', 'pending', 'open', 'contested'}


def _require_civilian_dashboard_context():
    user_id = session.get('user_id')
    if not isinstance(user_id, int):
        return None, None, (jsonify({'success': False, 'error': 'Authentication required'}), 401)
    community = resolve_active_community()
    if not community or not community.get('community_id'):
        return None, None, (jsonify({'success': False, 'error': 'Community context required'}), 400)
    return user_id, community, None


def _civilian_dashboard_profile(civilian, community):
    return {
        'id': civilian.civilian_id,
        'civilian_id': civilian.civilian_id,
        'name': _civilian_full_name(civilian),
        'date_of_birth': civilian.date_of_birth.isoformat() if civilian.date_of_birth else '',
        'phone': civilian.phone_number or '',
        'address': civilian.address or '',
        'occupation': civilian.occupation or '',
        'license_status': civilian.driver_license_status or 'Valid',
        'created_at': civilian.created_at.isoformat() if civilian.created_at else None,
        'community_name': community.get('name') or '',
    }


def _civilian_dashboard_profiles(civilians, community):
    return [_civilian_dashboard_profile(c, community) for c in civilians]


def _civilian_exact_name(civilian):
    return _normalize_name(_civilian_full_name(civilian))


def _dashboard_vehicle_rows(civilian, community_id):
    rows = scoped_query(Vehicle, community_id).filter_by(owner_civilian_id=civilian.civilian_id).order_by(Vehicle.created_at.desc()).all()
    vehicles = [{
        'plate': v.plate or '',
        'make': v.make or '',
        'model': v.model or '',
        'color': v.color or '',
        'registration_status': v.registration_status or 'Valid',
        'insurance_status': v.insurance_status or 'Valid',
        'created_at': v.created_at.isoformat() if v.created_at else None,
    } for v in rows]
    if civilian.plate_number and not any((v.get('plate') or '').lower() == civilian.plate_number.lower() for v in vehicles):
        vehicles.insert(0, {
            'plate': civilian.plate_number or '',
            'make': civilian.vehicle_make or '',
            'model': civilian.vehicle_model or '',
            'color': civilian.vehicle_color or '',
            'registration_status': 'Valid',
            'insurance_status': civilian.insurance_status or 'Valid',
            'created_at': civilian.created_at.isoformat() if civilian.created_at else None,
        })
    return vehicles


def _dashboard_license_rows(civilian, community_id):
    full_name = _civilian_exact_name(civilian)
    licenses = []
    if full_name:
        rows = scoped_query(License, community_id).filter(func.lower(License.owner_name) == full_name).order_by(License.created_at.desc()).all()
        licenses = [{
            'license_type': l.license_type or '',
            'status': l.status or 'Valid',
            'expiration': l.expiry_date or '',
            'restrictions': '',
            'issued_date': l.issued_date or '',
        } for l in rows]
    built_ins = [
        ('Driver License', civilian.driver_license_status or 'Valid'),
        ('Firearm License', civilian.firearm_license_status or 'None'),
        ('Business License', civilian.business_license_status or 'None'),
    ]
    for license_type, status in built_ins:
        if status and status.lower() != 'none' and not any((l.get('license_type') or '').lower() == license_type.lower() for l in licenses):
            licenses.append({'license_type': license_type, 'status': status, 'expiration': '', 'restrictions': '', 'issued_date': ''})
    return licenses


def _dashboard_citation_rows(civilian, community_id):
    return [{
        'citation_number': c.citation_id,
        'violation': c.violation or '',
        'amount': c.fine_amount,
        'fine': c.fine_amount,
        'status': c.status or 'Issued',
        'issued_date': c.created_at.isoformat() if c.created_at else None,
        'court_required': False,
    } for c in scoped_query(Citation, community_id).filter_by(civilian_id=civilian.civilian_id).order_by(Citation.created_at.desc()).all()]


def _dashboard_fine_rows(citations):
    return [{
        'fine_number': c.get('citation_number') or '',
        'citation_number': c.get('citation_number') or '',
        'amount': c.get('amount'),
        'due_date': '',
        'status': c.get('status') or 'Issued',
        'linked_citation': c.get('citation_number') or '',
    } for c in citations if c.get('amount') not in (None, '')]


def _dashboard_arrest_rows(civilian, community_id):
    return [{
        'arrest_date': a.created_at.isoformat() if a.created_at else None,
        'charges': a.charges or '',
        'disposition': a.status or 'Active',
        'status': a.status or 'Active',
        'jail_time': a.penalty or '',
        'fine': '',
        'public_notes': '',
    } for a in scoped_query(Arrest, community_id).filter_by(civilian_id=civilian.civilian_id).order_by(Arrest.created_at.desc()).all()]


def _dashboard_jail_rows(civilian, community_id):
    bookings = [{
        'booking_id': j.booking_id,
        'arrest_id': j.arrest_id or '',
        'booking_date': j.created_at.isoformat() if j.created_at else None,
        'charges': j.charges or '',
        'status': j.status or 'Booked',
        'jail_time': j.sentence_length or '',
        'fine': PENDING_FINE if j.sentence_length == PENDING_SENTENCE else (j.bond_amount if j.bond_amount is not None else ''),
        'release_date': j.release_date.isoformat() if j.release_date else None,
    } for j in scoped_query(JailBooking, community_id).filter_by(civilian_id=civilian.civilian_id).order_by(JailBooking.created_at.desc()).all()]
    inmates = [{
        'booking_id': i.inmate_id,
        'arrest_id': i.arrest_id or '',
        'booking_date': i.booked_at.isoformat() if i.booked_at else None,
        'charges': i.charges or '',
        'status': i.status or 'In Custody',
        'jail_time': i.penalty or '',
        'fine': '',
        'release_date': i.released_at.isoformat() if i.released_at else None,
    } for i in scoped_query(Inmate, community_id).filter_by(civilian_id=civilian.civilian_id).order_by(Inmate.booked_at.desc()).all()]
    return bookings + inmates


def _is_served_warrant_for_civilian(warrant):
    status_values = (
        str(getattr(warrant, 'status', '') or '').strip().lower(),
        str(getattr(warrant, 'warrant_status', '') or '').strip().lower(),
    )
    return any(status == 'served' for status in status_values)


def _warrant_matches_civilian(warrant, civilian):
    if (getattr(warrant, 'civilian_id', None) or '').strip() == civilian.civilian_id:
        return True
    subject = _normalize_name(_warrant_value(warrant, 'subject_name', 'warrant_name'))
    if not subject or subject != _normalize_name(_civilian_full_name(civilian)):
        return False
    subject_dob = str(_warrant_value(warrant, 'subject_dob') or '').strip()
    civilian_dob = civilian.date_of_birth.isoformat() if civilian.date_of_birth else ''
    if subject_dob and civilian_dob and subject_dob == civilian_dob:
        return True
    subject_address = _normalize_name(_warrant_value(warrant, 'subject_address'))
    civilian_address = _normalize_name(civilian.address)
    return bool(subject_address and civilian_address and subject_address == civilian_address)


def _dashboard_served_warrant_rows(civilian, community_id):
    direct = scoped_query(Warrant, community_id).filter_by(civilian_id=civilian.civilian_id).all()
    named = scoped_query(Warrant, community_id).filter(or_(Warrant.subject_name.ilike(_civilian_full_name(civilian)), Warrant.warrant_name.ilike(_civilian_full_name(civilian)))).all()
    seen = {}
    for warrant in direct + named:
        if _is_served_warrant_for_civilian(warrant) and _warrant_matches_civilian(warrant, civilian):
            seen[warrant.warrant_id] = warrant
    warrants = sorted(seen.values(), key=lambda w: w.created_at or datetime.min, reverse=True)
    return [{
        'warrant_number': _warrant_value(w, 'warrant_number', 'warrant_id'),
        'warrant_type': _warrant_value(w, 'warrant_type', default='Arrest Warrant') or 'Arrest Warrant',
        'status': 'Served',
        'served_date': w.updated_at.isoformat() if getattr(w, 'updated_at', None) else None,
        'charges_or_basis': _warrant_value(w, 'charges_or_basis', 'warrant_charges'),
        'court_case_number': _warrant_value(w, 'court_case_number'),
        'issuing_agency': _warrant_value(w, 'issuing_agency', 'warrant_issuer'),
        'created_at': w.created_at.isoformat() if w.created_at else None,
    } for w in warrants if _is_served_warrant_for_civilian(w)]


def _dashboard_court_rows(civilian, community_id):
    hearings = [{
        'hearing_date': h.scheduled_at or '',
        'courtroom': '',
        'case_number': h.hearing_id,
        'status': h.status or 'Scheduled',
        'outcome': h.outcome or '',
        'hearing_type': _display_hearing_type(h.hearing_type),
    } for h in scoped_query(Hearing, community_id).filter_by(civilian_id=civilian.civilian_id).order_by(Hearing.created_at.desc()).all()]
    case_rows = [{
        'hearing_date': cf.court_date.isoformat() if cf.court_date else '',
        'courtroom': '',
        'case_number': cf.case_number or cf.case_id,
        'status': cf.status or 'open',
        'outcome': cf.outcome or '',
        'hearing_type': cf.case_type or 'Court',
    } for cf in scoped_query(CaseFile, community_id).filter_by(defendant_civilian_id=civilian.civilian_id).filter(CaseFile.court_date.isnot(None)).order_by(CaseFile.court_date.desc()).all()]
    return hearings + case_rows


def _dashboard_complaint_rows(civilian, community_id, user):
    identifiers = {_normalize_name(getattr(user, 'username', '') or ''), _normalize_name(getattr(user, 'email', '') or '')}
    identifiers.discard('')
    if not identifiers:
        return []
    rows = scoped_query(Complaint, community_id).filter(func.lower(Complaint.complaint_discord).in_(identifiers)).order_by(Complaint.submitted_at.desc()).all()
    return [{
        'complaint_id': c.complaint_id,
        'category': c.complaint_type or '',
        'status': c.status or 'Open',
        'submitted_date': c.submitted_at.isoformat() if c.submitted_at else None,
    } for c in rows]


def _is_upcoming_court(row):
    status = str(row.get('status') or '').strip().lower()
    return status in {'scheduled', 'pending', 'open'}


@app.route('/api/civilian/dashboard', methods=['GET'])
@require_auth
def civilian_dashboard_api():
    user_id, community, error = _require_civilian_dashboard_context()
    if error:
        return error
    community_id = community['community_id']
    ensure_civilians_user_id_schema()
    selected_id = (request.args.get('civilian_id') or '').strip()
    owned_query = scoped_query(Civilian, community_id).filter_by(user_id=user_id)
    profiles = owned_query.order_by(Civilian.created_at.desc()).all()
    if selected_id:
        civilian = owned_query.filter_by(civilian_id=selected_id).first()
        if not civilian:
            return jsonify({'success': False, 'error': 'Civilian profile not found'}), 404
    else:
        civilian = profiles[0] if len(profiles) == 1 else None

    if not civilian:
        return jsonify({
            'success': True,
            'civilian': None,
            'profiles': _civilian_dashboard_profiles(profiles, community),
            'vehicles': [],
            'licenses': [],
            'citations': [],
            'fines': [],
            'arrests': [],
            'jail_history': [],
            'served_warrants': [],
            'court_dates': [],
            'complaints': [],
            'summary': {'open_fines': 0, 'served_warrants': 0, 'unpaid_tickets': 0, 'upcoming_court_dates': 0},
        })

    user = User.query.get(user_id)
    vehicles = _dashboard_vehicle_rows(civilian, community_id)
    licenses = _dashboard_license_rows(civilian, community_id)
    citations = _dashboard_citation_rows(civilian, community_id)
    fines = _dashboard_fine_rows(citations)
    arrests = _dashboard_arrest_rows(civilian, community_id)
    jail_history = _dashboard_jail_rows(civilian, community_id)
    served_warrants = _dashboard_served_warrant_rows(civilian, community_id)
    court_dates = _dashboard_court_rows(civilian, community_id)
    complaints = _dashboard_complaint_rows(civilian, community_id, user) if user else []
    unpaid_tickets = sum(1 for c in citations if str(c.get('status') or '').strip().lower() in CIVILIAN_UNPAID_STATUSES)
    open_fines = sum(1 for f in fines if str(f.get('status') or '').strip().lower() in CIVILIAN_UNPAID_STATUSES)

    return jsonify({
        'success': True,
        'civilian': _civilian_dashboard_profile(civilian, community),
        'profiles': _civilian_dashboard_profiles(profiles, community),
        'vehicles': vehicles,
        'licenses': licenses,
        'citations': citations,
        'fines': fines,
        'arrests': arrests,
        'jail_history': jail_history,
        'served_warrants': served_warrants,
        'court_dates': court_dates,
        'complaints': complaints,
        'summary': {
            'open_fines': open_fines,
            'served_warrants': len(served_warrants),
            'unpaid_tickets': unpaid_tickets,
            'upcoming_court_dates': sum(1 for row in court_dates if _is_upcoming_court(row)),
        }
    })


def _civilian_name_filter(value):
    parts = [p for p in value.split() if p]
    if len(parts) >= 2:
        first = parts[0]
        last = ' '.join(parts[1:])
        return sqlalchemy.or_(
            sqlalchemy.and_(Civilian.first_name.ilike(f'%{first}%'), Civilian.last_name.ilike(f'%{last}%')),
            sqlalchemy.and_(Civilian.first_name.ilike(f'%{last}%'), Civilian.last_name.ilike(f'%{first}%')),
        )
    return sqlalchemy.or_(Civilian.first_name.ilike(f'%{value}%'), Civilian.last_name.ilike(f'%{value}%'))

# ---------------------------------------------------------------------------
# Database-backed CAD data helpers
# ---------------------------------------------------------------------------

def load_cad_data():
    """Return the full CAD data dict assembled from DB tables for the active tenant only."""
    community_id = get_current_community_id()
    civilians   = [civilian_to_dict(c)     for c in scoped_query(Civilian, community_id).order_by(Civilian.created_at).all()]
    vehicles    = [vehicle_to_dict(v)      for v in scoped_query(Vehicle, community_id).order_by(Vehicle.created_at).all()]
    licenses    = [license_to_dict(l)      for l in scoped_query(License, community_id).order_by(License.created_at).all()]
    warrants    = [warrant_to_dict(w)      for w in scoped_query(Warrant, community_id).order_by(Warrant.created_at.desc()).all()]
    arrests     = [arrest_to_dict(a)       for a in scoped_query(Arrest, community_id).order_by(Arrest.created_at.desc()).all()]
    incidents   = [incident_to_dict(i)     for i in scoped_query(Incident, community_id).order_by(Incident.created_at.desc()).all()]
    evidence    = [evidence_to_dict(e)     for e in scoped_query(Evidence, community_id).order_by(Evidence.created_at.desc()).all()]
    traffic     = [traffic_stop_to_dict(t) for t in scoped_query(TrafficStop, community_id).order_by(TrafficStop.created_at.desc()).all()]
    calls911    = [call911_to_dict(c)      for c in scoped_query(Call911, community_id).order_by(Call911.created_at.desc()).all()]
    activity    = [activity_log_to_dict(a) for a in scoped_query(ActivityLog, community_id).order_by(ActivityLog.created_at.desc()).limit(200).all()]
    hearings    = [hearing_to_dict(h)      for h in scoped_query(Hearing, community_id).order_by(Hearing.created_at.desc()).all()]
    jail_records = [jail_booking_to_dict(j) for j in scoped_query(JailBooking, community_id).order_by(JailBooking.created_at.desc()).all()]
    officer_sessions = scoped_query(OfficerSession, community_id).filter(OfficerSession.status != 'Off Duty').order_by(OfficerSession.updated_at.desc()).all()
    officers = [
        {
            **session_to_dict(session),
            'id': session.callsign,
            'lastUpdate': session.updated_at.isoformat() if session.updated_at else (session.logged_in_at.isoformat() if session.logged_in_at else None),
        }
        for session in officer_sessions
    ] or get_config('default_officers', DEFAULT_OFFICERS, community_id=community_id)
    return {
        'civilians':   civilians,
        'vehicles':    vehicles,
        'licenses':    licenses,
        'warrants':    warrants,
        'arrests':     arrests,
        'incidents':   incidents,
        'evidence':    evidence,
        'trafficStops': traffic,
        'calls911':    calls911,
        'officers':    officers,
        'activityLog': activity,
        'hearings':    hearings,
        'jailRecords': jail_records,
    }

def _upsert_civilian(data):
    civ_id = data.get('id') or f"CIV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(Civilian, community_id).filter_by(civilian_id=civ_id).first()
    if obj is None:
        obj = Civilian(community_id=community_id, civilian_id=civ_id, first_name=data.get('firstName', ''), last_name=data.get('lastName', ''))
        db.session.add(obj)
    obj.first_name   = data.get('firstName', '')
    obj.last_name    = data.get('lastName', '')
    obj.gender       = data.get('gender', '')
    obj.phone_number = data.get('phone', '')
    obj.address      = data.get('address', '')
    obj.occupation   = data.get('occupation', '')
    obj.updated_at   = datetime.utcnow()


def _upsert_vehicle(data):
    plate = data.get('plate', '').strip()
    if not plate:
        return
    community_id = get_current_community_id()
    obj = scoped_query(Vehicle, community_id).filter_by(plate=plate).first()
    if obj is None:
        obj = Vehicle(community_id=community_id, plate=plate)
        db.session.add(obj)
    obj.owner_name          = data.get('ownerName', '')
    obj.model               = data.get('model', '')
    obj.color               = data.get('color', '')
    obj.registration_status = data.get('registrationStatus', 'Valid')
    obj.updated_at          = datetime.utcnow()


def _upsert_license(data):
    lic_id = data.get('id') or f"LIC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(License, community_id).filter_by(license_id=lic_id).first()
    if obj is None:
        obj = License(community_id=community_id, license_id=lic_id)
        db.session.add(obj)
    obj.owner_name   = data.get('ownerName', '')
    obj.license_type = data.get('licenseType', '')
    obj.status       = data.get('status', 'Valid')
    obj.issued_date  = data.get('issuedDate', '')
    obj.expiry_date  = data.get('expiryDate', '')
    obj.notes        = data.get('notes', '')
    obj.updated_at   = datetime.utcnow()


def _upsert_warrant(data):
    community_id = get_current_community_id()
    payload = _normalize_warrant_payload(data) if 'WARRANT_FIELD_MAP' in globals() else {}
    provided_warrant_id = data.get('warrant_id') or data.get('id')
    display_warrant_number = data.get('warrant_number') or provided_warrant_id
    obj = None
    if provided_warrant_id:
        obj = scoped_query(Warrant, community_id).filter(
            or_(Warrant.warrant_id == provided_warrant_id, Warrant.warrant_number == provided_warrant_id)
        ).first()
    if obj is None and display_warrant_number:
        obj = scoped_query(Warrant, community_id).filter_by(warrant_number=display_warrant_number).first()
    if obj is None:
        if provided_warrant_id and not Warrant.query.filter_by(warrant_id=provided_warrant_id).first():
            warrant_id = provided_warrant_id
        else:
            warrant_id = generate_global_warrant_id()
        obj = Warrant(
            community_id=community_id,
            warrant_id=warrant_id,
            warrant_number=display_warrant_number,
            created_by_user_id=session.get('user_id'),
            created_at=datetime.utcnow(),
        )
        db.session.add(obj)
    if payload:
        if not payload.get('warrant_number'):
            payload['warrant_number'] = getattr(obj, 'warrant_number', None) or generate_warrant_number(community_id, payload.get('warrant_type') or 'Arrest Warrant')
        _apply_warrant_payload(obj, payload)
    else:
        obj.warrant_name    = data.get('warrantName', '')
        obj.warrant_charges = data.get('warrantCharges', '')
        obj.warrant_issuer  = data.get('warrantIssuer', '')
        obj.warrant_notes   = data.get('warrantNotes', '')
        _set_warrant_status(obj, data.get('warrantStatus', 'Active'))
        obj.expiration_date = data.get('expirationDate', '')
        obj.justification   = data.get('justification', '')
        obj.updated_at      = datetime.utcnow()


def _upsert_arrest(data):
    a_id = data.get('id') or data.get('arrest_id') or f"ARR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(Arrest, community_id).filter_by(arrest_id=a_id).first()
    if obj is None:
        obj = Arrest(community_id=community_id, arrest_id=a_id, created_at=datetime.utcnow())
        db.session.add(obj)
    _apply_arrest_payload(obj, data)
    _ensure_arrest_custody_and_hearing(obj)
    logger.info(f'Arrest saved: {obj.arrest_id} civilian_id={obj.civilian_id or "unlinked"}')
    return obj

def _upsert_incident(data):
    i_id = data.get('id') or f"INC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(Incident, community_id).filter_by(incident_id=i_id).first()
    if obj is None:
        obj = Incident(community_id=community_id, incident_id=i_id)
        db.session.add(obj)
    obj.incident_type     = data.get('incidentType', '')
    obj.location          = data.get('location', '')
    obj.description       = data.get('description', '')
    obj.officers_involved = data.get('officersInvolved', '')
    obj.suspects          = data.get('suspects', '')
    obj.status            = data.get('status', 'Open')
    obj.priority          = data.get('priority', 'Medium')
    obj.notes             = data.get('notes', '')
    obj.updated_at        = datetime.utcnow()


def _upsert_evidence(data):
    e_id = data.get('id') or f"EVD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(Evidence, community_id).filter_by(evidence_id=e_id).first()
    if obj is None:
        obj = Evidence(community_id=community_id, evidence_id=e_id)
        db.session.add(obj)
    obj.case_number          = data.get('caseNumber', '')
    obj.evidence_description = data.get('evidenceDescription', data.get('description', ''))
    obj.collected_by         = data.get('collectedBy', '')
    obj.location_found       = data.get('locationFound', '')
    obj.status               = data.get('status', 'Active')
    obj.notes                = data.get('notes', '')
    obj.updated_at           = datetime.utcnow()


def _upsert_traffic_stop(data):
    t_id = data.get('id') or f"TRF-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(TrafficStop, community_id).filter_by(stop_id=t_id).first()
    if obj is None:
        obj = TrafficStop(community_id=community_id, stop_id=t_id)
        db.session.add(obj)
    _apply_traffic_stop_payload(obj, data)


def _upsert_call911(data):
    c_id = data.get('id') or f"911-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(Call911, community_id).filter_by(call_id=c_id).first()
    if obj is None:
        obj = Call911(community_id=community_id, call_id=c_id)
        db.session.add(obj)
    obj.caller_name    = data.get('callerName', '')
    obj.location       = data.get('location', '')
    obj.description    = data.get('description', '')
    obj.incident_type  = data.get('incidentType', '')
    obj.priority       = data.get('priority', 'Medium')
    obj.assigned_unit  = data.get('assignedUnit', '')
    obj.status         = data.get('status', 'New')
    obj.dispatch_notes = data.get('dispatchNotes', '')
    obj.updated_at     = datetime.utcnow()


def _upsert_activity(data):
    a_id = data.get('id') or f"ACT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    community_id = get_current_community_id()
    obj = scoped_query(ActivityLog, community_id).filter_by(log_id=a_id).first()
    if obj is None:
        obj = ActivityLog(community_id=community_id, log_id=a_id)
        db.session.add(obj)
    obj.action  = data.get('action', '')
    obj.officer = data.get('officer', '')
    obj.details = data.get('details', '')


def save_cad_data(data):
    """Persist a full CAD data dict to the database."""
    try:
        if data.get('civilians'):
            logger.info('Ignoring civilians in bulk CAD save; use POST /api/civilians for PostgreSQL civilian writes')
        for item in data.get('vehicles', []):
            _upsert_vehicle(item)
        for item in data.get('licenses', []):
            _upsert_license(item)
        for item in data.get('warrants', []):
            _upsert_warrant(item)
        for item in data.get('arrests', []):
            _upsert_arrest(item)
        for item in data.get('incidents', []):
            _upsert_incident(item)
        for item in data.get('evidence', []):
            _upsert_evidence(item)
        for item in data.get('trafficStops', []):
            _upsert_traffic_stop(item)
        for item in data.get('calls911', []):
            _upsert_call911(item)
        for item in data.get('activityLog', []):
            _upsert_activity(item)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'save_cad_data error: {e}')
        raise


def send_bolo_discord(bolo):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '')
    if not webhook_url or 'placeholder' in webhook_url:
        return False
    threat_colors = {'High': 15158332, 'Medium': 16744272, 'Low': 5763719}
    color = threat_colors.get(bolo.get('threatLevel', 'Medium'), 16744272)
    fields = [
        {"name": "BOLO ID",      "value": f"`{bolo['id']}`",               "inline": True},
        {"name": "Threat Level", "value": bolo.get('threatLevel', '—'),    "inline": True},
        {"name": "Issued By",    "value": bolo.get('issuedBy', '—'),       "inline": True},
        {"name": "Last Seen",    "value": bolo.get('lastLocation', '—'),   "inline": True},
    ]
    if bolo.get('vehicle'):
        fields.append({"name": "Vehicle",  "value": bolo['vehicle'],  "inline": True})
    if bolo.get('charges'):
        fields.append({"name": "Charges",  "value": bolo['charges'],  "inline": False})
    auto_tag = ' *(auto-generated)*' if bolo.get('autoGenerated') else ''
    payload = {
        "username":   "GTAVCAD BOLO Board",
        "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
        "embeds": [{
            "title":       f"🔍 BOLO ISSUED — {bolo.get('suspectName', 'Unknown')}{auto_tag}",
            "description": bolo.get('description', ''),
            "color":       color,
            "fields":      fields,
            "footer":      {"text": f"GTAVCAD LSPD • {bolo.get('issuedAt', '')[:10]}"},
        }]
    }
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={'Content-Type': 'application/json'}, method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception as e:
        logger.error(f'BOLO Discord webhook failed: {e}')
    return False


def create_bolo(suspect_name, description, last_location, charges, officer, threat_level='High', vehicle=''):
    bolo_id = f"BOLO-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    bolo_obj = Bolo(
        community_id=community_id,
        bolo_id=bolo_id,
        suspect_name=suspect_name,
        description=description,
        last_location=last_location,
        vehicle=vehicle,
        charges=charges,
        threat_level=threat_level,
        issued_by=officer,
        status='Active',
        auto_generated=True,
    )
    try:
        db.session.add(bolo_obj)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'create_bolo DB error: {e}')
        raise
    bolo_dict = bolo_to_dict(bolo_obj)
    send_bolo_discord(bolo_dict)
    return bolo_dict


def load_radio_log():
    entries = scoped_query(RadioLog).order_by(RadioLog.created_at.desc()).limit(100).all()
    return [radio_to_dict(r) for r in reversed(entries)]


def load_server_status():
    status = ServerStatus.query.first()
    if status is None:
        status = ServerStatus(
            city_status='ACTIVE',
            player_count=0,
            max_players=32,
            custom_message='24/7 dispatch channel live',
        )
        try:
            db.session.add(status)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f'load_server_status init error: {e}')
    return status_to_dict(status)


def save_server_status(status_dict):
    status = ServerStatus.query.first()
    if status is None:
        status = ServerStatus()
        db.session.add(status)
    status.city_status    = status_dict.get('cityStatus', 'ACTIVE')
    status.player_count   = status_dict.get('playerCount', 0)
    status.max_players    = status_dict.get('maxPlayers', 32)
    status.custom_message = status_dict.get('customMessage', '')
    status.last_updated   = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'save_server_status error: {e}')
        raise
    return status_to_dict(status)


def load_applications():
    apps = scoped_query(Application, community_id).order_by(Application.submitted_at.desc()).all()
    return [application_to_dict(a) for a in apps]


def save_application(data):
    count = scoped_query(Application).count()
    app_id = f"APP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{count+1:04d}"
    app_obj = Application(
        community_id=community_id,
        application_id=app_id,
        app_discord=data.get('appDiscord', ''),
        app_character=data.get('appCharacter', ''),
        application_type=data.get('applicationType', ''),
        age_confirmation=data.get('ageConfirmation', ''),
        experience=data.get('experience', ''),
        role_reason=data.get('roleReason', ''),
        availability=data.get('availability', ''),
        status='Pending',
        staff_notes='',
    )
    try:
        db.session.add(app_obj)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'save_application DB error: {e}')
        raise
    return application_to_dict(app_obj)


def send_application_email(app):
    smtp_host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_email = os.environ.get('SMTP_EMAIL')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    notify_email = os.environ.get('NOTIFY_EMAIL', smtp_email)
    from_name = os.environ.get('SMTP_FROM_NAME', 'GTAVCAD')

    if not smtp_email or not smtp_password:
        logger.warning('Email credentials not configured. Application saved but no email sent.')
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"[GTAVCAD] New Application — {app['applicationType']} — {app['id']}"
        msg['From'] = f"{from_name} <{smtp_email}>"
        msg['To'] = notify_email

        html = f"""
        <html><body style="font-family:Arial,sans-serif;background:#111;color:#eee;padding:24px;">
          <div style="max-width:600px;margin:0 auto;background:#1a1a1a;border-radius:12px;padding:24px;border:1px solid #333;">
            <h2 style="color:#ff2d2d;margin-top:0;">GTAVCAD — New Application Submitted</h2>
            <table style="width:100%;border-collapse:collapse;">
              <tr><td style="padding:8px 0;color:#aaa;width:40%;">Application ID</td><td style="padding:8px 0;font-weight:bold;">{app['id']}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Submitted At</td><td style="padding:8px 0;">{app['submittedAt']}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Discord</td><td style="padding:8px 0;">{app.get('appDiscord','N/A')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Character Name</td><td style="padding:8px 0;">{app.get('appCharacter','N/A')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Role Applied For</td><td style="padding:8px 0;"><strong style="color:#ff2d2d;">{app.get('applicationType','N/A')}</strong></td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Age</td><td style="padding:8px 0;">{app.get('ageConfirmation','N/A')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Availability</td><td style="padding:8px 0;">{app.get('availability','N/A')}</td></tr>
            </table>
            <hr style="border-color:#333;margin:16px 0;">
            <p style="color:#aaa;margin:4px 0;">RP Experience</p>
            <p style="background:#111;padding:12px;border-radius:8px;border-left:3px solid #ff2d2d;">{app.get('experience','N/A')}</p>
            <p style="color:#aaa;margin:4px 0;">Why They Want This Role</p>
            <p style="background:#111;padding:12px;border-radius:8px;border-left:3px solid #555;">{app.get('roleReason','N/A')}</p>
            <p style="color:#555;font-size:12px;margin-top:24px;">GTAVCAD Application System — Automated Notification</p>
          </div>
        </body></html>
        """

        plain = f"""
GTAVCAD — New Application Submitted
---------------------------------------
Application ID: {app['id']}
Submitted At:   {app['submittedAt']}
Discord:        {app.get('appDiscord','N/A')}
Character:      {app.get('appCharacter','N/A')}
Role:           {app.get('applicationType','N/A')}
Age:            {app.get('ageConfirmation','N/A')}
Availability:   {app.get('availability','N/A')}

RP Experience:
{app.get('experience','N/A')}

Why This Role:
{app.get('roleReason','N/A')}
        """

        msg.attach(MIMEText(plain, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as srv:
                srv.login(smtp_email, smtp_password)
                srv.sendmail(smtp_email, notify_email, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as srv:
                srv.ehlo()
                srv.starttls()
                srv.login(smtp_email, smtp_password)
                srv.sendmail(smtp_email, notify_email, msg.as_string())

        logger.info(f"Application email sent for {app['id']}")
        return True
    except Exception as e:
        logger.error(f"Failed to send application email: {e}")
        return False


def send_application_discord(app):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '')
    if not webhook_url or 'placeholder' in webhook_url:
        logger.warning('Discord webhook not configured. Skipping Discord notification.')
        return False

    try:
        type_colors = {
            'Police Department': 3447003,
            'EMS': 3066993,
            'Staff': 10181046,
            'Business Owner': 16744272,
            'Gang / Faction': 15158332,
            'Court / Judge / Lawyer': 16776960,
            'DMV Worker': 9807270,
        }
        color = type_colors.get(app.get('applicationType', ''), 3447003)

        payload = {
            "username": "GTAVCAD Applications",
            "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
            "embeds": [{
                "title": f"📋 New Application — {app.get('applicationType', 'Unknown')}",
                "description": f"**RP Experience:**\n{app.get('experience', 'N/A')}\n\n**Why This Role:**\n{app.get('roleReason', 'N/A')}",
                "color": color,
                "fields": [
                    {"name": "Application ID", "value": f"`{app['id']}`", "inline": True},
                    {"name": "Role", "value": app.get('applicationType', 'N/A'), "inline": True},
                    {"name": "Discord", "value": app.get('appDiscord', 'N/A'), "inline": True},
                    {"name": "Character", "value": app.get('appCharacter', 'N/A'), "inline": True},
                    {"name": "Age", "value": app.get('ageConfirmation', 'N/A'), "inline": True},
                    {"name": "Availability", "value": app.get('availability', 'N/A'), "inline": True},
                ],
                "footer": {"text": f"GTAVCAD Application System • {app['submittedAt'][:10]}"},
            }]
        }

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={'Content-Type': 'application/json'}, method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                logger.info(f"Discord notification sent for {app['id']}")
                return True
    except Exception as e:
        logger.error(f"Application Discord webhook failed: {e}")
    return False


def load_complaints():
    complaints = scoped_query(Complaint, community_id).order_by(Complaint.submitted_at.desc()).all()
    return [complaint_to_dict(c) for c in complaints]


def save_complaint(data):
    count = Complaint.query.count()
    cmp_id = f"CMP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{count+1:04d}"
    cmp_obj = Complaint(
        complaint_id=cmp_id,
        complaint_discord=data.get('complaintDiscord', ''),
        reported_name=data.get('reportedName', ''),
        complaint_type=data.get('complaintType', ''),
        incident_date=data.get('incidentDate', ''),
        incident_location=data.get('incidentLocation', ''),
        witnesses=data.get('witnesses', ''),
        evidence_link=data.get('evidenceLink', ''),
        description=data.get('description', ''),
        resolution=data.get('resolution', ''),
        status='Open',
        staff_notes='',
    )
    try:
        db.session.add(cmp_obj)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'save_complaint DB error: {e}')
        raise
    return complaint_to_dict(cmp_obj)


def send_email_notification(complaint):
    smtp_host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_email = os.environ.get('SMTP_EMAIL')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    notify_email = os.environ.get('NOTIFY_EMAIL', smtp_email)
    from_name = os.environ.get('SMTP_FROM_NAME', 'GTAVCAD')

    if not smtp_email or not smtp_password:
        logger.warning('Email credentials not configured. Complaint saved but no email sent.')
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"[GTAVCAD] New Complaint — {complaint['complaintType']} — {complaint['id']}"
        msg['From'] = f"{from_name} <{smtp_email}>"
        msg['To'] = notify_email

        html = f"""
        <html><body style="font-family:Arial,sans-serif;background:#111;color:#eee;padding:24px;">
          <div style="max-width:600px;margin:0 auto;background:#1a1a1a;border-radius:12px;padding:24px;border:1px solid #333;">
            <h2 style="color:#ff2d2d;margin-top:0;">GTAVCAD — New Complaint Filed</h2>
            <table style="width:100%;border-collapse:collapse;">
              <tr><td style="padding:8px 0;color:#aaa;width:40%;">Complaint ID</td><td style="padding:8px 0;font-weight:bold;">{complaint['id']}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Submitted At</td><td style="padding:8px 0;">{complaint['submittedAt']}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Discord Username</td><td style="padding:8px 0;">{complaint.get('complaintDiscord','N/A')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Reported Person</td><td style="padding:8px 0;">{complaint.get('reportedName','N/A')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Complaint Type</td><td style="padding:8px 0;"><strong style="color:#ff2d2d;">{complaint.get('complaintType','N/A')}</strong></td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Incident Date/Time</td><td style="padding:8px 0;">{complaint.get('incidentDate','N/A')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Location/Channel</td><td style="padding:8px 0;">{complaint.get('incidentLocation','N/A')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Witnesses</td><td style="padding:8px 0;">{complaint.get('witnesses','None')}</td></tr>
              <tr><td style="padding:8px 0;color:#aaa;">Evidence Link</td><td style="padding:8px 0;">{complaint.get('evidenceLink','None')}</td></tr>
            </table>
            <hr style="border-color:#333;margin:16px 0;">
            <p style="color:#aaa;margin:4px 0;">Description</p>
            <p style="background:#111;padding:12px;border-radius:8px;border-left:3px solid #ff2d2d;">{complaint.get('description','N/A')}</p>
            <p style="color:#aaa;margin:4px 0;">Desired Resolution</p>
            <p style="background:#111;padding:12px;border-radius:8px;border-left:3px solid #555;">{complaint.get('resolution','N/A')}</p>
            <p style="color:#555;font-size:12px;margin-top:24px;">GTAVCAD Complaint System — Automated Notification</p>
          </div>
        </body></html>
        """

        plain = f"""
GTAVCAD — New Complaint Filed
---------------------------------
Complaint ID:     {complaint['id']}
Submitted At:     {complaint['submittedAt']}
Discord Username: {complaint.get('complaintDiscord','N/A')}
Reported Person:  {complaint.get('reportedName','N/A')}
Complaint Type:   {complaint.get('complaintType','N/A')}
Incident Date:    {complaint.get('incidentDate','N/A')}
Location:         {complaint.get('incidentLocation','N/A')}
Witnesses:        {complaint.get('witnesses','None')}
Evidence Link:    {complaint.get('evidenceLink','None')}

Description:
{complaint.get('description','N/A')}

Desired Resolution:
{complaint.get('resolution','N/A')}
        """

        msg.attach(MIMEText(plain, 'plain'))
        msg.attach(MIMEText(html, 'html'))

        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as srv:
                srv.login(smtp_email, smtp_password)
                srv.sendmail(smtp_email, notify_email, msg.as_string())
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as srv:
                srv.ehlo()
                srv.starttls()
                srv.login(smtp_email, smtp_password)
                srv.sendmail(smtp_email, notify_email, msg.as_string())

        logger.info(f"Email notification sent for complaint {complaint['id']}")
        return True

    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def send_discord_notification(complaint):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '')
    if not webhook_url or 'placeholder' in webhook_url:
        logger.warning('Discord webhook not configured. Skipping Discord notification.')
        return False

    try:
        type_colors = {
            'Player report': 15158332,
            'Staff complaint': 15105570,
            'Officer complaint': 15548997,
            'Rule break': 16711680,
            'Fail RP': 16744272,
            'RDM / VDM': 16711680,
            'Harassment': 15158332,
            'Evidence submission': 3447003,
        }
        color = type_colors.get(complaint.get('complaintType', ''), 15158332)

        fields = [
            {"name": "Complaint ID", "value": f"`{complaint['id']}`", "inline": True},
            {"name": "Type", "value": complaint.get('complaintType', 'N/A'), "inline": True},
            {"name": "Reported Person", "value": complaint.get('reportedName', 'N/A'), "inline": True},
            {"name": "Discord", "value": complaint.get('complaintDiscord', 'N/A'), "inline": True},
            {"name": "Location", "value": complaint.get('incidentLocation', 'N/A'), "inline": True},
            {"name": "Incident Date", "value": complaint.get('incidentDate', 'N/A'), "inline": True},
        ]
        if complaint.get('witnesses'):
            fields.append({"name": "Witnesses", "value": complaint['witnesses'], "inline": False})
        if complaint.get('evidenceLink'):
            fields.append({"name": "Evidence", "value": complaint['evidenceLink'], "inline": False})

        payload = {
            "username": "GTAVCAD Complaints",
            "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
            "embeds": [{
                "title": f"🚨 New Complaint Filed — {complaint.get('complaintType', 'Unknown')}",
                "description": f"**Description:**\n{complaint.get('description', 'N/A')}\n\n**Desired Resolution:**\n{complaint.get('resolution', 'N/A')}",
                "color": color,
                "fields": fields,
                "footer": {"text": f"GTAVCAD Complaint System • {complaint['submittedAt'][:10]}"},
            }]
        }

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 204):
                logger.info(f"Discord notification sent for {complaint['id']}")
                return True
    except Exception as e:
        logger.error(f"Discord webhook failed: {e}")
    return False


@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    if (os.getenv('FLASK_ENV') or '').lower() == 'production' or (os.getenv('ALLOW_LEGACY_ADMIN_AUTH', '').lower() != 'true'):
        logger.warning('Legacy admin authentication attempt blocked')
        return jsonify({'success': False, 'error': 'Legacy admin authentication is disabled'}), 410
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    admin_password_hash = os.environ.get('ADMIN_PASSWORD_HASH', '')
    if not admin_password_hash:
        return jsonify({'success': False, 'error': 'Admin password not configured'}), 500
    if verify_password(admin_password_hash, password):
        session['admin_logged_in'] = True
        session['role'] = 'Admin'
        session['user_id'] = 'admin'
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid password'}), 401


@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    if (os.getenv('FLASK_ENV') or '').lower() == 'production' or (os.getenv('ALLOW_LEGACY_ADMIN_AUTH', '').lower() != 'true'):
        return jsonify({'success': False, 'error': 'Legacy admin authentication is disabled'}), 410
    session.clear()
    return jsonify({'success': True})


@app.route('/api/admin/session', methods=['GET'])
def admin_session():
    if (os.getenv('FLASK_ENV') or '').lower() == 'production' or (os.getenv('ALLOW_LEGACY_ADMIN_AUTH', '').lower() != 'true'):
        return jsonify({'success': False, 'error': 'Legacy admin authentication is disabled'}), 410
    return jsonify({'success': True, 'loggedIn': bool(session.get('admin_logged_in'))})


# User Authentication Routes
@app.route('/api/auth/login', methods=['POST'])
def user_login():
    try:
        return _user_login_impl()
    except Exception as exc:
        db.session.rollback()
        identifier = ''
        try:
            data = request.get_json(silent=True) or {}
            identifier = (data.get('username') or data.get('email') or '').strip().lower()
        except Exception:
            identifier = ''
        logger.exception(
            'Auth login exception request_id=%s identifier=%s user_id=%s error=%s',
            getattr(g, 'request_id', None),
            identifier,
            session.get('user_id'),
            exc,
        )
        return jsonify({
            'success': False,
            'error': 'Login failed due to a temporary server issue. Please try again.',
            'code': 'AUTH_TEMPORARY_ERROR',
            'request_id': getattr(g, 'request_id', None),
        }), 500


def _user_login_impl():
    data = request.get_json(silent=True) or {}
    identifier = (data.get('username') or data.get('email') or '').strip()
    password = data.get('password', '').strip()

    if not identifier or not password:
        return jsonify({'success': False, 'error': 'Username/email and password required', 'code': 'MISSING_CREDENTIALS'}), 400

    identifier_lower = identifier.lower()
    logger.info("Auth login attempt identifier=%s ip=%s", identifier_lower, request.remote_addr)
    username_filter = func.lower(User.username) == identifier_lower if hasattr(User, 'username') else None
    email_filter = func.lower(User.email) == identifier_lower if hasattr(User, 'email') else None
    filters = [f for f in (username_filter, email_filter) if f is not None]
    if not filters:
        logger.error("Auth login failed: user model missing username/email columns")
        return jsonify({'success': False, 'error': 'Authentication unavailable', 'code': 'AUTH_UNAVAILABLE'}), 500
    matched_users = User.query.filter(or_(*filters)).all()
    if not matched_users:
        logger.warning("Auth login failed: account not found identifier=%s ip=%s", identifier_lower, request.remote_addr)
        return jsonify({'success': False, 'error': 'Account not found', 'code': 'ACCOUNT_NOT_FOUND'}), 404
    if len(matched_users) != 1:
        logger.warning("Auth login failed: ambiguous identifier=%s count=%s ip=%s", identifier_lower, len(matched_users), request.remote_addr)
        return jsonify({'success': False, 'error': 'Ambiguous login identifier', 'code': 'AMBIGUOUS_IDENTIFIER'}), 409
    user = matched_users[0]
    logger.info("Auth login diagnostics identifier=%s user_found=true user_id=%s hash_present=%s active=%s role=%s platform_role=%s",
                identifier_lower, user.id, bool(user.password_hash), bool(_user_field(user, 'active', True)), _user_field(user, 'role', ''), _user_field(user, 'platform_role', ''))
    if not bool(_user_field(user, 'active', True)):
        logger.warning("Auth login failed: inactive account user_id=%s ip=%s", user.id, request.remote_addr)
        return jsonify({'success': False, 'error': 'Account is inactive', 'code': 'ACCOUNT_INACTIVE'}), 403
    if not user.password_hash:
        logger.error("Auth login failed: missing password hash user_id=%s", user.id)
        return jsonify({'success': False, 'error': 'Internal authentication error', 'code': 'AUTH_STATE_INVALID'}), 500
    verify_result = verify_password(user.password_hash, password)
    logger.info("Auth login diagnostics user_id=%s verify_result=%s", user.id, verify_result)
    if not verify_result:
        logger.warning("Auth login failed: invalid credentials user_id=%s ip=%s", user.id, request.remote_addr)
        return jsonify({'success': False, 'error': 'Invalid username or password', 'code': 'INVALID_CREDENTIALS'}), 401

    ensure_civilians_user_id_schema()
    _safe_commit_last_login(user)

    try:
        is_owner = _session_hydrate_user(user)
        session.permanent = True
        session['community_id'] = _user_field(user, 'community_id', None)
        session.modified = True

        membership, community = _safe_get_user_community_membership(user.id)
        community_id = _user_field(user, 'community_id', None) or (membership.community_id if membership else None)
        community_slug = community.slug if community else None
        community_role = normalize_community_role(getattr(membership, 'role', None)) if membership else None
        if membership and community:
            session['selected_community_id'] = community.community_id
            session['active_community_id'] = community.community_id
            session['selected_community_slug'] = community.slug
        else:
            session.pop('active_community_id', None)
            session.pop('selected_community_id', None)
            session.pop('selected_community_slug', None)
        requires_community_setup = False if is_owner else not bool(community_id)
        redirect_target = get_post_login_redirect(is_owner, community_slug, requires_community_setup)
    except Exception as exc:
        db.session.rollback()
        session.clear()
        logger.exception(
            'Auth login exception request_id=%s identifier=%s user_id=%s error=%s',
            getattr(g, 'request_id', None),
            identifier_lower,
            getattr(user, 'id', None),
            exc,
        )
        return jsonify({
            'success': False,
            'error': 'Login failed due to a temporary server issue. Please try again.',
            'code': 'AUTH_TEMPORARY_ERROR',
            'request_id': getattr(g, 'request_id', None),
        }), 500

    logger.info("Auth login success login_success=true user_id=%s username=%s role=%s platform_role=%s is_platform_owner=%s session_keys=%s redirect=%s session_modified=%s",
                user.id, session.get('username'), session.get('role'), session.get('platform_role'), is_owner, sorted(list(session.keys())), redirect_target, session.modified)
    return jsonify({
        'success': True,
        'authenticated': True,
        'user': {
            'id': user.id,
            'username': _user_field(user, 'username', ''),
            'email': _user_field(user, 'email', None),
            'role': _user_field(user, 'role', 'Civilian') or 'Civilian',
            'platform_role': _user_field(user, 'platform_role', None),
            'is_platform_owner': is_owner,
            'community_id': community_id,
            'community_slug': community_slug,
            'community_role': community_role,
            'requires_community_setup': requires_community_setup,
            'can_access_police_cad': _safe_user_can_access_police_cad(is_owner, community_role, user=user, membership=membership),
        },
        'redirect': redirect_target,
        'api_token': issue_api_token(user, session.get('active_community_id') or session.get('selected_community_id')),
        'token_type': 'Bearer',
        'expires_in': JWT_MAX_AGE_SECONDS
    })


@app.route('/api/auth/register', methods=['POST'])
def user_register():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip() or None

    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required', 'code': 'MISSING_CREDENTIALS'}), 400

    if not validate_password_policy(password):
        return jsonify({'success': False, 'error': 'Password does not meet security requirements'}), 400

    existing_query = User.query.filter(User.username == username)
    if email:
        existing_query = User.query.filter((User.username == username) | (User.email == email))
    existing = existing_query.first()
    if existing:
        if existing.username == username:
            return jsonify({'success': False, 'error': 'Username already exists', 'code': 'USERNAME_EXISTS'}), 409
        return jsonify({'success': False, 'error': 'Email already exists', 'code': 'EMAIL_EXISTS'}), 409

    user = User(username=username, email=email, password_hash=hash_password(password), role='Civilian', active=True)
    db.session.add(user)
    db.session.commit()

    _session_hydrate_user(user)

    return jsonify({
        'success': True,
        'user': user.to_dict(),
        'communities': [],
        'community_count': 0,
        'next_step': 'create_or_join_community',
        'redirect_url': '/create-community',
        'message': 'Registration successful',
        'api_token': issue_api_token(user, session.get('active_community_id') or session.get('selected_community_id')),
        'token_type': 'Bearer',
        'expires_in': JWT_MAX_AGE_SECONDS,
    }), 201


@app.route('/api/auth/logout', methods=['POST'])
def user_logout():
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/session', methods=['GET'])
def user_session():
    user_id = session.get('user_id')
    if not user_id:
        logger.info("Auth session check has_user_id=false authenticated=false reason=missing_user_id")
        return jsonify({'success': False, 'authenticated': False, 'error': 'Session expired', 'code': 'SESSION_EXPIRED'}), 401

    user = User.query.get(user_id)
    if not user or not user.active:
        logger.info("Auth session check has_user_id=true user_id=%s authenticated=false reason=user_inactive_or_missing", user_id)
        session.clear()
        return jsonify({'success': False, 'authenticated': False, 'error': 'User not found or inactive', 'code': 'USER_INACTIVE'}), 401

    owner = is_platform_owner()
    membership, community = _safe_get_user_community_membership(user.id)
    community_id = _user_field(user, 'community_id', None) or (membership.community_id if membership else None)
    community_slug = community.slug if community else None
    community_role = normalize_community_role(getattr(membership, 'role', None)) if membership else None
    can_manage_community = bool(owner or community_role in COMMUNITY_ADMIN_ROLES or (community and community.owner_user_id == user.id))
    requires_community_setup = False if owner else not bool(community_id)
    redirect_target = get_post_login_redirect(owner, community_slug, requires_community_setup)
    logger.info("Auth session check has_user_id=true user_id=%s authenticated=true is_platform_owner=%s", user_id, owner)
    return jsonify({
        'success': True,
        'authenticated': True,
        'user': {
            'id': user.id,
            'username': _user_field(user, 'username', ''),
            'email': _user_field(user, 'email', None),
            'role': _user_field(user, 'role', 'Civilian') or 'Civilian',
            'platform_role': _user_field(user, 'platform_role', None),
            'is_platform_owner': owner,
            'community_id': community_id,
            'community_slug': community_slug,
            'community_role': community_role,
            'requires_community_setup': requires_community_setup,
            'impersonation_active': bool(session.get('impersonating_community_id')),
            'can_manage_community': can_manage_community,
            'is_community_admin': can_manage_community,
            'can_access_police_cad': _safe_user_can_access_police_cad(owner, community_role, user=user, membership=membership),
        },
        'redirect': redirect_target,
    })


@app.route('/api/debug/session', methods=['GET'])
def debug_session():
    env = (os.environ.get('FLASK_ENV') or '').lower()
    owner = bool(session.get('is_platform_owner'))
    if env == 'production' and not owner:
        return jsonify({'success': False, 'error': 'Forbidden'}), 403
    return jsonify({
        'has_session_user_id': bool(session.get('user_id')),
        'session_keys': sorted(list(session.keys())),
        'cookie_secure': app.config.get('SESSION_COOKIE_SECURE', False),
        'cookie_samesite': app.config.get('SESSION_COOKIE_SAMESITE'),
        'secret_key_configured': bool(app.config.get('SECRET_KEY')),
        'is_platform_owner': owner,
    })


logger.info("✓ Auth routes registered")


@app.route('/api/onboarding/status', methods=['GET'])
def onboarding_status():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({
            'success': True,
            'authenticated': False,
            'status': 'login_required',
            'communities': [],
            'community_count': 0,
            'next_step': 'login',
        })

    memberships = CommunityMember.query.filter_by(user_id=user_id, status='Active').all()
    communities = []
    for membership in memberships:
        community = Community.query.filter_by(community_id=membership.community_id, status='Active').first()
        if community:
            communities.append({'community': community.to_dict(), 'membership': membership.to_dict()})

    next_step = 'community_picker' if len(communities) > 1 else 'enter_community' if len(communities) == 1 else 'onboarding'
    return jsonify({
        'success': True,
        'authenticated': True,
        'status': next_step,
        'communities': communities,
        'community_count': len(communities),
        'selected_community_id': session.get('selected_community_id'),
        'selected_community_slug': session.get('selected_community_slug'),
        'next_step': next_step,
    })


logger.info("✓ Onboarding routes registered")


@app.route('/api/auth/change-password', methods=['POST'])
@require_auth
def change_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not current_password or not new_password:
        return jsonify({'success': False, 'error': 'Current and new password required', 'code': 'MISSING_PASSWORDS'}), 400

    if not validate_password_policy(new_password):
        return jsonify({'success': False, 'error': 'Password does not meet security requirements'}), 400

    user_id = session.get('user_id')
    user = User.query.get(user_id)

    if not verify_password(user.password_hash, current_password):
        return jsonify({'success': False, 'error': 'Current password is incorrect', 'code': 'INVALID_CURRENT_PASSWORD'}), 400

    user.password_hash = hash_password(new_password)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Password changed successfully'})


@app.route('/api/admin/create-user', methods=['POST'])
@admin_required
def create_user():
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'Civilian')
    email = data.get('email', '').strip() or None

    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required', 'code': 'MISSING_CREDENTIALS'}), 400

    if len(password) < 8:
        return jsonify({'success': False, 'error': 'Password must be at least 8 characters', 'code': 'PASSWORD_TOO_SHORT'}), 400

    if role not in ROLES:
        return jsonify({'success': False, 'error': 'Invalid role', 'code': 'INVALID_ROLE'}), 400

    # Check if username or email already exists
    existing = User.query.filter((User.username == username) | (User.email == email)).first()
    if existing:
        if existing.username == username:
            return jsonify({'success': False, 'error': 'Username already exists', 'code': 'USERNAME_EXISTS'}), 409
        else:
            return jsonify({'success': False, 'error': 'Email already exists', 'code': 'EMAIL_EXISTS'}), 409

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role=role
    )
    db.session.add(user)
    db.session.commit()

    return jsonify({
        'success': True,
        'user': user.to_dict(),
        'message': 'User created successfully'
    })


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def list_users():
    users = User.query.all()
    return jsonify({
        'success': True,
        'users': [user.to_dict() for user in users]
    })


@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
@admin_required
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}

    # Update allowed fields
    if 'role' in data:
        if data['role'] not in ROLES:
            return jsonify({'success': False, 'error': 'Invalid role', 'code': 'INVALID_ROLE'}), 400
        user.role = data['role']

    if 'active' in data:
        user.active = data['active']

    if 'email' in data:
        email = data['email'].strip() if data['email'] else None
        # Check if email is taken by another user
        existing = User.query.filter(User.email == email, User.id != user_id).first()
        if existing:
            return jsonify({'success': False, 'error': 'Email already exists', 'code': 'EMAIL_EXISTS'}), 409
        user.email = email

    db.session.commit()

    return jsonify({
        'success': True,
        'user': user.to_dict(),
        'message': 'User updated successfully'
    })


@app.route('/api/admin/config', methods=['GET'])
@admin_required
def get_config_admin():
    configs = Config.query.all()
    return jsonify({'success': True, 'config': [c.to_dict() for c in configs]})


@app.route('/api/admin/config/<key>', methods=['PUT'])
@admin_required
def update_config(key):
    data = request.get_json(silent=True) or {}
    current_community_id = get_current_community_id()
    config = None
    if current_community_id:
        config = Config.query.filter_by(key=key, community_id=current_community_id).first()
    if not config:
        config = Config.query.filter_by(key=key, community_id=None).first()
    if not config:
        config = Config(key=key, community_id=current_community_id)
        db.session.add(config)

    import json
    if 'value' in data:
        try:
            # Validate JSON if it's meant to be JSON
            json.dumps(data['value'])
            config.value = json.dumps(data['value'])
        except:
            config.value = str(data['value'])

    if 'description' in data:
        config.description = data['description']

    config.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'success': True, 'config': config.to_dict(), 'message': 'Config updated successfully'})



@app.route('/api/platform', methods=['GET'])
def get_platform_metadata():
    """Public global platform metadata that must never be tenant-branded."""
    return jsonify({
        'success': True,
        'platform': {
            'platform_name': PLATFORM_NAME,
            'platform_domain': PLATFORM_DOMAIN,
            'tagline': PLATFORM_TAGLINE,
            'cta': PLATFORM_CTA,
            'default_community': {
                'community_name': DEFAULT_COMMUNITY_NAME,
                'community_slug': DEFAULT_COMMUNITY_SLUG,
                'cad_name': DEFAULT_COMMUNITY_CAD_NAME,
            },
            'global_routes': ['/', '/login', '/register', '/communities', '/create-community'],
            'community_route_prefix': '/c/<community_slug>',
        }
    })

@app.route('/api/config/<key>', methods=['GET'])
def get_public_config(key):
    """Get public configuration values."""
    public_keys = ['platform_name', 'platform_domain', 'platform_tagline', 'platform_cta', 'server_name', 'departments', 'call_types', 'agency_names']
    if key not in public_keys:
        return jsonify({'success': False, 'error': 'Config key not public', 'code': 'CONFIG_NOT_PUBLIC'}), 403

    if key.startswith('platform_'):
        value = get_config(key)
    else:
        value = get_config(key, community_id=get_current_community_id())
    return jsonify({'success': True, 'key': key, 'value': value})


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring."""
    redis_url = os.environ.get('REDIS_URL')
    health = {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '3.0.0',  # Phase 3
        'checks': {},
        'uptime_seconds': int(time.time() - PROCESS_START_TIME),
        'memory_usage_mb': round((__import__('resource').getrusage(__import__('resource').RUSAGE_SELF).ru_maxrss / 1024), 2),
        'active_sessions': UserSession.query.filter_by(active=True).count(),
        'active_websocket_connections': len(ACTIVE_SOCKET_CONNECTIONS),
        'websocket_status': 'healthy'
    }

    # Database health
    try:
        db.engine.execute(text('SELECT 1'))
        health['checks']['database'] = {'status': 'healthy', 'message': 'Database connection OK'}
    except Exception as e:
        health['status'] = 'unhealthy'
        health['checks']['database'] = {'status': 'unhealthy', 'message': str(e)}

    # Migration status
    try:
        # Check if alembic_version table exists
        inspector = sa_inspect(db.engine)
        if 'alembic_version' in inspector.get_table_names():
            health['checks']['migrations'] = {'status': 'healthy', 'message': 'Migrations initialized'}
        else:
            health['checks']['migrations'] = {'status': 'warning', 'message': 'Migrations not initialized'}
    except Exception as e:
        health['checks']['migrations'] = {'status': 'error', 'message': str(e)}

    # Auth status
    try:
        admin_count = User.query.filter_by(role='Admin', active=True).count()
        health['checks']['auth'] = {
            'status': 'healthy' if admin_count > 0 else 'warning',
            'message': f'{admin_count} active admin users'
        }
    except Exception as e:
        health['checks']['auth'] = {'status': 'error', 'message': str(e)}

    # Environment validation
    missing_vars = []
    required_vars = ['DATABASE_URL']
    for var in required_vars:
        if not os.environ.get(var):
            missing_vars.append(var)

    if missing_vars:
        health['status'] = 'unhealthy'
        health['checks']['environment'] = {
            'status': 'unhealthy',
            'message': f'Missing required variables: {", ".join(missing_vars)}'
        }
    else:
        health['checks']['environment'] = {'status': 'healthy', 'message': 'All required variables present'}

    if redis_url:
        try:
            import redis
            redis.Redis.from_url(redis_url, socket_connect_timeout=1).ping()
            health['checks']['redis'] = {'status': 'healthy', 'message': 'Redis ping OK'}
        except Exception as e:
            health['checks']['redis'] = {'status': 'error', 'message': str(e)}
            health['status'] = 'unhealthy'
    else:
        health['checks']['redis'] = {'status': 'disabled', 'message': 'REDIS_URL not configured'}

    # Set overall status
    if any(check.get('status') in ['unhealthy', 'error'] for check in health['checks'].values()):
        health['status'] = 'unhealthy'
    elif any(check.get('status') == 'warning' for check in health['checks'].values()):
        health['status'] = 'warning'

    status_code = 200 if health['status'] == 'healthy' else 503
    return jsonify(health), status_code


@app.route('/api/platform/status', methods=['GET'])
@admin_required
def platform_status():
    return jsonify({
        'success': True,
        'timestamp': datetime.utcnow().isoformat(),
        'metrics': {
            'total_online_users': UserSession.query.filter_by(active=True).count(),
            'total_online_officers': UserSession.query.filter(UserSession.active.is_(True), UserSession.role.in_(['Police', 'LEO', 'Dispatch'])).count(),
            'active_dispatch_calls': DispatchCall.query.filter(DispatchCall.status.in_(['Open', 'Active', 'In Progress'])).count(),
            'websocket_connections': len(ACTIVE_SOCKET_CONNECTIONS),
            'communities_online': db.session.query(UserSession.tenant).filter(UserSession.active.is_(True)).distinct().count(),
            'active_scenes': Incident.query.filter(Incident.status.in_(['Open', 'Active'])).count(),
            'pending_hearings': Hearing.query.filter(Hearing.status.in_(['Scheduled', 'Pending'])).count(),
            'open_warrants': Warrant.query.filter(Warrant.status.in_(['Active', 'Open'])).count()
        }
    })


@app.route('/api/diagnostics', methods=['GET'])
@admin_required
def diagnostics():
    """Detailed diagnostics for administrators."""
    diag = {
        'timestamp': datetime.utcnow().isoformat(),
        'system': {
            'python_version': f'{__import__("sys").version_info.major}.{__import__("sys").version_info.minor}',
            'flask_version': __import__('flask').__version__,
            'platform': __import__('platform').platform()
        },
        'database': {
            'url': os.environ.get('DATABASE_URL', 'Not set')[:50] + '...' if os.environ.get('DATABASE_URL') else 'Not set',
            'tables': []
        },
        'config': {
            'flask_secret_set': bool(os.environ.get('FLASK_SECRET')),
            'admin_password_hash_set': bool(os.environ.get('ADMIN_PASSWORD_HASH')),
            'database_url_set': bool(os.environ.get('DATABASE_URL'))
        },
        'users': {
            'total': User.query.count(),
            'admins': User.query.filter_by(role='Admin').count(),
            'active': User.query.filter_by(active=True).count()
        }
    }

    # Get table list
    try:
        inspector = sa_inspect(db.engine)
        diag['database']['tables'] = inspector.get_table_names()
    except Exception as e:
        diag['database']['error'] = str(e)

    return jsonify({'success': True, 'diagnostics': diag})


@app.route('/api/bootstrap/first-admin', methods=['POST'])
def bootstrap_first_admin():
    """Create the first admin user. Only works if no admins exist."""
    # Check if any admins already exist
    admin_count = User.query.filter_by(role='Admin', active=True).count()
    if admin_count > 0:
        return jsonify({'success': False, 'error': 'Admin users already exist', 'code': 'ADMINS_EXIST'}), 403

    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip() or None

    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required', 'code': 'MISSING_CREDENTIALS'}), 400

    if len(password) < 8:
        return jsonify({'success': False, 'error': 'Password must be at least 8 characters', 'code': 'PASSWORD_TOO_SHORT'}), 400

    # Check if username/email already exists
    existing = User.query.filter((User.username == username) | (User.email == email)).first()
    if existing:
        if existing.username == username:
            return jsonify({'success': False, 'error': 'Username already exists', 'code': 'USERNAME_EXISTS'}), 409
        else:
            return jsonify({'success': False, 'error': 'Email already exists', 'code': 'EMAIL_EXISTS'}), 409

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role='Admin'
    )
    db.session.add(user)
    db.session.commit()

    logger.info(f'✅ First admin user created: {username}')

    return jsonify({
        'success': True,
        'user': user.to_dict(),
        'message': 'First admin user created successfully'
    })


@app.route('/api/complaint', methods=['POST'])
def submit_complaint():
    data = request.get_json(silent=True) or {}
    required = ['complaintDiscord', 'reportedName', 'complaintType', 'incidentDate', 'incidentLocation', 'description', 'resolution']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'success': False, 'error': f"Missing required fields: {', '.join(missing)}"}), 400

    complaint = save_complaint(data)
    email_sent = send_email_notification(complaint)
    send_discord_notification(complaint)

    return jsonify({
        'success': True,
        'id': complaint['id'],
        'emailSent': email_sent,
        'message': 'Complaint submitted successfully. Staff will review it shortly.'
    })


@app.route('/api/complaints', methods=['GET'])
@admin_required
def list_complaints():
    authz, denied = _require_modules('complaints', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    complaints = scoped_query(Complaint, community_id).order_by(Complaint.submitted_at.desc()).all()
    result = [complaint_to_dict(c) for c in complaints]
    return jsonify({'success': True, 'complaints': result, 'total': len(result)})


@app.route('/api/complaint/<complaint_id>/status', methods=['POST'])
@admin_required
def update_complaint_status(complaint_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    staff_notes = data.get('staffNotes')
    valid_statuses = ['Open', 'Under Review', 'Resolved', 'Dismissed']
    if new_status and new_status not in valid_statuses:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    authz, denied = _require_modules('complaints', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    c = scoped_query(Complaint, community_id).filter_by(complaint_id=complaint_id).first()
    if c is None:
        return jsonify({'success': False, 'error': 'Complaint not found'}), 404
    if new_status:
        c.status = new_status
        c.updated_at = datetime.utcnow()
    if staff_notes is not None:
        c.staff_notes = staff_notes
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'update_complaint_status error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'complaint': complaint_to_dict(c)})


@app.route('/api/complaint/<complaint_id>', methods=['DELETE'])
@admin_required
def delete_complaint(complaint_id):
    authz, denied = _require_modules('complaints', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    c = scoped_query(Complaint, community_id).filter_by(complaint_id=complaint_id).first()
    if c is None:
        return jsonify({'success': False, 'error': 'Complaint not found'}), 404
    try:
        db.session.delete(c)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'delete_complaint error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/application', methods=['POST'])
def submit_application():
    data = request.get_json(silent=True) or {}
    required = ['appDiscord', 'appCharacter', 'applicationType', 'ageConfirmation', 'experience', 'roleReason', 'availability']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'success': False, 'error': f"Missing required fields: {', '.join(missing)}"}), 400

    application = save_application(data)
    send_application_email(application)
    send_application_discord(application)

    return jsonify({
        'success': True,
        'id': application['id'],
        'message': 'Application submitted successfully. Staff will review it and contact you via Discord.'
    })


@app.route('/api/applications', methods=['GET'])
@admin_required
def list_applications():
    authz, denied = _require_modules('applications', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    apps = scoped_query(Application, community_id).order_by(Application.submitted_at.desc()).all()
    result = [application_to_dict(a) for a in apps]
    return jsonify({'success': True, 'applications': result, 'total': len(result)})


@app.route('/api/application/<app_id>/status', methods=['POST'])
@admin_required
def update_application_status(app_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get('status')
    staff_notes = data.get('staffNotes')
    valid_statuses = ['Pending', 'Under Review', 'Accepted', 'Denied']
    if new_status and new_status not in valid_statuses:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400

    authz, denied = _require_modules('applications', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    a = scoped_query(Application, community_id).filter_by(application_id=app_id).first()
    if a is None:
        return jsonify({'success': False, 'error': 'Application not found'}), 404
    if new_status:
        a.status = new_status
        a.updated_at = datetime.utcnow()
    if staff_notes is not None:
        a.staff_notes = staff_notes
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'update_application_status error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'application': application_to_dict(a)})


@app.route('/api/application/<app_id>', methods=['DELETE'])
@admin_required
def delete_application(app_id):
    authz, denied = _require_modules('applications', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    a = scoped_query(Application, community_id).filter_by(application_id=app_id).first()
    if a is None:
        return jsonify({'success': False, 'error': 'Application not found'}), 404
    try:
        db.session.delete(a)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'delete_application error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/server-status', methods=['GET'])
def get_server_status():
    try:
        status = load_server_status()
        return jsonify({'success': True, 'status': status})
    except Exception as e:
        logger.error(f'Error loading server status: {e}')
        return jsonify({'success': False, 'error': 'Failed to load server status', 'code': 'STATUS_ERROR'}), 500


def send_status_discord_notification(old_status, new_status):
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '')
    if not webhook_url or 'placeholder' in webhook_url:
        logger.warning('Discord webhook not configured. Skipping status notification.')
        return False

    status_colors = {
        'ACTIVE':      0x4caf50,
        'OFFLINE':     0x555555,
        'MAINTENANCE': 0x4a9eff,
        'WHITELIST':   0xf5a623,
    }
    status_emojis = {
        'ACTIVE':      '🟢',
        'OFFLINE':     '🔴',
        'MAINTENANCE': '🔵',
        'WHITELIST':   '🟡',
    }

    city = new_status.get('cityStatus', 'ACTIVE')
    color = status_colors.get(city, 0x555555)
    emoji = status_emojis.get(city, '⚪')

    old_city = old_status.get('cityStatus', 'ACTIVE')
    changed = old_city != city
    title = f"{emoji} City Status Changed: {old_city} → {city}" if changed else f"{emoji} City Status Updated: {city}"

    fields = [
        {"name": "City Status", "value": city, "inline": True},
        {"name": "Players Online", "value": f"{new_status.get('playerCount', 0)} / {new_status.get('maxPlayers', 32)}", "inline": True},
    ]
    if new_status.get('customMessage'):
        fields.append({"name": "Message", "value": new_status['customMessage'], "inline": False})

    payload = {
        "username": "GTAVCAD Status",
        "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png",
        "embeds": [{
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {"text": f"GTAVCAD • {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"},
        }]
    }

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        urllib.request.urlopen(req, timeout=5)
        logger.info('Status Discord notification sent.')
        return True
    except Exception as e:
        logger.error(f'Failed to send status Discord notification: {e}')
        return False


@app.route('/api/server-status', methods=['POST'])
@admin_required
def update_server_status():
    data = request.get_json(silent=True) or {}
    old_status = load_server_status()
    status = dict(old_status)
    valid_statuses = ['ACTIVE', 'OFFLINE', 'MAINTENANCE', 'WHITELIST']
    if 'cityStatus' in data and data['cityStatus'] in valid_statuses:
        status['cityStatus'] = data['cityStatus']
    if 'playerCount' in data:
        try:
            status['playerCount'] = max(0, int(data['playerCount']))
        except (ValueError, TypeError):
            pass
    if 'maxPlayers' in data:
        try:
            status['maxPlayers'] = max(1, int(data['maxPlayers']))
        except (ValueError, TypeError):
            pass
    if 'customMessage' in data:
        status['customMessage'] = str(data['customMessage'])[:200]
    save_server_status(status)
    send_status_discord_notification(old_status, status)
    return jsonify({'success': True, 'status': status})


@app.route('/api/bolos', methods=['GET'])
def get_bolos():
    try:
        bolos = scoped_query(Bolo).order_by(Bolo.created_at.desc()).all()
        return jsonify({'success': True, 'bolos': [bolo_to_dict(b) for b in bolos]})
    except Exception as e:
        logger.error(f'Error loading bolos: {e}')
        return jsonify({'success': False, 'error': 'Failed to load bolos', 'code': 'BOLOS_ERROR'}), 500


@police_required
@app.route('/api/bolo', methods=['POST'])
@police_required
def post_bolo():
    data = request.get_json(silent=True) or {}
    required = ['suspectName', 'description', 'lastLocation', 'threatLevel', 'issuedBy']
    if not all(data.get(f) for f in required):
        return jsonify({'success': False, 'error': 'Missing required fields.'}), 400
    bolo_id = f"BOLO-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    bolo_obj = Bolo(
        community_id=community_id,
        bolo_id=bolo_id,
        suspect_name=data.get('suspectName', 'Unknown'),
        description=data.get('description', ''),
        last_location=data.get('lastLocation', ''),
        vehicle=data.get('vehicle', ''),
        charges=data.get('charges', ''),
        threat_level=data.get('threatLevel', 'Medium'),
        issued_by=data.get('issuedBy', ''),
        status='Active',
        auto_generated=False,
    )
    try:
        db.session.add(bolo_obj)
        db.session.commit()
        from cad_helpers import log_audit
        log_audit(data.get('issuedBy', 'unknown'), 'create_bolo', 'Bolo', bolo_id)
    except Exception as e:
        db.session.rollback()
        logger.error(f'post_bolo DB error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    bolo_dict = bolo_to_dict(bolo_obj)
    send_bolo_discord(bolo_dict)
    emit_community_event('bolo:created', bolo_dict)
    return jsonify({'success': True, 'bolo': bolo_dict})


@police_required
@app.route('/api/bolo/<bolo_id>/clear', methods=['POST'])
def clear_bolo(bolo_id):
    b = scoped_query(Bolo).filter_by(bolo_id=bolo_id).first()
    if b is None:
        return jsonify({'success': False, 'error': 'BOLO not found.'}), 404
    b.status = 'Cleared'
    b.updated_at = datetime.utcnow()
    try:
        db.session.commit()
        from cad_helpers import log_audit
        log_audit('unknown', 'clear_bolo', 'Bolo', bolo_id)
    except Exception as e:
        db.session.rollback()
        logger.error(f'clear_bolo error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    emit_community_event('bolo:cleared', {'bolo_id': bolo_id, 'status': 'Cleared'})
    return jsonify({'success': True})


@app.route('/api/ai/use-of-force', methods=['POST'])
@require_auth
def ai_use_of_force():
    guard, cad_ai_error = _cad_ai_guard()
    if cad_ai_error:
        return cad_ai_error
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data = request.get_json(silent=True) or {}
    officer       = data.get('officer', 'Unknown').strip()
    subject       = data.get('subject', 'Unknown').strip()
    location      = data.get('location', 'Unknown').strip()
    force_type    = data.get('forceType', '').strip()
    resistance    = data.get('resistance', '').strip()
    incident_desc = data.get('incidentDesc', '').strip()
    charges       = data.get('charges', '').strip()
    injuries      = data.get('injuries', '').strip()
    weapons       = data.get('weaponsObserved', 'No').strip()
    bodycam       = data.get('bodycam', 'Yes').strip()

    system_msg = """You are an AI-powered Computer Aided Dispatch (CAD) and legal report-writing system for GTAVCAD, a GTA V roleplay server set in Los Santos.
LOCATION RULES (CRITICAL): ALL locations must reference real GTA V map areas — Davis, Strawberry, Mission Row, Vespucci, Del Perro, Mirror Park, Route 68, Senora Freeway, Legion Square, Pillbox Hill Medical Center, Maze Bank Arena, etc.
USE OF FORCE REPORTS must be court-defensible: internally consistent, no contradictions, avoid vague phrases like 'acted suspicious', use only observable behaviour (e.g. 'subject repeatedly reached into waistband and ignored verbal commands'). Every escalation step must be justified. Force must match threat level. Flag any missing critical data as UNKNOWN – REQUIRES OFFICER INPUT."""

    user_msg = f"""Generate a complete Use of Force Report for GTAVCAD LSPD. Respond with ONLY a valid JSON object with these exact keys:

- "reportId": a realistic LSPD case number string (e.g. "UOF-2026-0047")
- "dateTime": today's date + a realistic time string (e.g. "May 05, 2026 — 22:14 hrs")
- "location": GTA V formatted location — convert any vague input to nearest GTA V equivalent
- "officerInvolved": officer name / badge
- "subjectInvolved": subject name
- "incidentSummary": 2-3 sentence objective overview of the incident
- "forceType": one of ["Presence", "Verbal Commands", "Physical Control", "Less Lethal (Taser/Baton)", "Lethal Force (Firearm)"]
- "reasonForForce": 2-3 sentences explaining exactly what the subject did to necessitate force, using observable behaviour only
- "resistanceLevel": one of ["Compliant", "Passive Resistance", "Active Resistance", "Assaultive", "Life-Threatening"]
- "threatAssessment": object with "weaponsObserved" (bool), "threatToOfficer" (bool), "threatToPublic" (bool), each with a one-sentence explanation
- "legalJustification": 3-4 sentence court-defensible paragraph tying officer actions to subject behaviour, emphasising proportional response under LSPD use-of-force policy
- "forceTimeline": array of 4-6 short step strings (e.g. ["Officer arrived at scene", "Initial verbal commands given", ...])
- "medicalAftercare": object with "emsRequested" (bool), "injuriesObserved" (string), "treatmentProvided" (string)
- "evidence": object with "bodycam" (bool), "witnesses" (string), "sceneEvidence" (string)
- "disposition": one of ["Arrested", "Hospitalized", "Arrested + Hospitalized", "Released — No Charges", "Deceased"]
- "chargesRecommended": comma-separated string of recommended charges (e.g. "Assault on Officer, Resisting Arrest")
- "liabilityWarning": string — if any critical justification data is missing or force seems disproportionate, return a warning starting with "⚠️ REPORT MAY BE LEGALLY WEAK –". Otherwise return empty string.
- "suspectFled": boolean — true if subject evaded or escaped, false otherwise
- "lastKnownLocation": if suspectFled true, specific GTA V street/area; otherwise empty string

Officer: {officer}
Subject: {subject}
Location: {location}
Force Type Used: {force_type if force_type else 'Not specified'}
Resistance Level: {resistance if resistance else 'Not specified'}
Weapons Observed: {weapons}
Bodycam: {bodycam}
Injuries: {injuries if injuries else 'None reported'}
Charges: {charges if charges else 'Not specified'}
Incident Description: {incident_desc if incident_desc else 'Not provided'}

Respond only with the JSON object. No markdown, no extra text."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg}
            ],
            'max_tokens': 900,
            'temperature': 0.5,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            auto_bolo = None
            if ai_json.get('suspectFled'):
                auto_bolo = create_bolo(
                    suspect_name=subject,
                    description=f'Subject fled after use-of-force incident. {incident_desc[:120] if incident_desc else ""}',
                    last_location=ai_json.get('lastKnownLocation', '') or location,
                    charges=charges or ai_json.get('chargesRecommended', ''),
                    officer=officer,
                    threat_level='High'
                )
            return jsonify({'success': True, 'report': ai_json, 'autoBolo': auto_bolo})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter UOF error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}.'}), 502
    except Exception as e:
        logger.error(f'AI UOF generation failed: {e}')
        return jsonify({'success': False, 'error': 'Report generation failed. Try again.'}), 500


@police_required
@app.route('/api/ai/generate-bolo', methods=['POST'])
def ai_generate_bolo():
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data = request.get_json(silent=True) or {}
    charges = data.get('charges', '').strip()

    system_msg = """You are an AI-powered Computer Aided Dispatch (CAD) system for GTAVCAD, a GTA V roleplay server set in Los Santos.
LOCATION RULES (CRITICAL): ALL locations must be real GTA V map areas — Davis, Strawberry, Mission Row, Vespucci, Del Perro, Mirror Park, Rockford Hills, Sandy Shores, Route 68, Senora Freeway, Legion Square, Pillbox Hill, Maze Bank Arena, LSIA, La Mesa, Cypress Flats, etc.
VEHICLES: Use GTA V vehicle names — Baller, Dominator, Sultan, Kuruma, Sentinel, Schafter, Issi, Elegy, Banshee, Sandking, Granger, etc.
Generate realistic RP suspect profiles. No real-world references."""

    charge_hint = f" The suspect is wanted for: {charges}." if charges else " Pick a realistic crime scenario."

    user_msg = f"""Generate a realistic BOLO (Be On the Lookout) notice for an LSPD officer.{charge_hint}

Respond with ONLY a valid JSON object with these exact keys:
- "suspectName": realistic full name OR "Unknown Male" / "Unknown Female" if identity unconfirmed
- "description": 2-sentence physical description (gender, approx age, build, hair, clothing, distinguishing features like tattoos/scars)
- "lastLocation": specific GTA V street + area (e.g. "Covenant Ave & Forum Dr, Davis")
- "vehicle": GTA V vehicle name + color + partial plate (e.g. "Navy Blue Baller, partial plate 4KX") or "On foot" if no vehicle
- "charges": 1-3 charge strings (e.g. "Armed Robbery, Possession of Illegal Firearm")
- "threatLevel": "High", "Medium", or "Low" — based on severity of charges

Respond only with the JSON object. No markdown, no extra text."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg}
            ],
            'max_tokens': 350,
            'temperature': 0.85,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            return jsonify({'success': True, 'bolo': ai_json})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter generate-bolo error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}.'}), 502
    except Exception as e:
        logger.error(f'AI BOLO generation failed: {e}')
        return jsonify({'success': False, 'error': 'BOLO generation failed. Try again.'}), 500


@app.route('/api/ai/police-report', methods=['POST'])
@require_auth
def ai_police_report():
    guard, cad_ai_error = _cad_ai_guard()
    if cad_ai_error:
        return cad_ai_error
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data = request.get_json(silent=True) or {}
    suspect = data.get('suspectName', 'Unknown')
    charges = data.get('charges', 'Unknown')
    officer = data.get('arrestingOfficer', 'Unknown')
    location = data.get('arrestLocation', 'Unknown')
    evidence = data.get('evidenceAttached', 'None')
    penalty = data.get('penalty', 'Unknown')
    notes = data.get('reportNotes', '')

    system_msg = """You are an AI-powered Computer Aided Dispatch (CAD) system and report-writing assistant for a GTA V roleplay server called GTAVCAD set in Los Santos.
LOCATION RULES (CRITICAL): ALL locations must reference GTA V map areas, streets, or landmarks such as Davis, Strawberry, Mission Row, Vespucci, Del Perro, Mirror Park, Route 68, Great Ocean Highway, Senora Freeway, Legion Square, Pillbox Hill Medical Center, Maze Bank Arena. If a vague location is given, convert it to the closest GTA V equivalent.
Maintain a professional law enforcement tone. No breaking RP immersion. No real-world cities."""

    user_msg = f"""Generate an INCIDENT REPORT for the following arrest. Respond with ONLY a valid JSON object with exactly four keys:
- "narrative": a formal, professional arrest narrative (150-220 words, third-person past tense). Use INCIDENT REPORT MODE structure: include Date/Time, Location (GTA V formatted), Reporting Officer, Involved Parties, Incident Type, Narrative, Actions Taken, Evidence, Disposition.
- "suggestedPenalty": a short realistic penalty string (e.g. "3 years / $25,000 fine") based on the charges — if already provided, refine and return it.
- "suspectFled": boolean true if the narrative indicates the suspect evaded, escaped, fled, or was not apprehended — otherwise false.
- "lastKnownLocation": if suspectFled is true, a specific GTA V street/area where the suspect was last seen (e.g. "Elgin Ave & Adam's Apple Blvd, Strawberry") — otherwise empty string.

Suspect: {suspect}
Charges: {charges}
Arresting Officer: {officer}
Arrest Location: {location}
Evidence: {evidence}
Current Penalty: {penalty if penalty else 'Not specified'}
Officer Notes: {notes if notes else 'None provided'}

Respond only with the JSON object. No markdown, no extra text."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user',   'content': user_msg}
            ],
            'max_tokens': 600,
            'temperature': 0.6,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            suspect_fled = ai_json.get('suspectFled', False)
            auto_bolo = None
            if suspect_fled:
                last_loc = ai_json.get('lastKnownLocation', '') or location
                auto_bolo = create_bolo(
                    suspect_name=suspect,
                    description=f'Suspect fled scene. Charges: {charges}.',
                    last_location=last_loc,
                    charges=charges,
                    officer=officer,
                    threat_level='High'
                )
                logger.info(f'Auto-BOLO created for {suspect} — fled scene')
            return jsonify({
                'success': True,
                'narrative': ai_json.get('narrative', ''),
                'suggestedPenalty': ai_json.get('suggestedPenalty', ''),
                'suspectFled': suspect_fled,
                'autoBolo': auto_bolo
            })
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter API error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}: check your API key and billing.'}), 502
    except Exception as e:
        logger.error(f'AI report generation failed: {e}')
        return jsonify({'success': False, 'error': 'Report generation failed. Try again.'}), 500


@app.route('/api/radio-log', methods=['GET'])
def get_radio_log():
    try:
        entries = scoped_query(RadioLog).order_by(RadioLog.created_at.desc()).limit(50).all()
        return jsonify({'success': True, 'entries': [radio_to_dict(r) for r in reversed(entries)]})
    except Exception as e:
        logger.error(f'Error loading radio log: {e}')
        return jsonify({'success': False, 'error': 'Failed to load radio log', 'code': 'RADIO_LOG_ERROR'}), 500


@app.route('/api/radio-log', methods=['POST'])
@police_required
def post_radio_log():
    data = request.get_json(silent=True) or {}
    unit = data.get('unit', '').strip()
    channel = data.get('channel', 'Primary').strip()
    message = data.get('message', '').strip()
    if not unit or not message:
        return jsonify({'success': False, 'error': 'Unit and message are required.'}), 400
    log_id = f"RADIO-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    entry_obj = RadioLog(
        community_id=community_id,
        log_id=log_id,
        unit=unit,
        channel=channel,
        message=message,
    )
    try:
        db.session.add(entry_obj)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'post_radio_log error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'entry': radio_to_dict(entry_obj)})


@app.route('/api/ai/dispatch', methods=['POST'])
@require_auth
def ai_dispatch():
    guard, cad_ai_error = _cad_ai_guard()
    if cad_ai_error:
        return cad_ai_error
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data = request.get_json(silent=True) or {}
    caller = data.get('callerName', 'Unknown')
    location = data.get('location', 'Unknown')
    description = data.get('description', '')

    system_msg = """You are an AI-powered Computer Aided Dispatch (CAD) system for GTAVCAD, a GTA V roleplay server set in Los Santos.
LOCATION RULES (CRITICAL): ALL locations must reference GTA V map areas — Davis, Strawberry, Mission Row, Vespucci, Del Perro, Mirror Park, Route 68, Senora Freeway, Legion Square, Pillbox Hill, Maze Bank Arena, etc. Convert vague locations to nearest GTA V equivalent.
DISPATCH LOGIC: Assign units using LSPD format (e.g. LSPD-1A23, LSPD-2B04) for city calls, BCSO format (e.g. BCSO-3C11) for county/highway calls, K9-01/K9-02 for dog units, AIR-1 for helicopter. Escalate priority for weapons, violence, or pursuit. Suggest backup when warranted.
Maintain professional law enforcement tone. No real-world city references."""

    user_msg = f"""Triage this 911 call using DISPATCH LOGIC. Respond with ONLY a valid JSON object with these exact keys:
- "incidentType": one of exactly ["Robbery", "Assault", "Suspicious activity", "Traffic accident", "Shots fired", "Domestic disturbance", "Drug activity", "Pursuit", "Hostage situation", "Noise complaint"]
- "priority": one of exactly ["Critical", "High", "Medium", "Low"] — Critical=active threat/shots/hostage, High=robbery/assault in progress, Medium=suspicious/drugs, Low=noise/minor
- "assignedUnit": realistic LSPD/BCSO unit designation based on location and incident type (e.g. "LSPD-1A23", "BCSO-2B11", "K9-02", "AIR-1")
- "status": always "New"
- "triage": one dispatcher-style sentence (max 20 words) summarising the call with GTA V location reference

Caller: {caller}
Location: {location}
Description: {description if description else 'No description provided'}

Respond only with the JSON object. No markdown, no extra text."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg}
            ],
            'max_tokens': 200,
            'temperature': 0.4,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            return jsonify({
                'success': True,
                'incidentType': ai_json.get('incidentType', ''),
                'priority': ai_json.get('priority', ''),
                'assignedUnit': ai_json.get('assignedUnit', ''),
                'status': ai_json.get('status', 'New'),
                'triage': ai_json.get('triage', '')
            })
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter dispatch error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}: check your API key.'}), 502
    except Exception as e:
        logger.error(f'AI dispatch triage failed: {e}')
        return jsonify({'success': False, 'error': 'Triage failed. Try again.'}), 500


WARRANT_AI_FORM_FIELDS = [
    'warrant_type',
    'subject_name',
    'subject_dob',
    'subject_address',
    'charges_or_basis',
    'issuing_agency',
    'judge_or_authority',
    'probable_cause',
    'search_location',
    'items_to_seize',
    'court_case_number',
    'bench_failure_reason',
    'administrative_basis',
    'inspection_scope',
    'originating_jurisdiction',
    'extradition_location',
    'fugitive_last_known_location',
    'alias_names',
    'execution_instructions',
    'expiration_date',
    'status',
]

WARRANT_AI_LEGACY_ALIASES = {
    'warrantName': 'subject_name',
    'warrantCharges': 'charges_or_basis',
    'warrantIssuer': 'issuing_agency',
    'warrantNotes': 'probable_cause',
    'warrantExpiration': 'expiration_date',
    'warrantStatus': 'status',
}

WARRANT_AI_STATUSES = {
    'Active',
    'Suspended',
    'Cleared',
    'Served',
    'Expired',
    'Withdrawn',
}


def _warrant_ai_default_expiration_date():
    return (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')


def _warrant_ai_valid_iso_date(value):
    value = _clean_warrant_ai_text(value, max_len=32)
    if not value:
        return ''
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return ''
    return parsed.strftime('%Y-%m-%d')


def _warrant_ai_valid_enum(value, valid_values, default):
    candidate = _clean_warrant_ai_text(value, max_len=80)
    return candidate if candidate in valid_values else default


def _clean_warrant_ai_text(value, max_len=1600):
    if value is None:
        return ''
    cleaned = ' '.join(str(value).replace('\x00', '').split())
    return cleaned[:max_len]


def _normalize_warrant_ai_payload(data):
    """Copy only text warrant form fields into the AI context."""
    normalized = {}
    for field in WARRANT_AI_FORM_FIELDS:
        normalized[field] = _clean_warrant_ai_text(data.get(field))
    for alias, canonical in WARRANT_AI_LEGACY_ALIASES.items():
        alias_value = _clean_warrant_ai_text(data.get(alias))
        if alias_value and not normalized.get(canonical):
            normalized[canonical] = alias_value
        if alias_value:
            normalized[alias] = alias_value
    normalized['warrant_type'] = _warrant_ai_valid_enum(
        normalized.get('warrant_type'),
        WARRANT_TYPES,
        'Arrest Warrant',
    )
    normalized['status'] = _warrant_ai_valid_enum(
        normalized.get('status'),
        WARRANT_AI_STATUSES,
        'Active',
    )
    if normalized.get('expiration_date'):
        normalized['expiration_date'] = _warrant_ai_valid_iso_date(normalized.get('expiration_date')) or _warrant_ai_default_expiration_date()
    return normalized


def _warrant_ai_backfill_values(form_values):
    warrant_type = form_values.get('warrant_type') or 'Arrest Warrant'
    subject = form_values.get('subject_name') or 'the named subject'
    charges = form_values.get('charges_or_basis') or 'pending criminal violations under San Andreas law'
    agency = form_values.get('issuing_agency') or 'LSPD'
    judge = form_values.get('judge_or_authority') or 'San Andreas court authority'
    address = form_values.get('subject_address') or 'a location associated with the subject in Los Santos'
    search_location = form_values.get('search_location') or address or 'the listed Los Santos location associated with the subject'
    expires = _warrant_ai_valid_iso_date(form_values.get('expiration_date')) or _warrant_ai_default_expiration_date()

    common = {
        'warrant_type': warrant_type,
        'subject_name': subject if subject != 'the named subject' else 'Unknown Subject',
        'subject_dob': _warrant_ai_valid_iso_date(form_values.get('subject_dob')) or '1990-01-01',
        'subject_address': form_values.get('subject_address') or 'Unknown Los Santos address',
        'charges_or_basis': charges,
        'issuing_agency': agency,
        'judge_or_authority': judge,
        'probable_cause': form_values.get('probable_cause', ''),
        'search_location': form_values.get('search_location') or search_location,
        'items_to_seize': form_values.get('items_to_seize') or 'evidence related to the listed offense',
        'court_case_number': form_values.get('court_case_number') or f'SA-CR-{datetime.now().strftime("%Y%m%d")}-{random.randint(100, 999)}',
        'bench_failure_reason': form_values.get('bench_failure_reason') or 'Failure to appear for a scheduled court proceeding or violation of a court-ordered condition after notice was provided.',
        'administrative_basis': form_values.get('administrative_basis') or f'Administrative enforcement inspection requested by {agency} for documented compliance concerns.',
        'inspection_scope': form_values.get('inspection_scope') or 'Inspect only premises, records, equipment, or areas tied to the administrative compliance basis.',
        'originating_jurisdiction': form_values.get('originating_jurisdiction') or 'San Andreas originating jurisdiction',
        'extradition_location': form_values.get('extradition_location') or 'Los Santos / San Andreas custody transfer point',
        'fugitive_last_known_location': form_values.get('fugitive_last_known_location') or 'last known in the Los Santos area',
        'alias_names': form_values.get('alias_names') or (f'{subject} / unknown alias identifiers' if subject != 'the named subject' else 'Unknown aliases used to conceal identity'),
        'execution_instructions': form_values.get('execution_instructions', ''),
        'expiration_date': expires,
        'status': _warrant_ai_valid_enum(form_values.get('status'), WARRANT_AI_STATUSES, 'Active'),
        'summary': '',
    }

    if warrant_type == 'Search Warrant':
        common.update({
            'search_location': search_location,
            'items_to_seize': form_values.get('items_to_seize') or 'weapons, stolen property, clothing matching suspect descriptions, communications devices, records, and other evidence connected to the listed offense',
            'probable_cause': form_values.get('probable_cause') or f'Based on officer observations, witness statements, dispatch records, and investigative leads in Los Santos, {subject} is believed to be connected to {charges}. A search of {search_location} is requested because evidence related to the offense is reasonably believed to be located there, including property, weapons, communications, clothing, and records that may identify participants and preserve the facts for court review.',
            'execution_instructions': form_values.get('execution_instructions') or 'Execute with appropriate officer-safety precautions, secure occupants before the search, photograph and log evidence in place when practical, preserve chain of custody, and limit the search to areas where the authorized items may reasonably be located.',
        })
    elif warrant_type == 'Bench Warrant':
        common.update({
            'court_case_number': form_values.get('court_case_number') or f'SA-CR-{datetime.now().strftime("%Y%m%d")}-{random.randint(100, 999)}',
            'bench_failure_reason': form_values.get('bench_failure_reason') or 'Failure to appear for a scheduled court proceeding or violation of a court-ordered condition after notice was provided.',
            'probable_cause': form_values.get('probable_cause') or f'Court records indicate {subject} was required to appear or comply with a lawful court order before {judge}. The subject failed to appear or otherwise violated that order, creating sufficient basis for a bench warrant so the subject can be brought before the court for processing.',
            'execution_instructions': form_values.get('execution_instructions') or 'Take the subject into custody on confirmation of identity, notify the issuing court, and transport or book the subject for court processing according to agency policy.',
        })
    elif warrant_type == 'Administrative Warrant':
        common.update({
            'administrative_basis': form_values.get('administrative_basis') or f'Administrative enforcement inspection requested by {agency} for documented compliance concerns.',
            'inspection_scope': form_values.get('inspection_scope') or 'Inspect only the listed premises, records, equipment, or areas reasonably tied to the administrative compliance basis.',
            'probable_cause': form_values.get('probable_cause') or f'{agency} has documented an administrative compliance basis requiring a limited inspection involving {subject}. The request is limited in scope and intended to verify compliance, document conditions, and preserve relevant records without exceeding the authorized inspection purpose.',
            'execution_instructions': form_values.get('execution_instructions') or 'Conduct a limited administrative inspection within the authorized scope, document observations, avoid unrelated searches, and refer any criminal evidence through proper warrant channels.',
        })
    elif warrant_type == 'Extradition Warrant':
        common.update({
            'originating_jurisdiction': form_values.get('originating_jurisdiction') or 'San Andreas originating jurisdiction',
            'extradition_location': form_values.get('extradition_location') or 'Los Santos / San Andreas custody transfer point',
            'probable_cause': form_values.get('probable_cause') or f'{subject} is wanted by the originating jurisdiction for {charges}. Records support lawful custody and transfer so the subject can answer the pending matter while identity, warrant status, and transport requirements are confirmed.',
            'execution_instructions': form_values.get('execution_instructions') or 'Confirm identity and warrant validity, coordinate with the originating jurisdiction, document custody transfer, and maintain secure transport until handoff is complete.',
        })
    elif warrant_type == 'Fugitive Warrant':
        common.update({
            'fugitive_last_known_location': form_values.get('fugitive_last_known_location') or 'last known in the Los Santos area',
            'probable_cause': form_values.get('probable_cause') or f'{subject} is wanted in connection with {charges} and is believed to be avoiding lawful detention or court processing. Information places the subject at or near {common["fugitive_last_known_location"]}, supporting fugitive status and the need for coordinated apprehension.',
            'execution_instructions': form_values.get('execution_instructions') or 'Use caution during contact, verify identity and warrant status, notify the issuing agency upon detention, and coordinate transport or transfer according to policy.',
        })
    elif warrant_type == 'Alias Warrant':
        common.update({
            'alias_names': form_values.get('alias_names') or (f'{subject} / unknown alias identifiers' if subject != 'the named subject' else 'Unknown aliases used to conceal identity'),
            'probable_cause': form_values.get('probable_cause') or f'Investigative records indicate the subject may be using alternate names or identifiers to avoid detection while connected to {charges}. The alias information requires verification so officers can confirm identity, link records accurately, and prevent mistaken release or misidentification.',
            'execution_instructions': form_values.get('execution_instructions') or 'Verify identity through multiple identifiers, document all aliases used, confirm the warrant before enforcement action, and notify the issuing agency of any identity conflicts.',
        })
    else:
        common.update({
            'probable_cause': form_values.get('probable_cause') or f'Based on officer observations, witness statements, dispatch records, and investigative information, {subject} is believed to have committed or be connected to {charges} in San Andreas. The facts support issuance of an arrest warrant so officers may lawfully locate, identify, and bring the subject before the appropriate authority.',
            'execution_instructions': form_values.get('execution_instructions') or 'Confirm the subject identity and warrant status before arrest, use standard officer-safety procedures, search incident to arrest as authorized, and transport the subject for booking or court processing.',
        })

    common['summary'] = form_values.get('summary') or f'{warrant_type} draft for {subject} based on {charges}.'
    return common


def _merge_warrant_ai_output(form_values, ai_json):
    merged = _warrant_ai_backfill_values(form_values)
    if isinstance(ai_json, dict):
        for field in WARRANT_AI_FORM_FIELDS + ['summary']:
            candidate = _clean_warrant_ai_text(ai_json.get(field), max_len=3000)
            if candidate:
                merged[field] = candidate

    # User-entered form values are facts and must not be randomly replaced.
    for field in WARRANT_AI_FORM_FIELDS:
        if form_values.get(field):
            merged[field] = form_values[field]

    merged['warrant_type'] = _warrant_ai_valid_enum(merged.get('warrant_type'), WARRANT_TYPES, 'Arrest Warrant')
    merged['status'] = _warrant_ai_valid_enum(merged.get('status'), WARRANT_AI_STATUSES, 'Active')
    merged['expiration_date'] = _warrant_ai_valid_iso_date(merged.get('expiration_date')) or _warrant_ai_default_expiration_date()

    # Re-run deterministic type-specific backfill after preserving user facts so required
    # type-specific blanks are filled even when the AI omits them.
    backfilled = _warrant_ai_backfill_values(merged)
    for field, value in backfilled.items():
        if not merged.get(field) and value:
            merged[field] = value

    merged['warrant_type'] = _warrant_ai_valid_enum(merged.get('warrant_type'), WARRANT_TYPES, 'Arrest Warrant')
    merged['status'] = _warrant_ai_valid_enum(merged.get('status'), WARRANT_AI_STATUSES, 'Active')
    merged['expiration_date'] = _warrant_ai_valid_iso_date(merged.get('expiration_date')) or _warrant_ai_default_expiration_date()

    merged['warrantName'] = merged.get('subject_name', '')
    merged['warrantCharges'] = merged.get('charges_or_basis', '')
    merged['warrantIssuer'] = merged.get('issuing_agency', '')
    merged['warrantNotes'] = merged.get('probable_cause', '')
    merged['warrantExpiration'] = merged.get('expiration_date', '')
    merged['warrantStatus'] = merged.get('status', 'Active')
    merged['justification'] = merged.get('probable_cause', '')
    merged['suggestedExpiration'] = merged.get('expiration_date', '')
    merged['suggestedStatus'] = merged.get('status', 'Active')
    return merged


@app.route('/api/ai/warrant', methods=['POST'])
@require_auth
def ai_warrant():
    guard, cad_ai_error = _cad_ai_guard()
    if cad_ai_error:
        return cad_ai_error
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    form_values = _normalize_warrant_ai_payload(request.get_json(silent=True) or {})
    safe_context = {field: form_values.get(field, '') for field in WARRANT_AI_FORM_FIELDS}
    for alias in WARRANT_AI_LEGACY_ALIASES:
        if form_values.get(alias):
            safe_context[alias] = form_values[alias]

    expected_json = {field: '' for field in WARRANT_AI_FORM_FIELDS}
    expected_json['status'] = 'Active'
    expected_json['summary'] = ''

    system_msg = """You are an AI-powered Computer Aided Dispatch (CAD) warrant drafting assistant for GTAVCAD, a GTA V roleplay server set in Los Santos.
Use only the text form fields provided by the officer as facts. Do not claim you viewed PDFs, evidence files, storage paths, local file paths, binary uploads, or download URLs. If evidence is not described in text, refer only to officer-entered statements, dispatch records, witness statements, surveillance, records checks, or investigative leads as appropriate.
Preserve every non-empty user-filled value exactly as a fact. Complete blank warrant fields with realistic, type-appropriate Los Santos / San Andreas roleplay details. Avoid contradictions, generic filler, and real-world city names.
LOCATION RULES: Use GTA V / San Andreas areas such as Davis, Strawberry, Mission Row, Vespucci, Del Perro, Mirror Park, Route 68, Senora Freeway, Legion Square, Pillbox Hill, Maze Bank Arena, Paleto Bay, Sandy Shores, and Blaine County."""

    user_msg = f"""Draft and autofill a {safe_context.get('warrant_type') or 'Arrest Warrant'} form.

Current officer-entered form values (non-empty values are facts to preserve; blank values should be completed):
{json.dumps(safe_context, ensure_ascii=False, indent=2)}

Return ONLY one valid JSON object with these exact canonical keys plus the legacy aliases listed below:
{json.dumps(expected_json, ensure_ascii=False, indent=2)}

Also include legacy aliases: warrantName, warrantCharges, warrantIssuer, warrantNotes, warrantExpiration, warrantStatus.

Type-specific completion rules:
- Search Warrant: search_location and items_to_seize must not be blank; probable_cause must explain why the search is justified; execution_instructions should mention safe execution, officer safety, and evidence preservation.
- Arrest Warrant: charges_or_basis must be clear; probable_cause must describe facts supporting arrest; execution_instructions should describe arrest/service instructions.
- Bench Warrant: court_case_number must be generated if blank; bench_failure_reason must explain failure to appear or court violation; judge_or_authority should be filled if blank; execution_instructions should mention court processing.
- Administrative Warrant: administrative_basis and inspection_scope must be filled; issuing_agency should be preserved or filled; execution_instructions should describe inspection scope.
- Extradition Warrant: originating_jurisdiction and extradition_location must be filled; charges_or_basis must be clear; execution_instructions should mention custody transfer.
- Fugitive Warrant: fugitive_last_known_location must be filled; charges_or_basis must be clear; probable_cause must support fugitive status; execution_instructions should mention caution and contacting the issuing agency.
- Alias Warrant: alias_names must be filled; probable_cause must include identity/alias reasoning; charges_or_basis must be clear; execution_instructions should mention identity verification.

Validate enums before returning JSON: warrant_type must be one of {', '.join(WARRANT_TYPES)} and status must be one of {', '.join(sorted(WARRANT_AI_STATUSES))}. If expiration_date is blank or cannot be expressed as a real YYYY-MM-DD date, use {_warrant_ai_default_expiration_date()}. Never return relative dates such as '30 days from now'. Keep probable_cause specific, court-reviewable, and based on the supplied fields."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg}
            ],
            'max_tokens': 1200,
            'temperature': 0.35,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            response_payload = _merge_warrant_ai_output(form_values, ai_json)
            return jsonify({'success': True, **response_payload})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter warrant error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}: check your API key.'}), 502
    except Exception as e:
        logger.error(f'AI warrant generation failed: {e}')
        return jsonify({'success': False, 'error': 'Warrant generation failed. Try again.'}), 500


@app.route('/api/ai/generate-call', methods=['POST'])
@require_auth
def ai_generate_call():
    guard, cad_ai_error = _cad_ai_guard()
    if cad_ai_error:
        return cad_ai_error
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data = request.get_json(silent=True) or {}
    call_type = data.get('callType', '').strip()

    system_msg = """You are an AI-powered Computer Aided Dispatch (CAD) system for GTAVCAD, a GTA V roleplay server set in Los Santos.
CALL GENERATION MODE: Generate fully realistic GTA V emergency calls.
LOCATION RULES (CRITICAL): ALL locations must be real GTA V map areas — Davis, Strawberry, Mission Row, Vespucci, Del Perro, Mirror Park, Rockford Hills, Sandy Shores, Paleto Bay, Route 68, Senora Freeway, Great Ocean Highway, Legion Square, Pillbox Hill Medical Center, Maze Bank Arena, LSIA, La Mesa, Cypress Flats, etc.
DISPATCH LOGIC: Assign LSPD units (LSPD-1A23, LSPD-2B04) for city calls, BCSO (BCSO-3C11) for county/highway, K9-01/K9-02, AIR-1 for helicopter. Escalate priority based on severity.
Generate realistic caller names (first + last). The transcript must feel like a real 911 call — dispatcher asks clarifying questions, caller may be panicked or calm depending on incident. No real-world references."""

    type_hint = f" The call type should be: {call_type}." if call_type else " Pick a random realistic incident type."

    user_msg = f"""Generate a complete GTA V 911 emergency call for an LSPD dispatch session.{type_hint}

Respond with ONLY a valid JSON object with these exact keys:
- "callType": the incident type (e.g. "Shots Fired", "Traffic Accident", "Armed Robbery", "Domestic Disturbance", "Pursuit", "Suspicious Person", "Drug Activity", "Assault in Progress")
- "caller": realistic full name of the caller
- "location": specific GTA V street, area, or landmark (e.g. "Forum Drive & Covenant Ave, Davis" or "Route 68 near Harmony")
- "description": 2-3 sentences of what the caller describes to dispatch
- "dispatchNotes": 1-2 sentences of internal dispatcher notes (unit recommendation, hazards, backup needed)
- "priority": one of "Critical", "High", "Medium", "Low"
- "assignedUnit": LSPD/BCSO unit designation (e.g. "LSPD-1A23", "BCSO-2B11", "AIR-1", "K9-02")
- "transcript": an array of 6-10 objects, each with "speaker" ("Dispatch" or "Caller") and "line" (the spoken dialogue). Make it realistic — dispatcher confirms location, caller may be scared or urgent, dispatcher gives instructions and confirms unit en route.

Respond only with the JSON object. No markdown, no extra text."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg}
            ],
            'max_tokens': 800,
            'temperature': 0.9,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            return jsonify({'success': True, 'call': ai_json})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter generate-call error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}.'}), 502
    except Exception as e:
        logger.error(f'AI call generation failed: {e}')
        return jsonify({'success': False, 'error': 'Call generation failed. Try again.'}), 500


@app.route('/api/ai/incident-summary', methods=['POST'])
def ai_incident_summary():
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data = request.get_json(silent=True) or {}
    notes = data.get('notes', '').strip()

    if not notes:
        return jsonify({'success': False, 'error': 'No CAD notes provided.'}), 400

    system_msg = """You are an AI-powered Computer Aided Dispatch (CAD) system and report-writing assistant for GTAVCAD, a GTA V roleplay server set in Los Santos.
LOCATION RULES (CRITICAL): ALL locations must reference GTA V map areas — Davis, Strawberry, Mission Row, Vespucci, Del Perro, Mirror Park, Route 68, Senora Freeway, Legion Square, Pillbox Hill, Maze Bank Arena, etc. Convert any vague or real-world locations to the closest GTA V equivalent.
OUTPUT: Generate Discord-formatted (#criminal-files channel) summaries. Use INCIDENT REPORT MODE structure. Professional law enforcement tone only."""

    user_msg = f"""An officer has provided raw CAD notes. Generate a clean Discord-formatted incident summary for the #criminal-files channel.

Rules:
- Use **bold** for all section labels
- Use a `code block` only for case/report numbers if present
- Max 200 words
- Sections (include if data available): **Incident Type**, **Location** (GTA V formatted), **Date/Time**, **Officers Involved**, **Unit(s)**, **Suspect(s)**, **Charges**, **Outcome**, **Notes**
- End with: ―――――――――――――――――――――
- Raw Discord markdown only — no wrapper blocks

Raw CAD Notes:
{notes}

Respond with ONLY a valid JSON object with one key:
- "summary": the full Discord-formatted incident summary string"""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg}
            ],
            'max_tokens': 500,
            'temperature': 0.4,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            return jsonify({'success': True, 'summary': ai_json.get('summary', '')})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter incident summary error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}.'}), 502
    except Exception as e:
        logger.error(f'AI incident summary failed: {e}')
        return jsonify({'success': False, 'error': 'Summary failed. Try again.'}), 500


@app.route('/api/ai/suspect-match', methods=['POST'])
def ai_suspect_match():
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data = request.get_json(silent=True) or {}
    description = data.get('description', '').strip()
    civilians = data.get('civilians', [])

    if not description:
        return jsonify({'success': False, 'error': 'No description provided.'}), 400

    if not civilians:
        return jsonify({'success': True, 'matches': [], 'note': 'No civilians registered in the system yet.'})

    civ_list = '\n'.join([
        f"- Name: {c.get('firstName','?')} {c.get('lastName','?')} | DOB: {c.get('dob','?')} | Gender: {c.get('gender','?')} | Occupation: {c.get('occupation','?')} | Notes: {c.get('notes','')}"
        for c in civilians[:50]
    ])

    system_msg = """You are an AI-powered suspect identification assistant for GTAVCAD, a GTA V roleplay server set in Los Santos.
You help LSPD officers cross-reference physical suspect descriptions against the civilian registry. Be precise and analytical. Only match civilians where there is genuine physical basis. Maintain professional law enforcement tone."""

    user_msg = f"""An LSPD officer has provided a physical description of a suspect spotted in Los Santos. Cross-reference the registered civilian database and return the top matches.

Respond with ONLY a valid JSON object with one key:
- "matches": array of up to 3 objects, each with:
  - "name": full civilian name
  - "confidence": "High", "Medium", or "Low"
  - "reason": one short sentence (max 15 words) citing specific matching physical traits

If no civilians reasonably match, return an empty matches array.

Suspect Description: {description}

Registered Civilians:
{civ_list}

Respond only with the JSON object. No markdown, no extra text."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user', 'content': user_msg}
            ],
            'max_tokens': 300,
            'temperature': 0.3,
            'response_format': {'type': 'json_object'}
        }).encode('utf-8')

        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            ai_json = json.loads(result['choices'][0]['message']['content'])
            return jsonify({'success': True, 'matches': ai_json.get('matches', [])})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter suspect match error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}.'}), 502
    except Exception as e:
        logger.error(f'AI suspect match failed: {e}')
        return jsonify({'success': False, 'error': 'Match failed. Try again.'}), 500


@app.route('/api/officer-status', methods=['PATCH'])
def patch_officer_status():
    denied = require_police_cad_access()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    officer_id = data.get('id', '').strip()
    new_status = data.get('status', '').strip()
    valid_statuses = ['Available', 'Assigned', 'En Route', 'On Scene', 'Busy', 'Off Duty', 'Active', 'On Duty']
    if not officer_id or new_status not in valid_statuses:
        return jsonify({'success': False, 'error': 'invalid id or status'}), 400
    try:
        ensure_officer_sessions_schema()
    except Exception as e:
        logger.error(f'patch_officer_status schema error: {e}')
        return jsonify({'success': False, 'error': 'Unable to update officer status.'}), 500
    community_id = get_current_community_id()
    s = scoped_query(OfficerSession, community_id).filter_by(callsign=officer_id).first()
    if s is None:
        s = OfficerSession(
            community_id=community_id,
            callsign=officer_id,
            officer_name=data.get('name', officer_id),
            department=data.get('department', ''),
        )
        db.session.add(s)
    s.status = new_status
    s.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'patch_officer_status error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    logger.info(f"Officer status update: {officer_id} → {new_status}")
    return jsonify({'success': True})


@app.route('/api/officer-sessions', methods=['GET'])
def get_officer_sessions():
    authz, denied = _require_modules('cad', 'dispatch', 'police')
    if denied:
        return denied
    try:
        ensure_officer_sessions_schema()
        sessions = scoped_query(OfficerSession, authz['community_id']).all()
    except Exception as e:
        logger.error(f'get_officer_sessions error: {e}')
        return jsonify({'success': False, 'error': 'Unable to load officer sessions.'}), 500
    result = {s.callsign: session_to_dict(s) for s in sessions}
    return jsonify({'success': True, 'sessions': result})


@app.route('/api/officer-sessions/active', methods=['GET'])
def get_active_officer_sessions():
    authz, denied = _require_modules('cad', 'dispatch', 'police')
    if denied:
        return denied
    try:
        ensure_officer_sessions_schema()
        sessions = scoped_query(OfficerSession, authz['community_id']).filter_by(status='On Duty').order_by(OfficerSession.updated_at.desc()).all()
    except Exception as e:
        logger.error(f'get_active_officer_sessions error: {e}')
        return jsonify({'success': False, 'error': 'Unable to load active officer sessions.'}), 500
    return jsonify({'success': True, 'sessions': [session_to_dict(s) for s in sessions]})


@app.route('/api/officer-session', methods=['POST'])
def post_officer_session():
    authz, denied = _require_modules('cad', 'dispatch', 'police')
    if denied:
        return jsonify({'success': False, 'error': 'Unable to start officer session: no Police/Dispatch/CAD permission.'}), 403
    data = request.get_json(silent=True) or {}
    callsign = (data.get('callsign') or '').strip()
    name = (data.get('officer_name') or data.get('officerName') or data.get('name') or '').strip()
    department = (data.get('department') or '').strip()
    if not callsign:
        return jsonify({'success': False, 'error': 'Callsign is required.'}), 400
    if not name:
        return jsonify({'success': False, 'error': 'Officer name is required.'}), 400
    if not department:
        return jsonify({'success': False, 'error': 'Unable to start officer session: missing unit.'}), 400

    try:
        ensure_officer_sessions_schema()
        s = scoped_query(OfficerSession, authz['community_id']).filter_by(callsign=callsign).first()
        if s is not None and (s.status or '').strip().lower() == 'on duty':
            return jsonify({'success': False, 'error': 'Callsign already in use.'}), 409
        now = datetime.utcnow()
        if s is None:
            s = OfficerSession(community_id=authz['community_id'], callsign=callsign)
            db.session.add(s)
        s.officer_name = name
        s.department = department
        s.status = 'On Duty'
        s.logged_in_at = now
        s.updated_at = now
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'post_officer_session error: {e}')
        return jsonify({'success': False, 'error': 'Unable to start officer session: endpoint failure.'}), 500

    logger.info(f"Officer login: {callsign} ({name}) — {department}")
    return jsonify({'success': True, 'session': officer_session_response(s)})


@app.route('/api/officer-sessions/end', methods=['POST'])
def end_officer_session():
    authz, denied = _require_modules('cad', 'dispatch', 'police')
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    callsign = (data.get('callsign') or '').strip()
    if not callsign:
        return jsonify({'success': False, 'error': 'Callsign is required.'}), 400
    try:
        ensure_officer_sessions_schema()
        s = scoped_query(OfficerSession, authz['community_id']).filter_by(callsign=callsign).first()
        if s:
            s.status = 'Off Duty'
            s.updated_at = datetime.utcnow()
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'end_officer_session error: {e}')
        return jsonify({'success': False, 'error': 'Unable to end officer session.'}), 500
    logger.info(f"Officer end shift: {callsign}")
    return jsonify({'success': True})


@app.route('/api/officer-session/<callsign>', methods=['DELETE'])
def delete_officer_session(callsign):
    # Backward-compatible endpoint for older Police/CAD clients.
    return end_officer_session_for_callsign(callsign)


def end_officer_session_for_callsign(callsign):
    authz, denied = _require_modules('cad', 'dispatch', 'police')
    if denied:
        return denied
    try:
        ensure_officer_sessions_schema()
        s = scoped_query(OfficerSession, authz['community_id']).filter_by(callsign=callsign).first()
        if s:
            s.status = 'Off Duty'
            s.updated_at = datetime.utcnow()
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'delete_officer_session error: {e}')
        return jsonify({'success': False, 'error': 'Unable to end officer session.'}), 500
    logger.info(f"Officer end shift: {callsign}")
    return jsonify({'success': True})


@app.route('/api/alerts', methods=['GET'])
def get_alerts():
    denied = require_police_cad_access()
    if denied:
        return denied
    since = request.args.get('since', '')
    query = scoped_query(Alert).order_by(Alert.created_at.desc()).limit(100)
    alerts = [alert_to_dict(a) for a in query.all()]
    if since:
        alerts = [a for a in alerts if (a.get('issuedAt') or '') > since]
    return jsonify({'alerts': alerts[:20]})


@app.route('/api/alert', methods=['POST'])
def post_alert():
    data = request.get_json(silent=True) or {}
    alert_type = data.get('type', '').strip()
    message = data.get('message', '').strip()
    issued_by = data.get('issuedBy', 'Dispatch').strip()
    valid_types = ['PANIC', 'BOLO', 'ALL UNITS', 'CODE RED']
    if not alert_type or not message:
        return jsonify({'success': False, 'error': 'type and message are required'}), 400
    if alert_type not in valid_types:
        return jsonify({'success': False, 'error': f'Invalid type. Must be one of: {", ".join(valid_types)}'}), 400
    alert_id = f"ALERT-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    alert_obj = Alert(
        community_id=community_id,
        alert_id=alert_id,
        alert_type=alert_type,
        message=message,
        issued_by=issued_by,
    )
    try:
        db.session.add(alert_obj)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'post_alert error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    alert_dict = alert_to_dict(alert_obj)
    logger.info(f"Alert broadcast: {alert_id} — {alert_type} by {issued_by}")
    return jsonify({'success': True, 'alert': alert_dict})



@police_required
@app.route('/api/cad/arrests', methods=['POST'])
def create_arrest_report():
    denied = require_police_cad_access()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    for field in ('suspectName', 'charges', 'arrestingOfficer', 'arrestLocation'):
        if not body.get(field):
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400

    arrest_id = body.get('id') or body.get('arrestId') or body.get('arrest_id') or f"arr-{int(datetime.utcnow().timestamp() * 1000)}-{secrets.token_hex(4)}"
    community_id = get_current_community_id()
    arrest = scoped_query(Arrest, community_id).filter_by(arrest_id=arrest_id).first()
    if arrest is None:
        arrest = Arrest(community_id=community_id, arrest_id=arrest_id, created_at=datetime.utcnow())
        db.session.add(arrest)

    try:
        _apply_arrest_payload(arrest, {**body, 'id': arrest_id})
        db.session.flush()
        _ensure_arrest_custody_and_hearing(arrest)
        db.session.commit()
        logger.info(f'Arrest saved and committed: {arrest.arrest_id}')
        from cad_helpers import log_audit
        from security_service import get_current_user
        user = get_current_user()
        log_audit(user['user_id'] or body.get('arrestingOfficer', 'unknown'), 'create_arrest', 'Arrest', arrest.arrest_id, actor_role=user['role'], ip_address=user['ip'])
        return jsonify({'success': True, 'arrest': arrest_to_dict(arrest)})
    except Exception as e:
        db.session.rollback()
        logger.error(f'create_arrest_report error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cad', methods=['GET'])
@app.route('/api/cad/data', methods=['GET'])
def get_cad_data():
    denied = require_police_cad_access()
    if denied:
        return denied
    try:
        data = load_cad_data()
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.error(f'Error loading CAD data: {e}')
        return jsonify({'success': False, 'error': 'Failed to load CAD data', 'code': 'LOAD_ERROR'}), 500


@app.route('/api/cad', methods=['POST'])
@app.route('/api/cad/data', methods=['POST'])
def post_cad_data():
    denied = require_police_cad_access()
    if denied:
        return denied
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': 'Invalid payload'}), 400

    try:
        save_cad_data(data)
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error saving CAD data: {e}')
        return jsonify({'success': False, 'error': str(e), 'code': 'SAVE_ERROR'}), 500

@app.route('/api/ai/shift-summary', methods=['POST'])
def ai_shift_summary():
    ai_runtime, ai_error = get_platform_ai_runtime_or_error()
    if ai_error:
        return ai_error
    api_key = ai_runtime['api_key']

    data     = request.get_json(silent=True) or {}
    officer  = data.get('officer',    'Unknown')
    callsign = data.get('callsign',   '')
    dept     = data.get('department', '')
    started  = data.get('shiftStart', 'Unknown')
    calls    = data.get('calls',        [])
    arrests  = data.get('arrests',      [])
    warrants = data.get('warrants',     [])
    traffic  = data.get('trafficStops', [])

    def fmt_calls(lst):
        lines = [f"- [{c.get('priority','?')}] {c.get('incidentType','Unknown')} @ {c.get('location','?')} — {c.get('status','?')}" for c in lst[:8]]
        return '\n'.join(lines) if lines else 'None'

    def fmt_arrests(lst):
        lines = [f"- {a.get('suspectName','?')}: {a.get('charges','?')} | Penalty: {a.get('penalty','?')}" for a in lst[:8]]
        return '\n'.join(lines) if lines else 'None'

    def fmt_warrants(lst):
        lines = [f"- {w.get('warrantName', w.get('suspectName','?'))}: {w.get('warrantCharges', w.get('charges','?'))} ({w.get('warrantStatus', w.get('status','Active'))})" for w in lst[:8]]
        return '\n'.join(lines) if lines else 'None'

    def fmt_traffic(lst):
        lines = [f"- {t.get('driverName','?')} ({t.get('trafficPlate', t.get('plate','?'))}): {t.get('trafficReason', t.get('reason','?'))} → {t.get('trafficOutcome', t.get('outcome','?'))}" for t in lst[:8]]
        return '\n'.join(lines) if lines else 'None'

    system_msg = (
        "You are an AI report-writing assistant for GTAVCAD, a GTA V roleplay server set in Los Santos. "
        "Write professional law enforcement shift summaries for Discord posting. Use GTA V location and street names. "
        "Keep it RP-immersive, third-person, professional tone. No real-world city references."
    )

    user_msg = f"""Generate a Discord-ready end-of-shift summary for this officer. Use Discord markdown (bold with **, bullets with •). No # headers.

Officer: {officer} ({callsign}) — {dept}
Shift Started: {started}

Calls Handled ({len(calls)} total):
{fmt_calls(calls)}

Arrests Made ({len(arrests)} total):
{fmt_arrests(arrests)}

Warrants Issued ({len(warrants)} total):
{fmt_warrants(warrants)}

Traffic Stops ({len(traffic)} total):
{fmt_traffic(traffic)}

Structure: one opening sentence → **Calls** section → **Arrests** section → **Warrants** section → **Traffic Stops** section → professional closing line. Under 300 words. Plain Discord text only, no JSON."""

    try:
        payload = json.dumps({
            'model': ai_runtime['model'],
            'messages': [
                {'role': 'system', 'content': system_msg},
                {'role': 'user',   'content': user_msg}
            ],
            'max_tokens': 500,
            'temperature': 0.6,
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://openrouter.ai/api/v1/chat/completions',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'HTTP-Referer': get_openrouter_http_referer(),
                'X-Title': 'GTAVCAD Police CAD'
            },
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            result  = json.loads(resp.read().decode('utf-8'))
            summary = result['choices'][0]['message']['content']
            return jsonify({'success': True, 'summary': summary})
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        logger.error(f'OpenRouter shift-summary error: {e.code} {body}')
        return jsonify({'success': False, 'error': f'OpenRouter error {e.code}.'}), 502
    except Exception as e:
        logger.error(f'AI shift summary failed: {e}')
        return jsonify({'success': False, 'error': 'Shift summary failed. Try again.'}), 500


@app.route('/api/court/hearings', methods=['GET'])
def get_hearings():
    denied = require_police_cad_access()
    if denied:
        return denied
    hearings = scoped_query(Hearing).order_by(Hearing.scheduled_at.desc()).all()
    return jsonify({'success': True, 'hearings': [hearing_to_dict(h) for h in hearings]})


@judge_required
@app.route('/api/court/hearings', methods=['POST'])
def create_hearing():
    denied = require_police_cad_access()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    for field in ('suspectName', 'charges', 'hearingType', 'scheduledAt', 'filingOfficer'):
        if not body.get(field):
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
    ts = int(datetime.utcnow().timestamp() * 1000)
    rand = secrets.token_hex(5)
    hearing_obj = Hearing(
        community_id=community_id,
        hearing_id=f'hearing-{ts}-{rand}',
        civilian_id=body.get('civilianId', body.get('civilian_id', '')),
        suspect_name=body.get('suspectName', '').strip(),
        charges=body.get('charges', '').strip(),
        hearing_type=body.get('hearingType', 'Arraignment'),
        scheduled_at=body.get('scheduledAt', ''),
        judge=body.get('judge', '').strip(),
        notes=body.get('notes', '').strip(),
        arrest_id=body.get('arrestId', ''),
        filing_officer=body.get('filingOfficer', '').strip(),
        outcome='',
        sentence_length='',
        fine_amount='',
        outcome_notes='',
        status='Scheduled',
    )
    try:
        db.session.add(hearing_obj)
        db.session.commit()
        from cad_helpers import log_audit
        log_audit(body.get('filingOfficer', 'unknown'), 'create_hearing', 'Hearing', hearing_obj.hearing_id)
    except Exception as e:
        db.session.rollback()
        logger.error(f'create_hearing error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'hearing': hearing_to_dict(hearing_obj)})


@judge_required
@app.route('/api/court/hearings/<hearing_id>', methods=['PUT'])
def update_hearing(hearing_id):
    denied = require_police_cad_access()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    h = scoped_query(Hearing).filter_by(hearing_id=hearing_id).first()
    if h is None:
        return jsonify({'success': False, 'error': 'Hearing not found'}), 404
    if 'outcome' in body:
        h.outcome = body['outcome']
    if 'sentenceLength' in body:
        h.sentence_length = body['sentenceLength']
    if 'fineAmount' in body:
        h.fine_amount = body['fineAmount']
    if 'outcomeNotes' in body:
        h.outcome_notes = body['outcomeNotes']
    if 'status' in body:
        h.status = body['status']
    if 'judge' in body:
        h.judge = body['judge']
    if 'notes' in body:
        h.notes = body['notes']
    if 'scheduledAt' in body:
        h.scheduled_at = body['scheduledAt']
    h.updated_at = datetime.utcnow()
    _sync_custody_from_completed_hearing(h)
    try:
        db.session.commit()
        from cad_helpers import log_audit
        log_audit('judge', 'update_hearing', 'Hearing', hearing_id)
    except Exception as e:
        db.session.rollback()
        logger.error(f'update_hearing error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'hearing': hearing_to_dict(h)})


@judge_required
@app.route('/api/court/hearings/<hearing_id>', methods=['DELETE'])
def delete_hearing(hearing_id):
    denied = require_police_cad_access()
    if denied:
        return denied
    h = scoped_query(Hearing).filter_by(hearing_id=hearing_id).first()
    if h is None:
        return jsonify({'success': False, 'error': 'Hearing not found'}), 404
    try:
        db.session.delete(h)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'delete_hearing error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True})


@app.route('/api/jail/inmates', methods=['GET'])
def get_inmates():
    denied = require_police_cad_access()
    if denied:
        return denied
    inmates = scoped_query(Inmate).order_by(Inmate.booked_at.desc()).all()
    return jsonify({'success': True, 'inmates': [inmate_to_dict(i) for i in inmates]})


@app.route('/api/jail/inmates', methods=['POST'])
def book_inmate():
    denied = require_police_cad_access()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    for field in ('suspectName', 'charges', 'bookedBy'):
        if not body.get(field):
            return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
    ts = int(datetime.utcnow().timestamp() * 1000)
    rand = secrets.token_hex(4)
    inmate_obj = Inmate(
        community_id=community_id,
        inmate_id=f'inmate-{ts}-{rand}',
        suspect_name=body.get('suspectName', '').strip(),
        charges=body.get('charges', '').strip(),
        penalty=body.get('penalty', '').strip(),
        cell=body.get('cell', '').strip(),
        booked_by=body.get('bookedBy', '').strip(),
        arrest_id=body.get('arrestId', ''),
        estimated_release=body.get('estimatedRelease', ''),
        notes=body.get('notes', '').strip(),
        status='In Custody',
    )
    try:
        db.session.add(inmate_obj)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'book_inmate error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'inmate': inmate_to_dict(inmate_obj)})


@app.route('/api/jail/inmates/<inmate_id>', methods=['PUT'])
def update_inmate(inmate_id):
    denied = require_police_cad_access()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    inmate = scoped_query(Inmate).filter_by(inmate_id=inmate_id).first()
    if inmate is None:
        return jsonify({'success': False, 'error': 'Inmate not found'}), 404
    if 'estimatedRelease' in body:
        inmate.estimated_release = body['estimatedRelease']
    if 'cell' in body:
        inmate.cell = body['cell']
    if 'notes' in body:
        inmate.notes = body['notes']
    if 'penalty' in body:
        inmate.penalty = body['penalty']
    if 'status' in body:
        inmate.status = body['status']
    inmate.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'update_inmate error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'inmate': inmate_to_dict(inmate)})


@app.route('/api/jail/inmates/<inmate_id>/release', methods=['POST'])
def release_inmate(inmate_id):
    denied = require_police_cad_access()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    inmate = scoped_query(Inmate).filter_by(inmate_id=inmate_id).first()
    if inmate is None:
        return jsonify({'success': False, 'error': 'Inmate not found'}), 404
    inmate.status = 'Released'
    inmate.released_at = datetime.utcnow()
    inmate.released_by = body.get('releasedBy', 'Officer').strip()
    inmate.release_reason = body.get('releaseReason', '').strip()
    inmate.updated_at = datetime.utcnow()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'release_inmate error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'inmate': inmate_to_dict(inmate)})


# ---------------------------------------------------------------------------
# AI Civilian Generation
# ---------------------------------------------------------------------------

@app.route('/api/ai/civilian', methods=['POST'])
@admin_required
def generate_ai_civilian():
    """Generate and save an AI civilian."""
    from civilian_ai_service import generate_and_save_civilian
    from cad_helpers import log_audit

    try:
        civilian_data = generate_and_save_civilian()
        log_audit('ai', 'generate_civilian', 'Civilian', civilian_data['civilian_id'])

        return jsonify({
            'success': True,
            'civilian': civilian_data,
            'message': f"Generated {civilian_data['full_name']}"
        })
    except ValueError as e:
        logger.error(f'Duplicate prevention failed: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400
    except Exception as e:
        logger.error(f'Failed to generate civilian: {e}')
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ---------------------------------------------------------------------------
# AI Assist — Civilian Generator (public, no admin required)
# ---------------------------------------------------------------------------

@app.route('/api/ai/civilian-assist', methods=['POST'])
def ai_civilian_assist():
    """Generate civilian data for form population (NO auto-save)."""
    try:
        params = request.get_json() or {}

        from ai_assist_service import generate_ai_civilian

        civilian_data, source = generate_ai_civilian(params)

        if 'error' in civilian_data:
            return jsonify({'success': False, 'error': civilian_data['error']}), 400

        # Return ONLY form-visible fields
        return jsonify({
            'success': True,
            'data': civilian_data,
            'source': source,
        }), 200

    except Exception as e:
        logger.error(f'AI assist error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/civilians', methods=['POST'])
def create_civilian():
    """Persist a Civilian Registration payload directly to PostgreSQL."""
    try:
        authz, denied = _require_modules('civilian_portal', 'dmv_self')
        if denied:
            return denied
        community = resolve_active_community()
        if not community:
            return jsonify({'success': False, 'error': 'Active community is required'}), 400
        community_id = community['community_id']
        data = request.get_json(silent=True) or {}
        mapped = _civilian_from_payload(data)

        if not mapped['first_name'] or not mapped['last_name']:
            return jsonify({'success': False, 'error': 'firstName and lastName are required'}), 400

        current_user_id = authz['user_id']

        ensure_civilians_user_id_schema()
        civilian_id = f"CIV-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        civilian = Civilian(community_id=community_id, user_id=current_user_id, civilian_id=civilian_id, **mapped)

        db.session.add(civilian)
        db.session.commit()

        logger.info('Civilian insert success: civilian_id=%s name="%s %s" plate="%s"',
                    civilian.civilian_id, civilian.first_name, civilian.last_name, civilian.plate_number or '')

        dashboard_url = f"/c/{community.get('slug')}/civilian-dashboard?civilian_id={civilian.civilian_id}" if community.get('slug') else f"/civilian-dashboard?civilian_id={civilian.civilian_id}"
        return jsonify({
            'success': True,
            'civilian_id': civilian.civilian_id,
            'id': civilian.civilian_id,
            'name': _civilian_full_name(civilian),
            'community_slug': community.get('slug'),
            'dashboard_url': dashboard_url,
            'civilian': _civilian_response(civilian),
        }), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create civilian: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/civilians', methods=['GET'])
def get_civilians():
    """Read civilian records directly from PostgreSQL with q/name/dob filters."""
    authz, denied = _require_modules('civilian_portal', 'dmv_lookup', 'cad', 'police', 'community_admin')
    if denied:
        return denied
    q = request.args.get('q', '').strip()
    name = request.args.get('name', '').strip()
    dob = request.args.get('dob', '').strip()
    community = resolve_active_community()
    if not community:
        return jsonify({'success': False, 'error': 'Active community is required'}), 400

    try:
        logger.info('Civilian lookup query: q="%s" name="%s" dob="%s"', q, name, dob)
        civilian_query = _civilian_search_query(q, name=name, dob=dob, community_id=community['community_id'])
        can_read_all = any(
            _can_access_module(module, authz['allowed_modules'])
            for module in ('dmv_lookup', 'cad', 'police', 'community_admin')
        )
        if not can_read_all:
            civilian_query = civilian_query.filter(Civilian.user_id == authz['user_id'])
        civilians = civilian_query.order_by(Civilian.created_at.desc()).limit(100).all()
        result = [_civilian_response(c) for c in civilians]
        logger.info('Civilian lookup result count: %s', len(result))
        return jsonify({'success': True, 'civilians': result, 'results': result, 'total': len(result)})
    except Exception as e:
        logger.error(f'Civilian lookup error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/civilian/search', methods=['POST'])
def search_civilians():
    """Police/CAD civilian lookup backed by PostgreSQL civilians table."""
    authz, denied = _require_modules('cad', 'police', 'dmv_lookup')
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    query = (data.get('query') or data.get('q') or '').strip()
    name = (data.get('name') or '').strip()
    dob = (data.get('dob') or '').strip()

    if not query and not name and not dob:
        return jsonify({'success': False, 'error': 'Query required'}), 400
    community = resolve_active_community()
    if not community:
        return jsonify({'success': False, 'error': 'Active community is required'}), 400

    try:
        logger.info('Civilian lookup query: q="%s" name="%s" dob="%s"', query, name, dob)
        civilians = _civilian_search_query(query, name=name, dob=dob, community_id=community['community_id']).order_by(Civilian.created_at.desc()).limit(50).all()
        result = [_civilian_response(c) for c in civilians]
        logger.info('Civilian lookup result count: %s', len(result))
        return jsonify({'success': True, 'results': result, 'civilians': result, 'total': len(result)})
    except Exception as e:
        logger.error(f'Civilian search error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cad/search', methods=['POST'])
def cad_search():
    """Search civilians in PostgreSQL database for CAD."""
    denied = require_police_cad_access()
    if denied:
        return denied
    community_id = get_current_community_id()
    if not community_id:
        return jsonify({'success': False, 'error': 'Community context required'}), 400
    try:
        data = request.get_json(silent=True) or {}
        query_type = data.get('type', 'all')
        query_value = (data.get('query') or data.get('q') or '').strip()

        if not query_value:
            return jsonify({'success': False, 'error': 'Query required'}), 400

        logger.info('Civilian lookup query: cad_type="%s" query="%s"', query_type, query_value)

        if query_type == 'name':
            civilians = _civilian_search_query('', name=query_value, community_id=community_id).all()
        elif query_type == 'dob':
            civilians = _civilian_search_query('', dob=query_value, community_id=community_id).all()
        elif query_type in ('plate', 'phone', 'civilian_id'):
            column = {
                'plate': Civilian.plate_number,
                'phone': Civilian.phone_number,
                'civilian_id': Civilian.civilian_id,
            }[query_type]
            civilians = scoped_query(Civilian, community_id).filter(column.ilike(f'%{query_value}%')).order_by(Civilian.created_at.desc()).limit(50).all()
        elif query_type == 'all':
            civilians = _civilian_search_query(query_value, community_id=community_id).order_by(Civilian.created_at.desc()).limit(50).all()
        else:
            return jsonify({'success': False, 'error': 'Invalid search type'}), 400

        results = [_civilian_response(c) for c in civilians]
        logger.info('Civilian lookup result count: %s', len(results))

        return jsonify({
            'success': True,
            'query_type': query_type,
            'query': query_value,
            'results': results,
            'civilians': results,
            'total': len(results),
        }), 200

    except Exception as e:
        logger.error(f'CAD search error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Advanced AI Character Engine
# ---------------------------------------------------------------------------

@app.route('/api/ai/character', methods=['POST'])
def ai_generate_character():
    """Generate AI character data (form population only, no auto-save)."""
    data = request.get_json(silent=True) or {}

    from ai_assist_service import generate_ai_civilian

    try:
        ai_result, source = generate_ai_civilian(data)

        if 'error' in ai_result:
            return jsonify({'success': False, 'error': ai_result['error']}), 500

        return jsonify({
            'success': True,
            'source': source,
            'data': ai_result,
        })
    except Exception as e:
        logger.error(f'Character generation failed: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/narrative', methods=['POST'])
def ai_generate_narrative():
    """Generate AI narrative for reports."""
    data = request.get_json(silent=True) or {}

    narrative_type = data.get('type', 'arrest_narrative')
    context = data.get('context', '')

    if not context:
        return jsonify({'success': False, 'error': 'Context required'}), 400

    from ai_character_engine import generate_narrative
    from cad_helpers import log_ai_generation

    result = generate_narrative(narrative_type, context)

    if 'error' in result:
        log_ai_generation('narrative', data, 'Failed', status='Error', error_message=result['error'])
        return jsonify({'success': False, 'error': result['error']}), 500

    log_ai_generation('narrative', data, f'Generated {narrative_type}', status='Success')
    return jsonify({'success': True, 'narrative': result})


@app.route('/api/cad/civilian/<civilian_id>', methods=['GET'])
def get_cad_civilian(civilian_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    """Get civilian details for CAD."""
    try:
        civilian = scoped_query(Civilian, community_id).filter_by(civilian_id=civilian_id).first()

        if not civilian:
            return jsonify({'success': False, 'error': 'Civilian not found'}), 404

        return jsonify({
            'success': True,
            'civilian': _civilian_response(civilian),
        }), 200

    except Exception as e:
        logger.error(f'Failed to get civilian: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cad/civilians', methods=['GET'])
def get_all_cad_civilians():
    community_id, error = _require_cad_community()
    if error:
        return error
    """Get all civilians for CAD list."""
    try:
        civilians = scoped_query(Civilian, community_id).order_by(Civilian.created_at.desc()).all()

        results = [_civilian_response(c) for c in civilians]

        logger.info(f'CAD civilians list: total={len(results)}')

        return jsonify({
            'success': True,
            'civilians': results,
            'total': len(results),
        }), 200

    except Exception as e:
        logger.error(f'Failed to get civilians list: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/civilian/<civilian_id>', methods=['GET'])
def get_civilian(civilian_id):
    authz, denied = _require_modules('civilian_portal', 'dmv_lookup', 'cad', 'police', 'community_admin')
    if denied:
        return denied
    community = resolve_active_community()
    if not community:
        return jsonify({'success': False, 'error': 'Active community is required'}), 400
    c = scoped_query(Civilian, community['community_id']).filter_by(civilian_id=civilian_id).first()
    if not c:
        return jsonify({'success': False, 'error': 'Civilian not found'}), 404
    can_read_all = any(
        _can_access_module(module, authz['allowed_modules'])
        for module in ('dmv_lookup', 'cad', 'police', 'community_admin')
    )
    if not can_read_all and c.user_id != authz['user_id']:
        return jsonify({'success': False, 'error': 'Civilian not found'}), 404

    return jsonify({
        'success': True,
        'civilian': _civilian_response(c),
    })


# ---------------------------------------------------------------------------
# Dispatch CAD Routes
# ---------------------------------------------------------------------------

@app.route('/api/dispatch/calls', methods=['GET'])
def get_dispatch_calls():
    authz, denied = _require_modules('cad', 'dispatch', 'call_logs', 'report_911', 'civilian_portal')
    if denied:
        return denied
    """Get active dispatch calls scoped to active community."""
    from dispatch_service import get_active_calls
    calls = get_active_calls()
    can_cad = _can_access_module('cad', authz['allowed_modules']) or _can_access_module('dispatch', authz['allowed_modules'])
    if not can_cad:
        calls = [c for c in calls if c.get('caller_user_id') == authz['user_id']]
    return jsonify({'success': True, 'calls': calls, 'total': len(calls)})


@app.route('/api/dispatch/calls', methods=['POST'])
@require_auth
def create_dispatch_call_route():
    """Create a new dispatch call with server-side tenant scoping."""
    authz, denied = _require_modules('report_911', 'civilian_portal', 'cad', 'dispatch')
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    required = ['location', 'description']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'success': False, 'error': f'Missing fields: {", ".join(missing)}'}), 400

    from dispatch_service import create_dispatch_call as create_call
    from cad_helpers import log_audit

    try:
        caller_name = (data.get('caller_name') or data.get('callerName') or session.get('username') or 'Citizen').strip()
        call_type = (data.get('call_type') or data.get('incidentType') or 'Emergency').strip()
        call = create_call(
            caller_name,
            (data.get('location') or '').strip(),
            call_type,
            (data.get('description') or '').strip(),
            data.get('priority', 'Medium')
        )
        call.community_id = authz['community_id']
        call.created_by_user_id = authz['user_id']
        if _can_access_module('report_911', authz['allowed_modules']) or _can_access_module('civilian_portal', authz['allowed_modules']):
            call.caller_user_id = authz['user_id']
        call.updated_at = datetime.utcnow()

        log_audit('dispatch', 'create_call', 'DispatchCall', call.call_id)
        _cad_audit('911_call_created', authz['community_id'], None, {'call_id': call.call_id, 'call_type': call.call_type})
        _create_call_notifications(authz['community_id'], call.call_id, call.call_type, call.location)
        db.session.commit()
        payload = {
            'call_id': call.call_id,
            'caller_name': call.caller_name,
            'location': call.location,
            'call_type': call.call_type,
            'description': call.description,
            'priority': call.priority,
            'status': call.status,
            'community_id': authz['community_id'],
        }
        socketio.emit('dispatch:call_created', payload, room=f"community:{authz['community_id']}:dispatch")
        socketio.emit('dispatch:call_created', payload, room=f"community:{authz['community_id']}:police")
        socketio.emit('dispatch:call_created', payload, room=f"community:{authz['community_id']}:admin")

        return jsonify({
            'success': True,
            'call_id': call.call_id,
            'message': 'Dispatch call created'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create dispatch call: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dispatch/calls/<call_id>', methods=['PUT'])
def update_dispatch_call(call_id):
    """Update dispatch call status or assignment."""
    authz, denied = _require_modules('dispatch', 'cad', 'call_logs')
    if denied:
        return denied
    data = request.get_json(silent=True) or {}

    from dispatch_service import assign_units_to_call, close_dispatch_call
    from cad_helpers import log_audit

    try:
        call = None

        if 'units' in data:
            call = assign_units_to_call(call_id, data['units'])
            if not call or call.community_id != authz['community_id']:
                return jsonify({'success': False, 'error': 'Call not found in active community'}), 404
            log_audit('dispatch', 'assign_units', 'DispatchCall', call_id)
            payload = {
                'call_id': call_id,
                'units': data['units'],
                'status': getattr(call, 'status', None),
                'community_id': authz['community_id'],
            }
            socketio.emit('dispatch:units_assigned', payload, room=f"community:{authz['community_id']}:dispatch")
            socketio.emit('dispatch:units_assigned', payload, room=f"community:{authz['community_id']}:police")

        if 'resolution' in data:
            call = close_dispatch_call(call_id, data['resolution'])
            if not call or call.community_id != authz['community_id']:
                return jsonify({'success': False, 'error': 'Call not found in active community'}), 404
            log_audit('dispatch', 'close_call', 'DispatchCall', call_id)
            payload = {
                'call_id': call_id,
                'resolution': data['resolution'],
                'status': getattr(call, 'status', 'Closed'),
                'community_id': authz['community_id'],
            }
            socketio.emit('dispatch:call_closed', payload, room=f"community:{authz['community_id']}:dispatch")
            socketio.emit('dispatch:call_closed', payload, room=f"community:{authz['community_id']}:police")

        if not call:
            return jsonify({'success': False, 'error': 'Call not found'}), 404

        return jsonify({'success': True, 'message': 'Call updated'})
    except Exception as e:
        logger.error(f'Failed to update dispatch call: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dispatch/officer-status', methods=['GET'])
def get_all_officer_status():
    """Get all officer statuses."""
    authz, denied = _require_modules('cad', 'dispatch', 'police')
    if denied:
        return denied
    sessions = scoped_query(OfficerSession, authz['community_id']).all()

    result = [{
        'callsign': s.callsign,
        'officer_name': s.officer_name,
        'department': s.department,
        'status': s.status,
        'logged_in_at': s.logged_in_at.isoformat() if s.logged_in_at else None,
    } for s in sessions]

    return jsonify({'success': True, 'officers': result, 'total': len(result)})


@dispatch_required
@app.route('/api/dispatch/officer-status/<callsign>', methods=['PUT'])
def update_officer_status_route(callsign):
    """Update officer status."""
    authz, denied = _require_modules('dispatch', 'cad')
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    new_status = data.get('status')

    if not new_status:
        return jsonify({'success': False, 'error': 'Status required'}), 400

    from dispatch_service import update_officer_status
    from cad_helpers import log_audit

    try:
        officer_session = update_officer_status(callsign, new_status)
        if not officer_session or officer_session.community_id != authz['community_id']:
            return jsonify({'success': False, 'error': 'Officer not found'}), 404

        log_audit('dispatch', 'update_status', 'OfficerSession', callsign)
        payload = {
            'callsign': callsign,
            'status': new_status,
            'updated_at': datetime.utcnow().isoformat(),
            'community_id': authz['community_id'],
        }
        socketio.emit('officer:status_changed', payload, room=f"community:{authz['community_id']}:dispatch")
        socketio.emit('officer:status_changed', payload, room=f"community:{authz['community_id']}:police")

        return jsonify({'success': True, 'message': 'Status updated'})
    except Exception as e:
        logger.error(f'Failed to update officer status: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@dispatch_required
@app.route('/api/dispatch/panic', methods=['POST'])
def panic_button():
    """Officer panic button - creates urgent dispatch call."""
    data = request.get_json(silent=True) or {}

    callsign = data.get('callsign', 'Unknown')
    location = data.get('location', 'Unknown')

    from dispatch_service import create_dispatch_call as create_call
    from cad_helpers import log_audit

    try:
        call = create_call(
            f'Officer {callsign} - PANIC BUTTON',
            location,
            'Officer Needs Help',
            f'OFFICER PANIC BUTTON ACTIVATED - {callsign} at {location}',
            'Critical'
        )

        log_audit('dispatch', 'panic_button', 'DispatchCall', call.call_id)
        emit_community_event('dispatch:panic', {
            'call_id': call.call_id,
            'callsign': callsign,
            'location': location,
            'priority': 'Critical',
            'message': 'PANIC BUTTON ACTIVATED',
            'created_at': datetime.utcnow().isoformat(),
        })

        return jsonify({
            'success': True,
            'call_id': call.call_id,
            'message': 'PANIC BUTTON ACTIVATED - All units respond'
        })
    except Exception as e:
        logger.error(f'Panic button failed: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# DMV/Records Routes
# ---------------------------------------------------------------------------

@app.route('/api/dmv/license/<license_id>', methods=['GET'])
def get_license(license_id):
    """Get license information."""
    from dmv_service import get_license_by_id

    license = get_license_by_id(license_id)
    if not license:
        return jsonify({'success': False, 'error': 'License not found'}), 404

    return jsonify({
        'success': True,
        'license': {
            'license_id': license.license_id,
            'owner_name': license.owner_name,
            'license_type': license.license_type,
            'status': license.status,
            'issued_date': license.issued_date,
            'expiry_date': license.expiry_date,
            'notes': license.notes,
        }
    })

@app.route('/api/dmv/license/civilian/<civilian_id>', methods=['GET'])
def check_civilian_license(civilian_id):
    """Check license status for a civilian."""
    from dmv_service import check_license_status

    result = check_license_status(civilian_id)
    return jsonify({'success': True, 'data': result})

@dmv_required
@app.route('/api/dmv/license/<license_id>/suspend', methods=['POST'])
def suspend_license_route(license_id):
    """Suspend a driver's license."""
    data = request.get_json(silent=True) or {}
    reason = data.get('reason', 'No reason provided')

    from dmv_service import suspend_license
    from cad_helpers import log_audit

    try:
        license = suspend_license(license_id, reason)
        if not license:
            return jsonify({'success': False, 'error': 'License not found'}), 404

        log_audit('dmv', 'suspend_license', 'License', license_id)
        return jsonify({'success': True, 'message': 'License suspended'})
    except Exception as e:
        logger.error(f'Failed to suspend license: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@dmv_required
@app.route('/api/dmv/license/<license_id>/revoke', methods=['POST'])
def revoke_license_route(license_id):
    """Revoke a driver's license."""
    data = request.get_json(silent=True) or {}
    reason = data.get('reason', 'No reason provided')

    from dmv_service import revoke_license
    from cad_helpers import log_audit

    try:
        license = revoke_license(license_id, reason)
        if not license:
            return jsonify({'success': False, 'error': 'License not found'}), 404

        log_audit('dmv', 'revoke_license', 'License', license_id)
        return jsonify({'success': True, 'message': 'License revoked'})
    except Exception as e:
        logger.error(f'Failed to revoke license: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dmv/vehicle/plate/<plate>', methods=['GET'])
def lookup_plate(plate):
    """Look up vehicle by license plate."""
    from dmv_service import lookup_vehicle_by_plate

    vehicle = lookup_vehicle_by_plate(plate)
    if not vehicle:
        return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

    return jsonify({'success': True, 'vehicle': vehicle})

@app.route('/api/dmv/vehicle/owner/<civilian_id>', methods=['GET'])
def lookup_owner_vehicles(civilian_id):
    """Look up all vehicles owned by a civilian."""
    from dmv_service import lookup_vehicles_by_owner

    vehicles = lookup_vehicles_by_owner(civilian_id)
    return jsonify({'success': True, 'vehicles': vehicles, 'total': len(vehicles)})

@dmv_required
@app.route('/api/dmv/vehicle/stolen/<plate>', methods=['POST'])
def flag_stolen(plate):
    """Flag a vehicle as stolen."""
    from dmv_service import flag_stolen_vehicle
    from cad_helpers import log_audit

    try:
        vehicle = flag_stolen_vehicle(plate)
        if not vehicle:
            return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

        log_audit('dmv', 'flag_stolen', 'Vehicle', plate)
        return jsonify({'success': True, 'message': 'Vehicle flagged as stolen'})
    except Exception as e:
        logger.error(f'Failed to flag stolen vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@dmv_required
@app.route('/api/dmv/vehicle/recovered/<plate>', methods=['POST'])
def recover_vehicle(plate):
    """Mark a stolen vehicle as recovered."""
    from dmv_service import recover_stolen_vehicle
    from cad_helpers import log_audit

    try:
        vehicle = recover_stolen_vehicle(plate)
        if not vehicle:
            return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

        log_audit('dmv', 'recover_vehicle', 'Vehicle', plate)
        return jsonify({'success': True, 'message': 'Vehicle marked as recovered'})
    except Exception as e:
        logger.error(f'Failed to recover vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@dmv_required
@app.route('/api/dmv/vehicle/impound/<plate>', methods=['POST'])
def impound_vehicle_route(plate):
    """Impound a vehicle."""
    data = request.get_json(silent=True) or {}
    reason = data.get('reason', 'No reason provided')

    from dmv_service import impound_vehicle
    from cad_helpers import log_audit

    try:
        vehicle = impound_vehicle(plate, reason)
        if not vehicle:
            return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

        log_audit('dmv', 'impound_vehicle', 'Vehicle', plate)
        return jsonify({'success': True, 'message': 'Vehicle impounded'})
    except Exception as e:
        logger.error(f'Failed to impound vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@dmv_required
@app.route('/api/dmv/vehicle/release/<plate>', methods=['POST'])
def release_vehicle_route(plate):
    """Release an impounded vehicle."""
    from dmv_service import release_impounded_vehicle
    from cad_helpers import log_audit

    try:
        vehicle = release_impounded_vehicle(plate)
        if not vehicle:
            return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

        log_audit('dmv', 'release_vehicle', 'Vehicle', plate)
        return jsonify({'success': True, 'message': 'Vehicle released from impound'})
    except Exception as e:
        logger.error(f'Failed to release vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# DMV Vehicle CRUD Routes (Phase 1: Dedicated backend persistence)
# ---------------------------------------------------------------------------

@app.route('/api/dmv/vehicles', methods=['GET'])
def get_all_vehicles():
    authz, denied = _require_modules('dmv', 'police', 'cad', 'dispatch')
    if denied:
        return denied
    community_id = authz['community_id']
    """List all vehicles in DMV database."""
    try:
        vehicles = scoped_query(Vehicle).order_by(Vehicle.created_at.desc()).all()
        result = [vehicle_to_dict(v) for v in vehicles]
        return jsonify({'success': True, 'vehicles': result, 'total': len(result)})
    except Exception as e:
        logger.error(f'Failed to get vehicles: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@dmv_required
@app.route('/api/dmv/vehicles', methods=['POST'])
def create_vehicle():
    """Create a new vehicle registration in DMV."""
    data = request.get_json(silent=True) or {}

    # Field mapping: frontend camelCase -> database snake_case
    plate = (data.get('plateau Number') or data.get('plateNumber') or data.get('plate') or '').strip()
    if not plate:
        return jsonify({'success': False, 'error': 'plate number is required'}), 400

    # Check for duplicates
    authz, denied = _require_modules('dmv')
    if denied:
        return denied
    community_id = authz['community_id']

    existing = scoped_query(Vehicle, community_id).filter_by(plate=plate).first()
    if existing:
        return jsonify({'success': False, 'error': f'Vehicle with plate {plate} already exists'}), 409

    try:
        owner_civilian_id = data.get('ownerCivilianId') or data.get('owner_civilian_id') or ''
        vehicle = Vehicle(community_id=community_id,
            vehicle_id=f"VEH-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}",
            owner_civilian_id=owner_civilian_id,
            plate=plate,
            vin=data.get('vin', ''),
            make=(data.get('vehicleMake') or data.get('make') or '').strip(),
            model=(data.get('vehicleModel') or data.get('model') or '').strip(),
            color=(data.get('vehicleColor') or data.get('color') or '').strip(),
            registration_status=(data.get('registrationStatus') or data.get('registration_status') or 'Valid').strip(),
            insurance_status=(data.get('insuranceStatus') or data.get('insurance_status') or 'Valid').strip(),
            notes=data.get('notes', ''),
            owner_name=data.get('ownerName') or data.get('owner_name') or '',
        )
        db.session.add(vehicle)
        db.session.commit()

        from cad_helpers import log_audit
        log_audit('dmv', 'create_vehicle', 'Vehicle', vehicle.vehicle_id)
        logger.info(f'Vehicle registered: {plate} owner={vehicle.owner_name}')
        return jsonify({
            'success': True,
            'vehicle_id': vehicle.vehicle_id,
            'vehicle': vehicle_to_dict(vehicle)
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@dmv_required
@app.route('/api/dmv/vehicles/<plate>', methods=['PUT'])
def update_vehicle(plate):
    """Update an existing vehicle registration."""
    data = request.get_json(silent=True) or {}

    try:
        authz, denied = _require_modules('dmv')
        if denied:
            return denied
        community_id = authz['community_id']
        vehicle = scoped_query(Vehicle, community_id).filter_by(plate=plate).first()
        if not vehicle:
            return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

        # Update mappable fields from frontend -> database
        if 'vehicleMake' in data or 'make' in data:
            vehicle.make = (data.get('vehicleMake') or data.get('make') or '').strip()
        if 'vehicleModel' in data or 'model' in data:
            vehicle.model = (data.get('vehicleModel') or data.get('model') or '').strip()
        if 'vehicleColor' in data or 'color' in data:
            vehicle.color = (data.get('vehicleColor') or data.get('color') or '').strip()
        if 'insuranceStatus' in data or 'insurance_status' in data:
            vehicle.insurance_status = (data.get('insuranceStatus') or data.get('insurance_status') or 'Valid').strip()
        if 'registrationStatus' in data or 'registration_status' in data:
            vehicle.registration_status = (data.get('registrationStatus') or data.get('registration_status') or 'Valid').strip()
        if 'ownerName' in data or 'owner_name' in data:
            vehicle.owner_name = (data.get('ownerName') or data.get('owner_name') or '').strip()
        if 'notes' in data:
            vehicle.notes = data.get('notes', '')
        if 'vin' in data:
            vehicle.vin = data.get('vin', '')

        vehicle.updated_at = datetime.utcnow()
        db.session.commit()

        logger.info(f'Vehicle updated: {plate}')
        return jsonify({'success': True, 'vehicle': vehicle_to_dict(vehicle)})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to update vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@dmv_required
@app.route('/api/dmv/vehicles/<plate>', methods=['DELETE'])
def delete_vehicle(plate):
    """Delete a vehicle registration (admin only)."""
    try:
        authz, denied = _require_modules('dmv')
        if denied:
            return denied
        community_id = authz['community_id']
        vehicle = scoped_query(Vehicle, community_id).filter_by(plate=plate).first()
        if not vehicle:
            return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

        db.session.delete(vehicle)
        db.session.commit()

        logger.info(f'Vehicle deleted: {plate}')
        return jsonify({'success': True, 'message': 'Vehicle deleted'})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to delete vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# DMV License CRUD Routes (Phase 1: Dedicated backend persistence)
# ---------------------------------------------------------------------------

@app.route('/api/dmv/licenses', methods=['GET'])
def get_all_licenses():
    authz, denied = _require_modules('dmv', 'police', 'cad', 'dispatch')
    if denied:
        return denied
    community_id = authz['community_id']
    """List all licenses in DMV database."""
    try:
        licenses = scoped_query(License).order_by(License.created_at.desc()).all()
        result = [license_to_dict(l) for l in licenses]
        return jsonify({'success': True, 'licenses': result, 'total': len(result)})
    except Exception as e:
        logger.error(f'Failed to get licenses: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@dmv_required
@app.route('/api/dmv/licenses', methods=['POST'])
def create_license():
    """Create a new driver license in DMV."""
    data = request.get_json(silent=True) or {}

    # Field mapping: frontend camelCase -> database snake_case
    owner_name = (data.get('licenseName') or data.get('ownerName') or data.get('owner_name') or '').strip()
    if not owner_name:
        return jsonify({'success': False, 'error': 'owner name is required'}), 400

    license_type = (data.get('licenseClass') or data.get('licenseType') or data.get('license_type') or '').strip()
    if not license_type:
        return jsonify({'success': False, 'error': 'license class/type is required'}), 400

    authz, denied = _require_modules('dmv')
    if denied:
        return denied
    community_id = authz['community_id']

    try:
        license_id = f"LIC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        license_obj = License(community_id=community_id,
            license_id=license_id,
            owner_name=owner_name,
            license_type=license_type,
            status=(data.get('status') or 'Valid').strip(),
            issued_date=data.get('licenseIssuedDate') or data.get('issued_date') or '',
            expiry_date=data.get('licenseExpiration') or data.get('expiryDate') or data.get('expiry_date') or '',
            notes=data.get('notes', '') or data.get('restrictions', ''),
        )
        db.session.add(license_obj)
        db.session.commit()

        from cad_helpers import log_audit
        log_audit('dmv', 'create_license', 'License', license_id)
        logger.info(f'License issued: {license_id} to {owner_name} class={license_type}')
        return jsonify({
            'success': True,
            'license_id': license_id,
            'license': license_to_dict(license_obj)
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create license: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@dmv_required
@app.route('/api/dmv/licenses/<license_id>', methods=['PUT'])
def update_license_route(license_id):
    """Update an existing driver license."""
    data = request.get_json(silent=True) or {}

    try:
        authz, denied = _require_modules('dmv')
        if denied:
            return denied
        community_id = authz['community_id']
        license_obj = scoped_query(License, community_id).filter_by(license_id=license_id).first()
        if not license_obj:
            return jsonify({'success': False, 'error': 'License not found'}), 404

        # Update mappable fields
        if 'ownerName' in data or 'licenseName' in data or 'owner_name' in data:
            val = data.get('ownerName') or data.get('licenseName') or data.get('owner_name')
            if val:
                license_obj.owner_name = val.strip()
        if 'licenseClass' in data or 'licenseType' in data or 'license_type' in data:
            val = data.get('licenseClass') or data.get('licenseType') or data.get('license_type')
            if val:
                license_obj.license_type = val.strip()
        if 'status' in data:
            license_obj.status = data.get('status', 'Valid').strip()
        if 'licenseExpiration' in data or 'expiryDate' in data or 'expiry_date' in data:
            license_obj.expiry_date = data.get('licenseExpiration') or data.get('expiryDate') or data.get('expiry_date') or ''
        if 'notes' in data:
            license_obj.notes = data.get('notes', '')
        if 'restrictions' in data:
            license_obj.notes = data.get('restrictions', '')

        license_obj.updated_at = datetime.utcnow()
        db.session.commit()

        logger.info(f'License updated: {license_id}')
        return jsonify({'success': True, 'license': license_to_dict(license_obj)})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to update license: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@dmv_required
@app.route('/api/dmv/licenses/<license_id>', methods=['DELETE'])
def delete_license_route(license_id):
    """Delete a driver license (admin only)."""
    try:
        authz, denied = _require_modules('dmv')
        if denied:
            return denied
        community_id = authz['community_id']
        license_obj = scoped_query(License, community_id).filter_by(license_id=license_id).first()
        if not license_obj:
            return jsonify({'success': False, 'error': 'License not found'}), 404

        db.session.delete(license_obj)
        db.session.commit()

        logger.info(f'License deleted: {license_id}')
        return jsonify({'success': True, 'message': 'License deleted'})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to delete license: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Business CRUD Routes (Phase 1: Complete business persistence)
# ---------------------------------------------------------------------------

def business_to_dict(b):
    """Convert Business model to JSON response dict."""
    return {
        'business_id': b.business_id,
        'owner_civilian_id': b.owner_civilian_id or '',
        'business_name': b.business_name or '',
        'business_type': b.business_type or '',
        'license_status': b.license_status or 'Active',
        'address': b.address or '',
        'employees': b.employees or 0,
        'inspection_notes': b.inspection_notes or '',
        'legal_flags': b.legal_flags or '',
        'created_at': b.created_at.isoformat() if b.created_at else None,
        'updated_at': b.updated_at.isoformat() if b.updated_at else None,
    }


@app.route('/api/businesses', methods=['GET'])
def get_all_businesses():
    authz, denied = _require_modules('businesses', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    """List all businesses in the system."""
    try:
        businesses = scoped_query(Business, community_id).order_by(Business.created_at.desc()).all()
        result = [business_to_dict(b) for b in businesses]
        return jsonify({'success': True, 'businesses': result, 'total': len(result)})
    except Exception as e:
        logger.error(f'Failed to get businesses: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/businesses', methods=['POST'])
def create_business():
    authz, denied = _require_modules('businesses', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    """Create a new business registration."""
    data = request.get_json(silent=True) or {}

    business_name = (data.get('businessName') or data.get('business_name') or '').strip()
    if not business_name:
        return jsonify({'success': False, 'error': 'business_name is required'}), 400

    try:
        business_id = f"BIZ-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        business = Business(
            business_id=business_id,
            community_id=community_id,
            owner_civilian_id=data.get('ownerCivilianId') or data.get('owner_civilian_id') or '',
            business_name=business_name,
            business_type=(data.get('businessType') or data.get('business_type') or '').strip(),
            license_status=(data.get('licenseStatus') or data.get('license_status') or 'Active').strip(),
            address=data.get('address') or data.get('desiredLocation') or '',
            employees=int(data.get('employees', 0)) if data.get('employees') else 0,
            inspection_notes=data.get('inspectionNotes') or data.get('inspection_notes') or '',
            legal_flags=data.get('legalFlags') or data.get('legal_flags') or data.get('illegalDisclosure') or '',
        )
        db.session.add(business)
        db.session.commit()

        logger.info(f'Business registered: {business_id} name={business_name} type={business.business_type}')

        # Log audit trail
        try:
            from cad_helpers import log_audit
            log_audit('business', 'create', 'Business', business_id)
        except:
            pass

        return jsonify({
            'success': True,
            'business_id': business_id,
            'business': business_to_dict(business)
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to create business: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/businesses/<business_id>', methods=['GET'])
def get_business(business_id):
    authz, denied = _require_modules('businesses', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    """Get a specific business by ID."""
    try:
        business = scoped_query(Business, community_id).filter_by(business_id=business_id).first()
        if not business:
            return jsonify({'success': False, 'error': 'Business not found'}), 404

        return jsonify({'success': True, 'business': business_to_dict(business)})
    except Exception as e:
        logger.error(f'Failed to get business: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/businesses/<business_id>', methods=['PUT'])
def update_business(business_id):
    authz, denied = _require_modules('businesses', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    """Update an existing business."""
    data = request.get_json(silent=True) or {}

    try:
        business = scoped_query(Business, community_id).filter_by(business_id=business_id).first()
        if not business:
            return jsonify({'success': False, 'error': 'Business not found'}), 404

        # Update fields if provided
        if 'businessName' in data or 'business_name' in data:
            val = data.get('businessName') or data.get('business_name')
            if val:
                business.business_name = val.strip()
        if 'businessType' in data or 'business_type' in data:
            val = data.get('businessType') or data.get('business_type')
            if val:
                business.business_type = val.strip()
        if 'licenseStatus' in data or 'license_status' in data:
            val = data.get('licenseStatus') or data.get('license_status')
            if val:
                business.license_status = val.strip()
        if 'address' in data or 'desiredLocation' in data:
            business.address = (data.get('address') or data.get('desiredLocation') or '').strip()
        if 'employees' in data:
            try:
                business.employees = int(data.get('employees', 0))
            except:
                pass
        if 'inspectionNotes' in data or 'inspection_notes' in data:
            business.inspection_notes = (data.get('inspectionNotes') or data.get('inspection_notes') or '').strip()
        if 'legalFlags' in data or 'legal_flags' in data or 'illegalDisclosure' in data:
            val = data.get('legalFlags') or data.get('legal_flags') or data.get('illegalDisclosure')
            if val:
                business.legal_flags = val.strip()
        if 'ownerCivilianId' in data or 'owner_civilian_id' in data:
            val = data.get('ownerCivilianId') or data.get('owner_civilian_id')
            if val:
                business.owner_civilian_id = val.strip()

        business.updated_at = datetime.utcnow()
        db.session.commit()

        logger.info(f'Business updated: {business_id}')
        return jsonify({'success': True, 'business': business_to_dict(business)})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to update business: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/businesses/<business_id>', methods=['DELETE'])
def delete_business(business_id):
    authz, denied = _require_modules('businesses', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    """Delete a business (admin only)."""
    try:
        business = scoped_query(Business, community_id).filter_by(business_id=business_id).first()
        if not business:
            return jsonify({'success': False, 'error': 'Business not found'}), 404

        db.session.delete(business)
        db.session.commit()

        logger.info(f'Business deleted: {business_id}')
        return jsonify({'success': True, 'message': 'Business deleted'})
    except Exception as e:
        db.session.rollback()
        logger.error(f'Failed to delete business: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/applications', methods=['GET'])
@admin_required
def list_applications_admin():
    """List applications (admin endpoint)."""
    return list_applications()

# ---------------------------------------------------------------------------
# World Realism Routes
# ---------------------------------------------------------------------------

@app.route('/api/world/address', methods=['GET'])
def generate_address_route():
    """Generate a random address."""
    from world_realism_service import generate_address

    neighborhood = request.args.get('neighborhood')
    address = generate_address(neighborhood)

    return jsonify({'success': True, 'address': address})

@app.route('/api/world/plate', methods=['GET'])
def generate_plate_route():
    """Generate a random license plate."""
    from world_realism_service import generate_plate

    plate = generate_plate()
    return jsonify({'success': True, 'plate': plate})

@app.route('/api/world/vehicle', methods=['GET'])
def generate_vehicle_route():
    """Generate a random vehicle."""
    from world_realism_service import generate_vehicle

    vehicle = generate_vehicle()
    return jsonify({'success': True, 'vehicle': vehicle})

@app.route('/api/world/business', methods=['GET'])
def generate_business_route():
    """Generate a random business."""
    from world_realism_service import generate_business

    neighborhood = request.args.get('neighborhood')
    business = generate_business(neighborhood)

    return jsonify({'success': True, 'business': business})

@app.route('/api/world/name', methods=['GET'])
def generate_name_route():
    """Generate a random name."""
    from world_realism_service import generate_name

    gender = request.args.get('gender', 'random')
    name = generate_name(gender)

    return jsonify({'success': True, 'name': name})

@app.route('/api/world/rp-history', methods=['GET'])
def generate_rp_history_route():
    """Generate a random RP history."""
    from world_realism_service import generate_rp_history

    history = generate_rp_history()
    return jsonify({'success': True, 'history': history})

@app.route('/api/world/call', methods=['GET'])
def generate_call_route():
    """Generate a random dispatch call."""
    from world_realism_service import generate_dispatch_call

    call = generate_dispatch_call()
    return jsonify({'success': True, 'call': call})

@app.route('/api/world/neighborhoods', methods=['GET'])
def get_neighborhoods():
    """Get list of all neighborhoods."""
    from world_realism_service import NEIGHBORHOODS

    return jsonify({'success': True, 'neighborhoods': NEIGHBORHOODS})


# ---------------------------------------------------------------------------
# Relationship Routes
# ---------------------------------------------------------------------------

@app.route('/api/relationships/link-vehicle', methods=['POST'])
@admin_required
def link_vehicle_route():
    """Link civilian to vehicle."""
    data = request.get_json(silent=True) or {}
    civilian_id = data.get('civilian_id')
    vehicle_id = data.get('vehicle_id')

    if not civilian_id or not vehicle_id:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400

    from relationships_service import link_civilian_to_vehicle
    from cad_helpers import log_audit

    try:
        vehicle = link_civilian_to_vehicle(civilian_id, vehicle_id)
        if not vehicle:
            return jsonify({'success': False, 'error': 'Vehicle not found'}), 404

        log_audit('relationships', 'link_vehicle', 'Vehicle', vehicle_id)
        return jsonify({'success': True, 'message': 'Vehicle linked to civilian'})
    except Exception as e:
        logger.error(f'Failed to link vehicle: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/relationships/gang-crew/<gang_name>', methods=['GET'])
def get_gang_crew_route(gang_name):
    """Get gang crew with relationships."""
    from relationships_service import get_gang_crew

    crew = get_gang_crew(gang_name)
    return jsonify({'success': True, 'crew': crew, 'total': len(crew)})


@app.route('/api/relationships/criminal-history/<civilian_id>', methods=['GET'])
def get_criminal_history_route(civilian_id):
    """Get complete criminal history."""
    from relationships_service import get_civilian_criminal_history

    history = get_civilian_criminal_history(civilian_id)
    if not history:
        return jsonify({'success': False, 'error': 'Civilian not found'}), 404

    return jsonify({'success': True, 'history': history})


@app.route('/api/relationships/create-arrest', methods=['POST'])
@admin_required
def create_arrest_route():
    """Create arrest and update criminal history."""
    data = request.get_json(silent=True) or {}

    required = ['civilian_id', 'charges', 'arresting_officer', 'location', 'narrative']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'success': False, 'error': f'Missing fields: {", ".join(missing)}'}), 400

    from relationships_service import create_arrest_record, create_warrant_from_arrest
    from cad_helpers import log_audit

    try:
        arrest = create_arrest_record(
            data['civilian_id'],
            data['charges'],
            data['arresting_officer'],
            data['location'],
            data['narrative']
        )
        _ensure_arrest_custody_and_hearing(arrest)
        db.session.commit()

        # Auto-create warrant if requested
        if data.get('create_warrant'):
            warrant = create_warrant_from_arrest(
                arrest.arrest_id,
                data['civilian_id'],
                data['charges'],
                data.get('probable_cause', 'Arrest warrant')
            )
            log_audit('relationships', 'create_warrant', 'Warrant', warrant.warrant_id)

        log_audit('relationships', 'create_arrest', 'Arrest', arrest.arrest_id)
        return jsonify({'success': True, 'arrest_id': arrest.arrest_id})
    except Exception as e:
        logger.error(f'Failed to create arrest: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/relationships/warrant-check/<plate>', methods=['GET'])
def warrant_check_route(plate):
    """Check for warrants on traffic stop."""
    from relationships_service import check_warrant_on_traffic_stop

    result = check_warrant_on_traffic_stop(plate)
    if not result:
        return jsonify({'success': True, 'warrants': None})

    return jsonify({'success': True, 'warrants': result})


@app.route('/api/relationships/family', methods=['POST'])
@admin_required
def create_family_route():
    """Create family relationship."""
    data = request.get_json(silent=True) or {}

    from relationships_service import create_family_relationship
    from cad_helpers import log_audit

    try:
        assoc = create_family_relationship(
            data['civilian_id1'],
            data['civilian_id2'],
            data.get('relationship', 'Family')
        )

        log_audit('relationships', 'create_family', 'KnownAssociate', assoc.associate_id)
        return jsonify({'success': True, 'message': 'Family relationship created'})
    except Exception as e:
        logger.error(f'Failed to create family: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/relationships/employment', methods=['POST'])
@admin_required
def create_employment_route():
    """Link civilian to business as employee."""
    data = request.get_json(silent=True) or {}

    from relationships_service import create_employment_relationship
    from cad_helpers import log_audit

    try:
        assoc = create_employment_relationship(
            data['civilian_id'],
            data['business_id']
        )

        log_audit('relationships', 'create_employment', 'KnownAssociate', assoc.associate_id)
        return jsonify({'success': True, 'message': 'Employment relationship created'})
    except Exception as e:
        logger.error(f'Failed to create employment: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Evidence Routes
# ---------------------------------------------------------------------------

@police_required
@app.route('/api/evidence/create', methods=['POST'])
def create_evidence_route():
    """Create evidence record."""
    data = request.get_json(silent=True) or {}

    required = ['case_id', 'arrest_id', 'evidence_type', 'description', 'collected_by', 'location_found']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'success': False, 'error': f'Missing fields: {", ".join(missing)}'}), 400

    from evidence_service import create_evidence
    from cad_helpers import log_audit

    try:
        evidence = create_evidence(
            data['case_id'],
            data['arrest_id'],
            data['evidence_type'],
            data['description'],
            data['collected_by'],
            data['location_found']
        )

        log_audit('evidence', 'create_evidence', 'Evidence', evidence.evidence_id)
        return jsonify({'success': True, 'evidence_id': evidence.evidence_id})
    except Exception as e:
        logger.error(f'Failed to create evidence: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/evidence/<evidence_id>/chain-of-custody', methods=['GET'])
def get_evidence_custody_route(evidence_id):
    """Get chain of custody for evidence."""
    from evidence_service import get_evidence_chain_of_custody

    custody = get_evidence_chain_of_custody(evidence_id)
    if not custody:
        return jsonify({'success': False, 'error': 'Evidence not found'}), 404

    return jsonify({'success': True, 'custody': custody})


@police_required
@app.route('/api/evidence/<evidence_id>/transfer', methods=['POST'])
def transfer_evidence_route(evidence_id):
    """Transfer evidence custody."""
    data = request.get_json(silent=True) or {}

    from evidence_service import transfer_evidence_custody
    from cad_helpers import log_audit

    try:
        evidence = transfer_evidence_custody(
            evidence_id,
            data.get('from_officer', 'Unknown'),
            data.get('to_officer', 'Unknown'),
            data.get('reason', 'Transfer')
        )

        if not evidence:
            return jsonify({'success': False, 'error': 'Evidence not found'}), 404

        log_audit('evidence', 'transfer_evidence', 'Evidence', evidence_id)
        return jsonify({'success': True, 'message': 'Evidence transferred'})
    except Exception as e:
        logger.error(f'Failed to transfer evidence: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@police_required
@app.route('/api/evidence/<evidence_id>/release', methods=['POST'])
def release_evidence_route(evidence_id):
    """Release evidence from storage."""
    data = request.get_json(silent=True) or {}

    from evidence_service import release_evidence
    from cad_helpers import log_audit

    try:
        evidence = release_evidence(evidence_id, data.get('reason', 'Case closed'))
        if not evidence:
            return jsonify({'success': False, 'error': 'Evidence not found'}), 404

        log_audit('evidence', 'release_evidence', 'Evidence', evidence_id)
        return jsonify({'success': True, 'message': 'Evidence released'})
    except Exception as e:
        logger.error(f'Failed to release evidence: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500



# ---------------------------------------------------------------------------
# Tenant-scoped CAD Case Workflow Routes
# ---------------------------------------------------------------------------

CASE_TYPES = {'incident', 'arrest', 'warrant', 'use_of_force', 'court'}
CASE_STATUSES_CAD = {'open', 'pending_review', 'closed', 'archived'}
CHARGE_SEVERITIES = {'infraction', 'misdemeanor', 'felony'}


def _cad_json_error(message, status=400):
    return jsonify({'success': False, 'error': message, 'request_id': getattr(g, 'request_id', None)}), status


def _require_cad_community():
    denied = require_police_cad_access()
    if denied:
        return None, denied
    community_id = get_current_community_id()
    if not community_id:
        return None, _cad_json_error('community_id is required for CAD data access', 400)
    return community_id, None


def _actor_name():
    return session.get('username') or session.get('email') or str(session.get('user_id') or 'system')


def _cad_audit(action, community_id, case_id=None, details=None):
    audit = CadAuditLog(
        audit_id=f"CAD-AUD-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3)}",
        acting_user_id=session.get('user_id'),
        community_id=community_id,
        case_id=case_id,
        action=action,
        request_id=getattr(g, 'request_id', None),
        ip_address=getattr(g, 'client_ip', None) or request.remote_addr,
        details=json.dumps(details or {}, default=str),
    )
    db.session.add(audit)


def _split_csv(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(',') if part.strip()]


def _join_values(value):
    if isinstance(value, list):
        return ','.join(str(v).strip() for v in value if str(v).strip())
    return str(value or '').strip()


def _case_public_id(case):
    return case.case_number or case.case_id


def _case_to_dict(case, include_related=False):
    charges = scoped_query(CaseCharge, case.community_id).filter_by(case_id=case.case_id).order_by(CaseCharge.created_at.asc()).all()
    linked_evidence = _split_csv(case.linked_evidence_ids or case.evidence_ids)
    payload = {
        'id': case.case_id,
        'case_id': case.case_id,
        'case_number': _case_public_id(case),
        'community_id': case.community_id,
        'title': case.title or '',
        'type': case.case_type or 'incident',
        'status': (case.status or 'open').lower(),
        'priority': case.priority or 'medium',
        'location': case.location or '',
        'involved_civilians': _split_csv(case.involved_civilians or case.defendant_civilian_id),
        'involved_officers': _split_csv(case.involved_officers),
        'linked_911_call_id': case.linked_911_call_id or '',
        'linked_arrest_id': case.linked_arrest_id or case.arrest_id or '',
        'linked_warrant_id': case.linked_warrant_id or '',
        'linked_evidence_ids': linked_evidence,
        'report_notes': case.report_notes or case.prosecutor_notes or '',
        'created_by': case.created_by or '',
        'assigned_to': case.assigned_to or '',
        'created_at': case.created_at.isoformat() if case.created_at else None,
        'updated_at': case.updated_at.isoformat() if case.updated_at else None,
        'charges': [_charge_to_dict(c) for c in charges],
    }
    if include_related:
        payload['evidence'] = [_evidence_workflow_to_dict(e) for e in scoped_query(Evidence, case.community_id).filter_by(case_number=_case_public_id(case)).order_by(Evidence.created_at.desc()).all()]
    return payload


def _charge_to_dict(charge):
    return {
        'charge_id': charge.charge_id,
        'case_id': charge.case_id,
        'charge_name': charge.charge_name,
        'penal_code': charge.penal_code or '',
        'severity': charge.severity or 'misdemeanor',
        'counts': charge.counts or 1,
        'recommended_fine': charge.recommended_fine or '',
        'recommended_jail_time': charge.recommended_jail_time or '',
        'notes': charge.notes or '',
        'created_at': charge.created_at.isoformat() if charge.created_at else None,
        'updated_at': charge.updated_at.isoformat() if charge.updated_at else None,
    }


def _evidence_workflow_to_dict(evidence):
    return {
        'evidence_id': evidence.evidence_id,
        'case_number': evidence.case_number or '',
        'evidence_type': evidence.evidence_type or 'Other',
        'description': evidence.evidence_description or '',
        'officer': evidence.officer or evidence.collected_by or '',
        'clip_link': evidence.clip_link or '',
        'screenshot_link': evidence.screenshot_link or '',
        'storage_status': evidence.storage_status or evidence.status or 'Logged',
        'chain_of_custody': evidence.chain_of_custody or evidence.notes or '',
        'created_at': evidence.created_at.isoformat() if evidence.created_at else None,
        'updated_at': evidence.updated_at.isoformat() if evidence.updated_at else None,
    }


def _apply_case_payload(case, data, creating=False):
    case.case_number = case.case_number or data.get('case_number') or case.case_id
    case.title = (data.get('title') or case.title or 'Untitled CAD Case').strip()
    case.case_type = (data.get('type') or data.get('case_type') or case.case_type or 'incident').strip().lower()
    if case.case_type not in CASE_TYPES:
        case.case_type = 'incident'
    case.status = (data.get('status') or case.status or 'open').strip().lower()
    if case.status not in CASE_STATUSES_CAD:
        case.status = 'open'
    case.priority = (data.get('priority') or case.priority or 'medium').strip()
    case.location = data.get('location', case.location) or ''
    case.involved_civilians = _join_values(data.get('involved_civilians', case.involved_civilians or case.defendant_civilian_id))
    case.involved_officers = _join_values(data.get('involved_officers', case.involved_officers))
    case.linked_911_call_id = data.get('linked_911_call_id', case.linked_911_call_id) or ''
    case.linked_arrest_id = data.get('linked_arrest_id', data.get('arrest_id', case.linked_arrest_id or case.arrest_id)) or ''
    case.linked_warrant_id = data.get('linked_warrant_id', case.linked_warrant_id) or ''
    case.linked_evidence_ids = _join_values(data.get('linked_evidence_ids', case.linked_evidence_ids or case.evidence_ids))
    case.evidence_ids = case.linked_evidence_ids
    case.arrest_id = case.linked_arrest_id
    case.defendant_civilian_id = _split_csv(case.involved_civilians)[0] if _split_csv(case.involved_civilians) else case.defendant_civilian_id
    case.report_notes = data.get('report_notes', data.get('notes', case.report_notes)) or ''
    case.charges = data.get('charges', case.charges) or case.charges
    case.created_by = case.created_by or data.get('created_by') or _actor_name()
    case.assigned_to = data.get('assigned_to', case.assigned_to) or ''
    case.updated_at = datetime.utcnow()
    if creating and not case.created_at:
        case.created_at = datetime.utcnow()


@app.route('/api/cad/cases', methods=['GET'])
def cad_cases_list():
    community_id, error = _require_cad_community()
    if error:
        return error
    status = request.args.get('status')
    query = scoped_query(CaseFile, community_id)
    if status:
        query = query.filter(func.lower(CaseFile.status) == status.lower())
    cases = query.order_by(CaseFile.created_at.desc()).limit(200).all()
    return jsonify({'success': True, 'cases': [_case_to_dict(case) for case in cases]})


@app.route('/api/cad/cases', methods=['POST'])
def cad_cases_create():
    community_id, error = _require_cad_community()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    case_number = data.get('case_number') or f"CASE-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
    case = CaseFile(community_id=community_id, case_id=case_number, case_number=case_number, created_at=datetime.utcnow())
    _apply_case_payload(case, data, creating=True)
    db.session.add(case)
    _cad_audit('case_created', community_id, case.case_id, {'case_number': case.case_number})
    db.session.commit()
    return jsonify({'success': True, 'case': _case_to_dict(case, include_related=True), 'case_number': _case_public_id(case)}), 201


@app.route('/api/cad/cases/<case_id>', methods=['GET'])
def cad_cases_get(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id)).first()
    if not case:
        return _cad_json_error('Case not found', 404)
    return jsonify({'success': True, 'case': _case_to_dict(case, include_related=True)})


@app.route('/api/cad/cases/<case_id>', methods=['PATCH'])
def cad_cases_patch(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id)).first()
    if not case:
        return _cad_json_error('Case not found', 404)
    _apply_case_payload(case, request.get_json(silent=True) or {})
    _cad_audit('case_updated', community_id, case.case_id)
    db.session.commit()
    return jsonify({'success': True, 'case': _case_to_dict(case, include_related=True)})


@app.route('/api/cad/cases/<case_id>/close', methods=['POST'])
def cad_cases_close(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id)).first()
    if not case:
        return _cad_json_error('Case not found', 404)
    data = request.get_json(silent=True) or {}
    case.status = 'closed'
    case.outcome = data.get('outcome') or data.get('notes') or case.outcome
    case.updated_at = datetime.utcnow()
    _cad_audit('case_closed', community_id, case.case_id)
    db.session.commit()
    return jsonify({'success': True, 'case': _case_to_dict(case, include_related=True)})


@app.route('/api/cad/911-calls/<call_id>', methods=['PATCH'])
def cad_911_update(call_id):
    authz, denied = _require_modules('cad', 'dispatch', 'police', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    data = request.get_json(silent=True) or {}
    call = scoped_query(DispatchCall, community_id).filter_by(call_id=call_id).first()
    if not call:
        return _cad_json_error('911 call not found', 404)
    if 'assigned_unit' in data or 'assignedUnit' in data or 'units' in data:
        units = data.get('assigned_unit') or data.get('assignedUnit') or data.get('units')
        call.assigned_unit = ','.join(units) if isinstance(units, list) else str(units or '')
    if 'status' in data:
        call.status = data.get('status') or call.status
    if 'notes' in data or 'dispatch_notes' in data:
        note = data.get('notes') or data.get('dispatch_notes') or ''
        call.notes = ((call.notes or '') + f"\n[{datetime.utcnow().isoformat()}] {note}").strip()
    if 'location' in data:
        call.location = data.get('location') or call.location
    if data.get('resolved') or data.get('status') == 'Resolved':
        call.status = 'Resolved'
    call.updated_at = datetime.utcnow()
    _cad_audit('911_call_updated', community_id, None, {'call_id': call.call_id})
    db.session.commit()
    payload = {'call_id': call.call_id, 'status': call.status, 'assigned_unit': call.assigned_unit, 'community_id': community_id}
    socketio.emit('dispatch:call_updated', payload, room=f"community:{community_id}:dispatch")
    socketio.emit('dispatch:call_updated', payload, room=f"community:{community_id}:police")
    socketio.emit('dispatch:call_updated', payload, room=f"community:{community_id}:admin")
    return jsonify({'success': True, 'call_id': call.call_id, 'status': call.status})


@app.route('/api/cad/911-calls/<call_id>/convert-to-case', methods=['POST'])
def cad_911_convert_to_case(call_id):
    authz, denied = _require_modules('cad', 'dispatch', 'police', 'community_admin')
    if denied:
        return denied
    community_id = authz['community_id']
    data = request.get_json(silent=True) or {}
    call = scoped_query(DispatchCall, community_id).filter_by(call_id=call_id).first()
    if not call:
        return _cad_json_error('911 call not found', 404)
    existing = scoped_query(CaseFile, community_id).filter_by(linked_911_call_id=call.call_id).first()
    if existing:
        return jsonify({'success': True, 'case_number': _case_public_id(existing), 'redirect': f'/c/{getattr(g, "community", None).slug if getattr(g, "community", None) else ""}/cad?case={_case_public_id(existing)}', 'case': _case_to_dict(existing)})
    case_number = f"CASE-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
    case = CaseFile(
        community_id=community_id,
        case_id=case_number,
        case_number=case_number,
        title=data.get('title') or f"{call.priority or '911'} Incident - {call.location or 'Unknown Location'}",
        case_type='incident',
        status='open',
        priority=data.get('priority') or call.priority or 'medium',
        location=data.get('location') or call.location or '',
        involved_officers=call.assigned_unit or '',
        linked_911_call_id=call.call_id,
        report_notes=data.get('notes') or call.description or '',
        created_by=_actor_name(),
        assigned_to=call.assigned_unit or '',
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    call.status = data.get('call_status') or 'Converted'
    call.updated_at = datetime.utcnow()
    db.session.add(case)
    _cad_audit('call_converted', community_id, case.case_id, {'call_id': call.call_id})
    db.session.commit()
    socketio.emit('cad:call_converted', {'call_id': call.call_id, 'case_number': _case_public_id(case), 'community_id': community_id}, room=f"community:{community_id}:dispatch")
    socketio.emit('cad:call_converted', {'call_id': call.call_id, 'case_number': _case_public_id(case), 'community_id': community_id}, room=f"community:{community_id}:police")
    slug = getattr(getattr(g, 'community', None), 'slug', '')
    return jsonify({'success': True, 'case_number': _case_public_id(case), 'redirect': f'/c/{slug}/cad?case={_case_public_id(case)}', 'case': _case_to_dict(case, include_related=True)})


@app.route('/api/cad/cases/<case_id>/charges', methods=['POST'])
def cad_case_add_charge(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id)).first()
    if not case:
        return _cad_json_error('Case not found', 404)
    data = request.get_json(silent=True) or {}
    if not data.get('charge_name'):
        return _cad_json_error('charge_name is required', 400)
    charge = CaseCharge(
        charge_id=f"CHG-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(2).upper()}",
        community_id=community_id,
        case_id=case.case_id,
        charge_name=data.get('charge_name'),
        penal_code=data.get('penal_code') or '',
        severity=(data.get('severity') or 'misdemeanor').lower() if (data.get('severity') or 'misdemeanor').lower() in CHARGE_SEVERITIES else 'misdemeanor',
        counts=int(data.get('counts') or 1),
        recommended_fine=str(data.get('recommended_fine') or ''),
        recommended_jail_time=str(data.get('recommended_jail_time') or ''),
        notes=data.get('notes') or '',
        created_at=datetime.utcnow(),
    )
    db.session.add(charge)
    case.charges = '\n'.join([line for line in [case.charges, f"{charge.counts}x {charge.penal_code} {charge.charge_name}".strip()] if line])
    case.updated_at = datetime.utcnow()
    _cad_audit('charge_added', community_id, case.case_id, {'charge_id': charge.charge_id})
    db.session.commit()
    return jsonify({'success': True, 'charge': _charge_to_dict(charge)}), 201


@app.route('/api/cad/cases/<case_id>/charges/<charge_id>', methods=['PATCH', 'DELETE'])
def cad_case_charge_mutate(case_id, charge_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id)).first()
    charge = scoped_query(CaseCharge, community_id).filter_by(case_id=case.case_id if case else '', charge_id=charge_id).first()
    if not case or not charge:
        return _cad_json_error('Charge not found', 404)
    if request.method == 'DELETE':
        db.session.delete(charge)
        _cad_audit('charge_deleted', community_id, case.case_id, {'charge_id': charge_id})
        db.session.commit()
        return jsonify({'success': True})
    data = request.get_json(silent=True) or {}
    for field in ['charge_name', 'penal_code', 'recommended_fine', 'recommended_jail_time', 'notes']:
        if field in data:
            setattr(charge, field, str(data.get(field) or ''))
    if 'severity' in data and str(data['severity']).lower() in CHARGE_SEVERITIES:
        charge.severity = str(data['severity']).lower()
    if 'counts' in data:
        charge.counts = int(data.get('counts') or 1)
    charge.updated_at = datetime.utcnow()
    _cad_audit('charge_updated', community_id, case.case_id, {'charge_id': charge_id})
    db.session.commit()
    return jsonify({'success': True, 'charge': _charge_to_dict(charge)})


@app.route('/api/cad/cases/<case_id>/evidence', methods=['GET', 'POST'])
def cad_case_evidence(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id)).first()
    if not case:
        return _cad_json_error('Case not found', 404)
    if request.method == 'GET':
        evidence = scoped_query(Evidence, community_id).filter_by(case_number=_case_public_id(case)).order_by(Evidence.created_at.desc()).all()
        return jsonify({'success': True, 'evidence': [_evidence_workflow_to_dict(e) for e in evidence]})
    data = request.get_json(silent=True) or {}
    evidence = Evidence(
        community_id=community_id,
        evidence_id=data.get('evidence_id') or f"EV-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}",
        case_number=_case_public_id(case),
        evidence_type=data.get('evidence_type') or 'Other',
        evidence_description=data.get('description') or data.get('evidence_description') or '',
        collected_by=data.get('officer') or _actor_name(),
        officer=data.get('officer') or _actor_name(),
        clip_link=data.get('clip_link') or '',
        screenshot_link=data.get('screenshot_link') or '',
        storage_status=data.get('storage_status') or 'Logged',
        chain_of_custody=data.get('chain_of_custody') or f"[{datetime.utcnow().isoformat()}] Logged by {_actor_name()}",
        status=data.get('storage_status') or 'Logged',
        notes=data.get('notes') or '',
        created_at=datetime.utcnow(),
    )
    db.session.add(evidence)
    ids = set(_split_csv(case.linked_evidence_ids or case.evidence_ids))
    ids.add(evidence.evidence_id)
    case.linked_evidence_ids = ','.join(sorted(ids))
    case.evidence_ids = case.linked_evidence_ids
    case.updated_at = datetime.utcnow()
    _cad_audit('evidence_added', community_id, case.case_id, {'evidence_id': evidence.evidence_id})
    db.session.commit()
    return jsonify({'success': True, 'evidence': _evidence_workflow_to_dict(evidence)}), 201


@app.route('/api/cad/evidence/<evidence_id>', methods=['PATCH'])
def cad_evidence_patch(evidence_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    evidence = scoped_query(Evidence, community_id).filter_by(evidence_id=evidence_id).first()
    if not evidence:
        return _cad_json_error('Evidence not found', 404)
    data = request.get_json(silent=True) or {}
    mapping = {'description': 'evidence_description'}
    for field in ['evidence_type', 'officer', 'clip_link', 'screenshot_link', 'storage_status', 'chain_of_custody', 'notes']:
        if field in data:
            setattr(evidence, field, data.get(field) or '')
    if 'description' in data:
        evidence.evidence_description = data.get('description') or ''
    evidence.collected_by = evidence.officer or evidence.collected_by
    evidence.status = evidence.storage_status or evidence.status
    evidence.updated_at = datetime.utcnow()
    _cad_audit('evidence_updated', community_id, None, {'evidence_id': evidence_id})
    db.session.commit()
    return jsonify({'success': True, 'evidence': _evidence_workflow_to_dict(evidence)})


def _case_or_404_for_link(case_id, community_id):
    return scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id)).first()


@app.route('/api/cad/cases/<case_id>/create-warrant', methods=['POST'])
def cad_case_create_warrant(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = _case_or_404_for_link(case_id, community_id)
    if not case:
        return _cad_json_error('Case not found', 404)
    data = request.get_json(silent=True) or {}
    warrant_type = data.get('warrant_type') or 'Arrest Warrant'
    generated_warrant_number = data.get('warrant_number') or generate_warrant_number(community_id, warrant_type)
    while scoped_query(Warrant, community_id).filter_by(warrant_number=generated_warrant_number).first():
        generated_warrant_number = generate_warrant_number(community_id, warrant_type)
    new_status = data.get('status') or data.get('warrant_status') or 'Active'
    warrant = Warrant(
        community_id=community_id,
        warrant_id=generate_global_warrant_id(),
        warrant_number=generated_warrant_number,
        warrant_type=warrant_type,
        civilian_id=data.get('civilian_id') or (case.defendant_civilian_id or (_split_csv(case.involved_civilians)[0] if _split_csv(case.involved_civilians) else '')),
        warrant_name=data.get('warrant_name') or case.title or _case_public_id(case),
        warrant_charges=data.get('warrant_charges') or case.charges or '',
        warrant_issuer=data.get('warrant_issuer') or _actor_name(),
        warrant_notes=data.get('warrant_notes') or case.report_notes or '',
        warrant_status=new_status,
        status=new_status,
        subject_name=data.get('subject_name') or data.get('warrant_name') or case.title or _case_public_id(case),
        charges_or_basis=data.get('charges_or_basis') or data.get('warrant_charges') or case.charges or '',
        probable_cause=data.get('probable_cause') or data.get('justification') or f"Probable cause linked to case {_case_public_id(case)}.",
        issuing_agency=data.get('issuing_agency') or data.get('warrant_issuer') or _actor_name(),
        created_by_user_id=session.get('user_id'),
        justification=data.get('justification') or data.get('probable_cause') or f"Probable cause linked to case {_case_public_id(case)}.",
        created_at=datetime.utcnow(),
    )
    db.session.add(warrant)
    case.linked_warrant_id = warrant.warrant_id
    case.updated_at = datetime.utcnow()
    _cad_audit('warrant_created', community_id, case.case_id, {'warrant_id': warrant.warrant_id})
    db.session.commit()
    return jsonify({'success': True, 'warrant': warrant_to_dict(warrant), 'case': _case_to_dict(case)})


@app.route('/api/cad/cases/<case_id>/create-arrest', methods=['POST'])
def cad_case_create_arrest(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = _case_or_404_for_link(case_id, community_id)
    if not case:
        return _cad_json_error('Case not found', 404)
    data = request.get_json(silent=True) or {}
    arrest = Arrest(
        community_id=community_id,
        arrest_id=data.get('arrest_id') or f"ARR-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}",
        civilian_id=data.get('civilian_id') or case.defendant_civilian_id or '',
        suspect_name=data.get('suspect_name') or data.get('suspectName') or '',
        charges=data.get('charges') or case.charges or '',
        arresting_officer=data.get('arresting_officer') or _actor_name(),
        arrest_location=data.get('arrest_location') or case.location or '',
        evidence_attached=data.get('evidence_attached') or case.linked_evidence_ids or '',
        report_notes=data.get('report_notes') or case.report_notes or '',
        narrative=data.get('narrative') or case.report_notes or '',
        status=data.get('status') or 'Active',
        created_at=datetime.utcnow(),
    )
    db.session.add(arrest)
    case.linked_arrest_id = arrest.arrest_id
    case.arrest_id = arrest.arrest_id
    case.updated_at = datetime.utcnow()
    _cad_audit('arrest_created', community_id, case.case_id, {'arrest_id': arrest.arrest_id})
    db.session.commit()
    return jsonify({'success': True, 'arrest': arrest_to_dict(arrest), 'case': _case_to_dict(case)})


@app.route('/api/cad/cases/<case_id>/link-warrant', methods=['POST'])
def cad_case_link_warrant(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = _case_or_404_for_link(case_id, community_id)
    data = request.get_json(silent=True) or {}
    warrant = _find_warrant_for_cad(community_id, data.get('warrant_id')) if data.get('warrant_id') else None
    if not case or not warrant:
        return _cad_json_error('Case or warrant not found', 404)
    if data.get('warrant_status'):
        _set_warrant_status(warrant, data.get('warrant_status'))
    case.linked_warrant_id = warrant.warrant_id
    case.updated_at = warrant.updated_at = datetime.utcnow()
    _cad_audit('warrant_linked', community_id, case.case_id, {'warrant_id': warrant.warrant_id})
    db.session.commit()
    return jsonify({'success': True, 'case': _case_to_dict(case), 'warrant': warrant_to_dict(warrant)})


@app.route('/api/cad/cases/<case_id>/link-arrest', methods=['POST'])
def cad_case_link_arrest(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = _case_or_404_for_link(case_id, community_id)
    data = request.get_json(silent=True) or {}
    arrest = scoped_query(Arrest, community_id).filter_by(arrest_id=data.get('arrest_id')).first()
    if not case or not arrest:
        return _cad_json_error('Case or arrest not found', 404)
    if data.get('status'):
        arrest.status = data.get('status')
    case.linked_arrest_id = arrest.arrest_id
    case.arrest_id = arrest.arrest_id
    case.updated_at = arrest.updated_at = datetime.utcnow()
    _cad_audit('arrest_linked', community_id, case.case_id, {'arrest_id': arrest.arrest_id})
    db.session.commit()
    return jsonify({'success': True, 'case': _case_to_dict(case), 'arrest': arrest_to_dict(arrest)})


@app.route('/api/cad/cases/<case_id>/court-packet', methods=['GET'])
def cad_case_court_packet(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = _case_or_404_for_link(case_id, community_id)
    if not case:
        return _cad_json_error('Case not found', 404)
    charges = scoped_query(CaseCharge, community_id).filter_by(case_id=case.case_id).order_by(CaseCharge.created_at.asc()).all()
    evidence = scoped_query(Evidence, community_id).filter(Evidence.evidence_id.in_(_split_csv(case.linked_evidence_ids or case.evidence_ids))).all() if _split_csv(case.linked_evidence_ids or case.evidence_ids) else scoped_query(Evidence, community_id).filter_by(case_number=_case_public_id(case)).all()
    arrest = scoped_query(Arrest, community_id).filter_by(arrest_id=case.linked_arrest_id or case.arrest_id).first() if (case.linked_arrest_id or case.arrest_id) else None
    warrant = _find_warrant_for_cad(community_id, case.linked_warrant_id) if case.linked_warrant_id else None
    civilians = scoped_query(Civilian, community_id).filter(Civilian.civilian_id.in_(_split_csv(case.involved_civilians or case.defendant_civilian_id))).all() if _split_csv(case.involved_civilians or case.defendant_civilian_id) else []
    packet = {
        'case_summary': _case_to_dict(case),
        'suspect_civilian_details': [_civilian_response(c) for c in civilians],
        'charges': [_charge_to_dict(c) for c in charges],
        'officer_narrative': case.report_notes or (arrest.narrative if arrest else '') or '',
        'evidence_links': [_evidence_workflow_to_dict(e) for e in evidence],
        'evidence_attachments': [_attachment_to_dict(a) for a in scoped_query(EvidenceAttachment, community_id).filter_by(case_id=case.case_id, is_deleted=False).order_by(EvidenceAttachment.created_at.desc()).all()],
        'witness_notes': case.defense_notes or '',
        'arrest_info': arrest_to_dict(arrest) if arrest else None,
        'warrant_info': warrant_to_dict(warrant) if warrant else None,
        'use_of_force_info': None,
        'timestamps': {'created_at': case.created_at.isoformat() if case.created_at else None, 'updated_at': case.updated_at.isoformat() if case.updated_at else None},
        'chain_of_custody': [e.chain_of_custody or e.notes or '' for e in evidence],
        'recommended_sentence_fine': {'fine': sum(float(str(c.recommended_fine or '0').replace('$','') or 0) for c in charges if str(c.recommended_fine or '0').replace('$','').replace('.','',1).isdigit()), 'jail_time': ', '.join([c.recommended_jail_time for c in charges if c.recommended_jail_time])},
        'officer_signature_name': case.created_by or _actor_name(),
    }
    _cad_audit('court_packet_viewed', community_id, case.case_id)
    db.session.commit()
    return jsonify({'success': True, 'court_packet': packet})

# ---------------------------------------------------------------------------
# Court Routes
# ---------------------------------------------------------------------------

@app.route('/api/court/case/create', methods=['POST'])
@admin_required
def create_case_route():
    """Create case from arrest."""
    data = request.get_json(silent=True) or {}

    from court_service import create_case_from_arrest
    from cad_helpers import log_audit

    try:
        case = create_case_from_arrest(
            data.get('arrest_id'),
            data.get('civilian_id'),
            data.get('charges')
        )

        log_audit('court', 'create_case', 'CaseFile', case.case_id)
        return jsonify({'success': True, 'case_id': case.case_id})
    except Exception as e:
        logger.error(f'Failed to create case: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/court/case/<case_id>', methods=['GET'])
def get_case_route(case_id):
    """Get case summary."""
    from court_service import get_case_summary

    summary = get_case_summary(case_id)
    if not summary:
        return jsonify({'success': False, 'error': 'Case not found'}), 404

    return jsonify({'success': True, 'case': summary})


@app.route('/api/court/case/<case_id>/assign-judge', methods=['POST'])
@admin_required
def assign_judge_route(case_id):
    """Assign judge to case."""
    data = request.get_json(silent=True) or {}

    from court_service import assign_judge
    from cad_helpers import log_audit

    try:
        case = assign_judge(case_id, data.get('judge_name', 'Judge TBD'))
        if not case:
            return jsonify({'success': False, 'error': 'Case not found'}), 404

        log_audit('court', 'assign_judge', 'CaseFile', case_id)
        return jsonify({'success': True, 'message': 'Judge assigned'})
    except Exception as e:
        logger.error(f'Failed to assign judge: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/court/case/<case_id>/prosecutor-notes', methods=['POST'])
@admin_required
def prosecutor_notes_route(case_id):
    """Add prosecutor notes."""
    data = request.get_json(silent=True) or {}

    from court_service import add_prosecutor_notes
    from cad_helpers import log_audit

    try:
        case = add_prosecutor_notes(case_id, data.get('notes', ''))
        if not case:
            return jsonify({'success': False, 'error': 'Case not found'}), 404

        log_audit('court', 'prosecutor_notes', 'CaseFile', case_id)
        return jsonify({'success': True, 'message': 'Notes added'})
    except Exception as e:
        logger.error(f'Failed to add notes: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/court/case/<case_id>/close', methods=['POST'])
@admin_required
def close_case_route(case_id):
    """Close case with verdict and sentencing."""
    data = request.get_json(silent=True) or {}

    from court_service import close_case
    from cad_helpers import log_audit

    try:
        case = close_case(
            case_id,
            data.get('outcome', 'Guilty'),
            data.get('sentence_type', 'Probation'),
            data.get('sentence_length', '1 year'),
            data.get('notes', '')
        )

        if not case:
            return jsonify({'success': False, 'error': 'Case not found'}), 404

        log_audit('court', 'close_case', 'CaseFile', case_id)
        return jsonify({'success': True, 'message': 'Case closed'})
    except Exception as e:
        logger.error(f'Failed to close case: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/court/cases/search', methods=['POST'])
def search_cases_route():
    """Search cases."""
    data = request.get_json(silent=True) or {}
    query = data.get('query', '').strip()

    if not query or len(query) < 2:
        return jsonify({'success': False, 'error': 'Query must be at least 2 characters'}), 400

    from court_service import search_cases

    cases = search_cases(query)
    return jsonify({'success': True, 'cases': cases, 'total': len(cases)})


# ---------------------------------------------------------------------------
# Immersion Features Routes
# ---------------------------------------------------------------------------

@app.route('/api/immersion/alerts', methods=['GET'])
def get_alerts_route():
    """Get active MDT alerts."""
    from immersion_service import get_active_alerts

    officer_id = request.args.get('officer_id')
    limit = request.args.get('limit', 20, type=int)

    alerts = get_active_alerts(officer_id, limit)
    return jsonify({'success': True, 'alerts': alerts, 'total': len(alerts)})


@app.route('/api/immersion/warrant-hit/<plate>', methods=['GET'])
def warrant_hit_alert_route(plate):
    """Check for warrant hit on plate."""
    from immersion_service import generate_warrant_hit_alert

    alert = generate_warrant_hit_alert(plate)
    if not alert:
        return jsonify({'success': True, 'alert': None})

    return jsonify({'success': True, 'alert': alert})


@app.route('/api/immersion/stolen-vehicle/<plate>', methods=['GET'])
def stolen_vehicle_alert_route(plate):
    """Check for stolen vehicle alert."""
    from immersion_service import generate_stolen_vehicle_alert

    alert = generate_stolen_vehicle_alert(plate)
    if not alert:
        return jsonify({'success': True, 'alert': None})

    return jsonify({'success': True, 'alert': alert})


@app.route('/api/immersion/bolo-match/<civilian_id>', methods=['GET'])
def bolo_match_alert_route(civilian_id):
    """Check for BOLO match."""
    from immersion_service import generate_bolo_match_alert

    alert = generate_bolo_match_alert(civilian_id)
    if not alert:
        return jsonify({'success': True, 'alert': None})

    return jsonify({'success': True, 'alert': alert})


@app.route('/api/immersion/safety-warning/<civilian_id>', methods=['GET'])
def safety_warning_route(civilian_id):
    """Get officer safety warning."""
    data = request.get_json(silent=True) or {}
    officer_id = data.get('officer_id', 'dispatch')

    from immersion_service import generate_safety_warning_alert

    alert = generate_safety_warning_alert(civilian_id, officer_id)
    if not alert:
        return jsonify({'success': True, 'alert': None})

    return jsonify({'success': True, 'alert': alert})


@app.route('/api/immersion/dispatch-audio/<call_id>', methods=['GET'])
def dispatch_audio_route(call_id):
    """Get dispatch audio log for call."""
    from immersion_service import generate_dispatch_audio_log

    log = generate_dispatch_audio_log(call_id)
    if not log:
        return jsonify({'success': False, 'error': 'Call not found'}), 404

    return jsonify({'success': True, 'audio_log': log})


@app.route('/api/immersion/audio-logs', methods=['GET'])
def audio_logs_route():
    """Get recent dispatch audio logs."""
    from immersion_service import get_dispatch_audio_logs

    limit = request.args.get('limit', 20, type=int)
    logs = get_dispatch_audio_logs(limit)

    return jsonify({'success': True, 'logs': logs, 'total': len(logs)})


@app.route('/api/immersion/incident-timeline/<call_id>', methods=['GET'])
def incident_timeline_route(call_id):
    """Get incident timeline."""
    from immersion_service import get_incident_timeline

    timeline = get_incident_timeline(call_id)
    if not timeline:
        return jsonify({'success': False, 'error': 'Call not found'}), 404

    return jsonify({'success': True, 'timeline': timeline})


@app.route('/api/immersion/mdt-dashboard', methods=['GET'])
def mdt_dashboard_route():
    """Get complete MDT dashboard data."""
    from immersion_service import get_active_alerts, get_dispatch_audio_logs

    officer_id = request.args.get('officer_id')

    alerts = get_active_alerts(officer_id, 10)
    audio_logs = get_dispatch_audio_logs(5)

    # Get active calls
    active_calls = DispatchCall.query.filter(
        DispatchCall.status.in_(['New', 'Assigned', 'En Route', 'On Scene'])
    ).order_by(DispatchCall.created_at.desc()).limit(10).all()

    calls_data = [{
        'call_id': c.call_id,
        'location': c.location,
        'priority': c.priority,
        'status': c.status,
        'description': (c.description or '')[:100],
    } for c in active_calls]

    return jsonify({
        'success': True,
        'dashboard': {
            'alerts': alerts,
            'audio_logs': audio_logs,
            'active_calls': calls_data,
            'timestamp': datetime.utcnow().isoformat(),
        }
    })



def frontend_page(filename):
    """Serve a browser page from the app root."""
    return send_from_directory('.', filename)


@app.route('/')
def home():
    return frontend_page('index.html')


@app.route('/login')
def login_page():
    return frontend_page('login.html')


@app.route('/register')
def register_page():
    return frontend_page('register.html')


@app.route('/communities')
def communities_page():
    return frontend_page('communities.html')


@app.route('/create-community')
def create_community_page():
    return frontend_page('create-community.html')


@app.route('/join')
def invite_join_page():
    return frontend_page('join-community.html')

@app.route('/invite/<invite_code>')
def invite_code_page(invite_code):
    return frontend_page('join-community.html')

@app.route('/join-community')
def join_community_page():
    return frontend_page('join-community.html')

@app.route('/community-setup')
def community_setup_page():
    return frontend_page('join-community.html')


@app.route('/c/<community_slug>/')
def community_home(community_slug):
    return frontend_page('community.html')

@app.route('/c/<community_slug>/cad')
def community_cad_page(community_slug):
    if not session.get('user_id'):
        return redirect('/login', code=302)
    if not current_role_allows_police_cad():
        return frontend_page('community-cad-forbidden.html'), 403
    return frontend_page('police.html')


@app.route('/c/<community_slug>/<page>')
def community_page(community_slug, page):
    allowed_pages = {
        'index.html',
        'rules.html',
        'police.html',
        'dmv.html',
        'donations.html',
        'businesses.html',
        'applications.html',
        'complaints.html',
        'civilian.html',
        'cad.html',
        'join.html',
    }
    extensionless_aliases = {
        'index': 'index.html',
        'rules': 'rules.html',
        'police': 'police.html',
        'dmv': 'dmv.html',
        'donations': 'donations.html',
        'businesses': 'businesses.html',
        'applications': 'applications.html',
        'complaints': 'complaints.html',
        'civilian': 'civilian.html',
        'cad': 'police.html',
        'join': 'join.html',
    }
    page = extensionless_aliases.get(page, page)
    if page in {'police.html', 'cad.html', 'civilian.html', 'dmv.html', 'applications.html', 'complaints.html', 'businesses.html', 'donations.html'}:
        if not session.get('user_id'):
            return redirect('/login', code=302)
        user_id = session.get('user_id')
        community_id = get_current_community_id()
        auth_context = get_active_community_auth_context(user_id, community_id)
        allowed_modules = _module_policy_for_auth_context(getattr(g, 'current_user', None), None, auth_context.get('membership'), auth_context)
        page_to_module = {
            'police.html': 'police',
            'cad.html': 'cad',
            'civilian.html': 'civilian_portal',
            'dmv.html': 'dmv',
            'applications.html': 'applications',
            'complaints.html': 'complaints',
            'businesses.html': 'businesses',
            'donations.html': 'donations',
        }
        module = page_to_module.get(page)
        if module in {'police', 'cad'} and not current_role_allows_police_cad():
            return frontend_page('community-cad-forbidden.html'), 403
        if module and not _can_access_module(module, allowed_modules):
            return frontend_page('community-cad-forbidden.html'), 403
    if page in allowed_pages:
        return frontend_page(page)
    abort(404)




@app.route('/admin')
def platform_admin_page():
    if not session.get('user_id'):
        return redirect('/login', code=302)
    if not is_platform_owner():
        return frontend_page('admin-forbidden.html'), 403
    return frontend_page('admin.html')


@app.route('/community-admin')
@require_auth
def community_admin_page():
    # The page shell must render even when a PlatformOwner still needs to pick a
    # community; the API will return the specific auth/context state.
    return frontend_page('community-admin.html')


@app.route('/<path:path>')
def serve_static(path):
    route_aliases = {
        '': 'index.html',
        'login': 'login.html',
        'register': 'register.html',
        'communities': 'communities.html',
        'create-community': 'create-community.html',
        'join-community': 'join-community.html',
    }
    if path in route_aliases:
        return frontend_page(route_aliases[path])

    legacy_tenant_pages = {
        'rules.html': 'rules.html',
        'civilian.html': 'civilian.html',
        'police.html': 'police.html',
        'cad.html': 'cad.html',
        'dmv.html': 'dmv.html',
        'businesses.html': 'businesses.html',
        'applications.html': 'applications.html',
        'donations.html': 'donations.html',
        'complaints.html': 'complaints.html',
        'join.html': 'join.html',
    }
    if path in legacy_tenant_pages:
        # Legacy root CAD pages no longer select a default tenant. Route users to
        # the community picker so stale links cannot force the wrong community.
        return redirect('/communities', code=302)

    parts = path.strip('/').split('/') if path else []
    if len(parts) >= 3 and parts[0] == 'c' and parts[2] in {'assets', 'static'}:
        asset_path = '/'.join(parts[2:])
        if os.path.exists(os.path.join('.', asset_path)):
            return frontend_page(asset_path)

    if path.startswith('api/'):
        return jsonify({
            'success': False,
            'error': 'Endpoint not found',
            'code': 'NOT_FOUND'
        }), 404

    if os.path.exists(os.path.join('.', path)):
        return frontend_page(path)
    return frontend_page('index.html')



def configured_platform_owner_matches_user(user):
    owner_email = (os.getenv('PLATFORM_OWNER_EMAIL') or '').strip().lower()
    owner_username = (os.getenv('PLATFORM_OWNER_USERNAME') or '').strip().lower()
    if not user:
        return False
    email = (getattr(user, 'email', None) or '').strip().lower()
    username = (getattr(user, 'username', None) or '').strip().lower()
    return (owner_email and email == owner_email) or (owner_username and username == owner_username)


def ensure_platform_owner(user):
    if not user:
        return False
    return _session_hydrate_user(user)


def normalize_community_role(role):
    return canonical_normalize_community_role(role)


def has_community_owner_access(user_id, community=None, membership=None):
    """Normalize owner/admin access checks with owner_user_id fallback."""
    if not user_id:
        return False
    normalized_role = normalize_community_role(getattr(membership, 'role', None)) if membership else None
    if normalized_role in ('Owner', 'Admin', 'CommunityOwner', 'CommunityAdmin'):
        return True
    if community and getattr(community, 'owner_user_id', None) == user_id:
        return True
    return False




def _module_policy_for_auth_context(user, community, membership, auth_context):
    role = (auth_context.get('community_role') or '').strip()
    department = (auth_context.get('department') or '').strip()
    is_owner = bool(auth_context.get('is_platform_owner'))
    allowed = {'home', 'rules', 'communities', 'notifications', 'logout'}
    if not membership and not is_owner:
        allowed.update({'login', 'register'})
    if role in ('Civilian', 'Resident', 'Member'):
        allowed.update({'civilian_portal', 'dmv_self', 'applications', 'complaints', 'report_911', 'my_reports'})
    if role in ('Police', 'LEO', 'Sheriff', 'StateTrooper'):
        allowed.update({'police', 'cad', 'police_records', 'unit_status', 'reports'})
    if role in ('Dispatch',):
        allowed.update({'dispatch', 'cad', 'unit_status', 'call_logs'})
    if role in ('DMV',):
        allowed.update({'dmv', 'dmv_lookup'})
    if role in ('Business', 'BusinessOwner'):
        allowed.update({'businesses'})
    if role in ('Admin', 'Owner', 'CommunityAdmin', 'CommunityOwner'):
        allowed.update({'community_admin', 'member_management', 'role_permissions', 'applications', 'complaints', 'businesses', 'donations'})
    if is_owner:
        allowed.update({'platform_admin', 'community_admin', 'cad', 'police', 'dispatch', 'dmv', 'businesses', 'applications', 'complaints', 'donations', 'civilian_portal'})
    if department.lower() == 'dispatch':
        allowed.update({'dispatch', 'cad', 'unit_status', 'call_logs'})
    if department.lower() == 'dmv':
        allowed.update({'dmv', 'dmv_lookup'})
    return sorted(allowed)


def _can_access_module(module, allowed_modules):
    return module in set(allowed_modules or [])


def _active_authz_context():
    user_id = session.get('user_id')
    community_id = get_current_community_id()
    auth_context = get_active_community_auth_context(user_id, community_id)
    membership = auth_context.get('membership')
    allowed_modules = _module_policy_for_auth_context(getattr(g, 'current_user', None), getattr(g, 'community', None), membership, auth_context)
    return user_id, community_id, auth_context, allowed_modules


def _require_modules(*required_modules):
    user_id, community_id, auth_context, allowed_modules = _active_authz_context()
    if not isinstance(user_id, int):
        return None, (jsonify({'success': False, 'error': 'Authentication required'}), 401)
    if not community_id:
        return None, (jsonify({'success': False, 'error': 'Active community is required'}), 400)
    if not auth_context.get('is_platform_owner') and not auth_context.get('membership'):
        return None, (jsonify({'success': False, 'error': 'Active community membership required'}), 403)
    if required_modules and not any(_can_access_module(m, allowed_modules) for m in required_modules):
        return None, (jsonify({'success': False, 'error': 'Insufficient module permission'}), 403)
    return {
        'user_id': user_id,
        'community_id': community_id,
        'auth_context': auth_context,
        'allowed_modules': allowed_modules,
    }, None


def _create_call_notifications(community_id, call_id, call_type, location):
    title = 'New 911 Call'
    message = f"{call_type or 'Emergency'} at {location or 'Unknown location'}"
    payload = {'call_id': call_id, 'call_type': call_type, 'location': location}
    db.session.add(Notification(
        community_id=community_id,
        target_scope='department',
        target_department='Dispatch',
        title=title,
        message=message,
        category='cad',
        priority='high',
        action_url='/cad',
        data_json=json.dumps(payload),
        created_at=datetime.utcnow(),
    ))
    db.session.add(Notification(
        community_id=community_id,
        target_scope='role',
        target_role='Police',
        title=title,
        message=message,
        category='cad',
        priority='high',
        action_url='/cad',
        data_json=json.dumps(payload),
        created_at=datetime.utcnow(),
    ))

def is_platform_owner():
    user_id = session.get('user_id')
    user = User.query.get(user_id) if isinstance(user_id, int) else None
    if user:
        return _session_hydrate_user(user)
    return False


def require_platform_owner():
    if is_platform_owner():
        return None
    return jsonify({'success': False, 'error': 'PlatformOwner required'}), 403


def log_platform_admin(action, target_user_id=None, tenant=None, details=None):
    db.session.add(PlatformAdminLog(
        actor_user_id=session.get('user_id') if isinstance(session.get('user_id'), int) else None,
        target_user_id=target_user_id,
        tenant=tenant,
        action=action,
        details=json.dumps(details or {}),
        ip_address=request.remote_addr,
    ))


def invalidate_user_sessions(user_id):
    UserSession.query.filter_by(user_id=user_id, active=True).update({'active': False, 'invalidated_at': datetime.utcnow()})


@app.route('/api/auth/forgot-password', methods=['POST'])
@limiter.limit("5 per minute")
def forgot_password():
    data = request.get_json(silent=True) or {}
    identifier = (data.get('identifier') or '').strip()
    user = User.query.filter((User.username == identifier) | (User.email == identifier)).first()
    if user:
        token = secrets.token_urlsafe(32)
        db.session.add(PasswordResetToken(user_id=user.id, token=token, tenant=session.get('selected_community_id'), expires_at=datetime.utcnow() + timedelta(hours=1)))
        db.session.commit()
    response = {'success': True, 'message': 'If an account exists, password reset instructions have been sent.'}
    dev_echo_enabled = (os.getenv('FLASK_ENV') or '').lower() != 'production' and (os.getenv('ALLOW_DEV_RESET_TOKEN_ECHO', '').lower() == 'true')
    if dev_echo_enabled and user:
        response['reset_token'] = token
    return jsonify(response)


@app.route('/api/auth/reset-password', methods=['POST'])
@limiter.limit("5 per minute")
def reset_password_with_token():
    data = request.get_json(silent=True) or {}
    token = data.get('token')
    new_password = data.get('new_password', '')
    prt = PasswordResetToken.query.filter_by(token=token, used=False).first()
    if not prt or prt.expires_at < datetime.utcnow():
        return jsonify({'success': False, 'error': 'Invalid token or password'}), 400
    if not validate_password_policy(new_password):
        return jsonify({'success': False, 'error': 'Password does not meet security requirements'}), 400
    user = User.query.get(prt.user_id)
    user.password_hash = hash_password(new_password)
    prt.used = True
    invalidate_user_sessions(user.id)
    log_platform_admin('token_password_reset', target_user_id=user.id, tenant=session.get('selected_community_id'))
    db.session.commit()
    return jsonify({'success': True, 'message': 'Password reset successful'})


@app.route('/api/platform-owner/recovery/reset-password', methods=['POST'])
@limiter.limit("5 per minute")
def platform_owner_recovery_reset_password():
    data = request.get_json(silent=True) or {}
    configured_token = os.getenv('PLATFORM_OWNER_RECOVERY_TOKEN', '')
    owner_email = (os.getenv('PLATFORM_OWNER_EMAIL') or '').strip().lower()
    email = (data.get('email') or '').strip().lower()
    provided_token = data.get('token', '')
    new_password = data.get('new_password', '')
    confirm_password = data.get('confirm_password', '')

    if not configured_token or provided_token != configured_token:
        return jsonify({'success': False, 'error': 'Invalid recovery token'}), 403
    if new_password != confirm_password or not validate_password_policy(new_password):
        return jsonify({'success': False, 'error': 'Password does not meet security requirements'}), 400

    user = User.query.filter(func.lower(User.email) == email).first()
    if not user:
        return jsonify({'success': False, 'error': 'PlatformOwner account not found'}), 404
    if user.role != 'PlatformOwner' and getattr(user, 'platform_role', None) != 'PlatformOwner' and (not owner_email or email != owner_email):
        return jsonify({'success': False, 'error': 'User is not eligible for PlatformOwner recovery'}), 403

    user.password_hash = hash_password(new_password)
    user.role = 'PlatformOwner'
    if hasattr(User, 'platform_role'):
        user.platform_role = 'PlatformOwner'
    user.active = True
    invalidate_user_sessions(user.id)
    log_platform_admin('platform_owner_recovery_password_reset', target_user_id=user.id, tenant='*', details={'email': email})
    db.session.commit()
    logger.info("PlatformOwner recovery reset completed for user_id=%s email=%s", user.id, email)
    return jsonify({'success': True, 'message': 'PlatformOwner password reset successfully'})


@app.route('/api/platform-admin/overview', methods=['GET'])
def platform_admin_overview():
    """Get platform admin overview with defensive hydration and fallback values."""
    if not is_platform_owner():
        return jsonify({'success': False, 'error': 'Forbidden'}), 403

    try:
        request_id = str(uuid.uuid4())
        user_id = session.get('user_id')
        logger.info('[platform_overview] request_id=%s user_id=%s is_platform_owner=%s started',
                    request_id, user_id, True)
        now = datetime.utcnow()
        warnings = []

        def _safe_isoformat(dt):
            if dt is None:
                return None
            try:
                if isinstance(dt, datetime):
                    return dt.isoformat()
                return str(dt)
            except Exception:
                return None

        def _section_failed(section, err, warning_message):
            logger.error('[platform_overview] request_id=%s section=%s failed=%s',
                         request_id, section, err, exc_info=True)
            warnings.append(warning_message)

        def _safe_count(label, query_fn):
            try:
                return query_fn()
            except Exception as e:
                _section_failed(f'metrics.{label}', e, f'{label} metric failed')
                return 0

        logger.info('[platform_overview] request_id=%s section=communities started', request_id)
        community_rows = []
        try:
            communities = Community.query.order_by(Community.name.asc()).all()
            for c in communities:
                try:
                    community_id = getattr(c, 'community_id', None)
                    active_sessions_count = 0
                    try:
                        active_sessions_count = UserSession.query.filter_by(
                            tenant=community_id, active=True
                        ).count()
                    except Exception as e:
                        logger.warning('[platform_overview] request_id=%s community_id=%s active-session-count failed=%s',
                                       request_id, community_id, e)

                    last_seen = None
                    try:
                        last_session = UserSession.query.filter_by(
                            tenant=community_id
                        ).order_by(UserSession.last_seen.desc()).first()
                        if last_session and last_session.last_seen:
                            last_seen = last_session.last_seen
                    except Exception as e:
                        logger.warning('[platform_overview] request_id=%s community_id=%s last-session failed=%s',
                                       request_id, community_id, e)

                    owner_username = 'Unknown'
                    try:
                        if getattr(c, 'owner_user_id', None):
                            owner_user = User.query.filter_by(id=c.owner_user_id).first()
                            if owner_user and owner_user.username:
                                owner_username = owner_user.username
                    except Exception as e:
                        logger.warning('[platform_overview] request_id=%s community_id=%s owner lookup failed=%s',
                                       request_id, community_id, e)

                    member_count = 0
                    try:
                        member_count = CommunityMember.query.filter_by(
                            community_id=community_id, status='Active'
                        ).count()
                    except Exception as e:
                        logger.warning('[platform_overview] request_id=%s community_id=%s member-count failed=%s',
                                       request_id, community_id, e)

                    if last_seen and (now - last_seen).total_seconds() < 300:
                        live_status = 'ONLINE'
                    elif last_seen and (now - last_seen).total_seconds() < 1800:
                        live_status = 'IDLE'
                    else:
                        live_status = 'OFFLINE'

                    community_rows.append({
                        'community_id': community_id,
                        'id': community_id,
                        'name': c.name or 'Unknown',
                        'slug': c.slug,
                        'cad_name': c.cad_name or c.name or 'Unknown',
                        'invite': getattr(c, 'invite', None),
                        'owner': owner_username,
                        'owner_username': owner_username,
                        'members': member_count,
                        'online': active_sessions_count,
                        'last_active': _safe_isoformat(last_seen),
                        'status': live_status or 'OFFLINE',
                        'active': True,
                    })
                except Exception as e:
                    logger.warning('[platform_overview] request_id=%s community_id=%s serialization failed=%s',
                                   request_id, getattr(c, 'community_id', 'unknown'), e, exc_info=True)
                    continue
            logger.info('[platform_overview] request_id=%s section=communities success count=%s',
                        request_id, len(community_rows))
        except Exception as e:
            _section_failed('communities', e, 'Community hydration failed')

        community_map = {row.get('community_id'): row for row in community_rows if row.get('community_id')}

        logger.info('[platform_overview] request_id=%s section=users started', request_id)
        user_rows = []
        try:
            users = User.query.order_by(User.id.desc()).limit(200).all()
            for u in users:
                try:
                    last_login = getattr(u, 'last_login', None)
                    last_login_iso = _safe_isoformat(last_login)
                    if last_login and (now - last_login).total_seconds() < 300:
                        online_status = 'ONLINE'
                    else:
                        online_status = 'OFFLINE'

                    session_count = 0
                    try:
                        session_count = UserSession.query.filter_by(
                            user_id=u.id, active=True
                        ).count()
                    except Exception as e:
                        logger.warning(f'Could not count sessions for user {u.id}: {e}')

                    member = CommunityMember.query.filter_by(user_id=u.id, status='Active').first()
                    community_id = getattr(member, 'community_id', None)
                    community_label = community_map.get(community_id) if community_id else None
                    user_rows.append({
                        'id': u.id,
                        'username': u.username or 'Unknown',
                        'email': u.email or 'Unknown',
                        'platform_role': getattr(u, 'platform_role', None) or getattr(u, 'role', None) or 'User',
                        'role': getattr(u, 'role', None) or 'User',
                        'community': community_id,
                        'community_id': community_id,
                        'community_name': community_label.get('name') if community_label else None,
                        'community_slug': community_label.get('slug') if community_label else None,
                        'community_role': normalize_community_role(getattr(member, 'role', None)) or 'Unknown',
                        'last_login': last_login_iso,
                        'sessions': session_count,
                        'status': online_status,
                    })
                except Exception as e:
                    logger.warning('[platform_overview] request_id=%s user_id=%s serialization failed=%s',
                                   request_id, getattr(u, 'id', 'unknown'), e, exc_info=True)
                    continue
            logger.info('[platform_overview] request_id=%s section=users success count=%s',
                        request_id, len(user_rows))
        except Exception as e:
            _section_failed('users', e, 'User hydration failed')

        logger.info('[platform_overview] request_id=%s section=activity started', request_id)
        recent_activity = []
        activity_models = [globals().get(n) for n in ['PlatformActivityLog', 'PlatformAdminLog', 'ActivityLog', 'AuditLog']]
        for model in [m for m in activity_models if m is not None]:
            try:
                rows = model.query.order_by(model.created_at.desc()).limit(50).all()
            except Exception as e:
                logger.warning('[platform_overview] request_id=%s activity source=%s failed=%s',
                               request_id, getattr(model, '__name__', 'unknown'), e)
                continue
            for a in rows:
                try:
                    recent_activity.append({
                        'id': getattr(a, 'log_id', None),
                        'action': getattr(a, 'action', '') or '',
                        'officer': getattr(a, 'officer', '') or '',
                        'details': getattr(a, 'details', '') or '',
                        'timestamp': _safe_isoformat(getattr(a, 'created_at', None)),
                        # legacy keys used by some frontend versions
                        'type': getattr(a, 'action', 'activity') or 'activity',
                        'message': getattr(a, 'details', '') or getattr(a, 'action', '') or '',
                        'created_at': _safe_isoformat(getattr(a, 'created_at', None)),
                    })
                except Exception as e:
                    logger.warning('[platform_overview] request_id=%s activity serialization failed=%s', request_id, e)
                    continue
            if recent_activity:
                break
        if not recent_activity:
            warnings.append('Activity hydration failed')

        metrics = {
            'total_communities': _safe_count('total_communities', lambda: Community.query.count()),
            'total_users': _safe_count('total_users', lambda: User.query.count()),
            'online_users': _safe_count('online_users', lambda: UserSession.query.filter_by(active=True).count()),
            'active_sessions': _safe_count('active_sessions', lambda: UserSession.query.filter_by(active=True).count()),
            'total_officers': _safe_count('total_officers', lambda: OfficerSession.query.count()),
            'total_warrants': _safe_count('total_warrants', lambda: Warrant.query.count()),
            'total_arrests': _safe_count('total_arrests', lambda: Arrest.query.count()),
            'total_businesses': _safe_count('total_businesses', lambda: Business.query.count()),
            'platform_uptime': '0s',
        }
        response = {
            'success': True,
            'metrics': metrics,
            'communities': community_rows,
            'users': user_rows,
            'activity': recent_activity,
            'chart': {'labels': [], 'datasets': []},
            'warnings': warnings,
            'overview': {**metrics, 'communities': community_rows, 'users': user_rows, 'activity': recent_activity},
        }
        logger.info('[platform_overview] request_id=%s final_success=true warnings=%s', request_id, len(warnings))
        return jsonify(response), 200

    except Exception as e:
        logger.error('[platform_overview] unhandled failure error=%s', e, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to load platform overview'}), 500


@app.route('/api/platform-admin/users/<int:user_id>/reset-password', methods=['POST'])
def platform_admin_reset_password(user_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    data = request.get_json(silent=True) or {}
    new_password = data.get('new_password', '')
    if not validate_password_policy(new_password):
        return jsonify({'success': False, 'error': 'Password does not meet security requirements'}), 400
    user = User.query.get_or_404(user_id)
    user.password_hash = hash_password(new_password)
    invalidate_user_sessions(user.id)
    log_platform_admin('platform_password_reset', target_user_id=user.id, tenant='*')
    db.session.commit()
    return jsonify({'success': True})


def _select_platform_admin_community(community, action='community_select'):
    session['selected_community_id'] = community.community_id
    session['selected_community_slug'] = community.slug
    session.modified = True
    log_platform_admin(action, tenant=community.community_id, details={'result': 'success'})
    db.session.commit()


@app.route('/api/platform-admin/communities/<community_id>/open', methods=['POST'])
def platform_admin_open_community(community_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    redirect_url = f"/c/{community.slug}/"
    _select_platform_admin_community(community, 'community_open')
    return jsonify({'success': True, 'redirect': redirect_url, 'redirect_url': redirect_url, 'community': community.to_dict()})


@app.route('/api/platform-admin/communities/<community_id>/select', methods=['POST'])
def platform_admin_select_community(community_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    _select_platform_admin_community(community, 'community_select')
    return jsonify({'success': True, 'redirect_url': '/community-admin', 'redirect': '/community-admin', 'community': community.to_dict()})


@app.route('/api/platform-admin/communities/<community_id>/suspend', methods=['POST'])
def platform_admin_suspend_community(community_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    community.status = 'SUSPENDED'
    log_platform_admin('community_suspend', tenant=community_id, details={'status': 'SUSPENDED', 'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'status': 'SUSPENDED'})


@app.route('/api/platform-admin/communities/<community_id>/disable', methods=['POST'])
def platform_admin_disable_community(community_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    community.status = 'INACTIVE'
    log_platform_admin('community_disable', tenant=community_id, details={'active': False, 'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'active': False})


@app.route('/api/platform-admin/communities/<community_id>/reset-invite', methods=['POST'])
def platform_admin_reset_invite(community_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    CommunityInvite.query.filter_by(community_id=community_id, active=True).update({'active': False})
    new_code = secrets.token_urlsafe(6).replace('_', '').replace('-', '').upper()[:8]
    invite = CommunityInvite(
        invite_code=new_code,
        community_id=community_id,
        role='Civilian',
        created_by=session.get('user_id') if isinstance(session.get('user_id'), int) else community.owner_user_id,
        active=True,
    )
    db.session.add(invite)
    db.session.flush()
    log_platform_admin(
        'community_reset_invite',
        tenant=community_id,
        details={'invite_id': invite.id, 'masked_invite_code': mask_invite_code(new_code), 'community_id': community_id, 'result': 'success'}
    )
    db.session.commit()
    return jsonify({'success': True, 'invite_code': new_code})


@app.route('/api/platform-admin/communities/<community_id>/logs', methods=['GET'])
def platform_admin_community_logs(community_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    limit = min(max(int(request.args.get('limit', 50)), 1), 200)
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    logs = PlatformAdminLog.query.filter_by(tenant=community_id).order_by(PlatformAdminLog.created_at.desc()).limit(limit).all()
    return jsonify({'success': True, 'community': {'community_id': community.community_id, 'name': community.name, 'slug': community.slug}, 'logs': [{
        'id': row.id,
        'action': row.action,
        'target_user_id': row.target_user_id,
        'details': row.details,
        'ip_address': row.ip_address,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    } for row in logs]})


@app.route('/api/platform-admin/communities/<community_id>/impersonate', methods=['POST'])
def platform_admin_impersonate_community(community_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return jsonify({'success': False, 'error': 'Community not found'}), 404
    session['impersonating_community_id'] = community.community_id
    session['impersonating_community_slug'] = community.slug
    session['selected_community_id'] = community.community_id
    session['selected_community_slug'] = community.slug
    session['impersonation_active'] = True
    session['original_user_id'] = session.get('user_id')
    session.modified = True
    log_platform_admin('community_impersonate', tenant=community_id, details={'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'redirect_url': '/community-admin', 'redirect': '/community-admin', 'impersonation_active': True})


@app.route('/api/platform-admin/impersonation/exit', methods=['POST'])
def platform_admin_exit_impersonation():
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    session.pop('impersonating_community_id', None)
    session.pop('impersonating_community_slug', None)
    session.pop('impersonation_active', None)
    session.pop('original_user_id', None)
    log_platform_admin('community_impersonation_exit', tenant='*', details={'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'redirect_url': '/admin', 'redirect': '/admin'})


def _platform_admin_self_block(user_id, action_name):
    data = request.get_json(silent=True) or {}
    if session.get('user_id') == user_id and not data.get('confirm_self'):
        return jsonify({'success': False, 'error': f'Cannot {action_name} your own account without confirmation'}), 400
    return None


@app.route('/api/platform-admin/users/<int:user_id>/ban', methods=['POST'])
def platform_admin_ban_user(user_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    self_block = _platform_admin_self_block(user_id, 'disable')
    if self_block:
        return self_block
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    user.active = False
    invalidate_user_sessions(user_id)
    log_platform_admin('user_ban', target_user_id=user_id, tenant='*', details={'status': 'BANNED', 'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'status': 'BANNED'})


@app.route('/api/platform-admin/users/<int:user_id>/unban', methods=['POST'])
def platform_admin_unban_user(user_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    user.active = True
    log_platform_admin('user_unban', target_user_id=user_id, tenant='*', details={'status': 'ACTIVE', 'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'status': 'ACTIVE'})


@app.route('/api/platform-admin/users/<int:user_id>/force-logout', methods=['POST'])
def platform_admin_force_logout_user(user_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    invalidate_user_sessions(user_id)
    log_platform_admin('user_force_logout', target_user_id=user_id, tenant='*', details={'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'message': 'User sessions invalidated'})


@app.route('/api/platform-admin/users/<int:user_id>/disable', methods=['POST'])
def platform_admin_disable_user(user_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    self_block = _platform_admin_self_block(user_id, 'disable')
    if self_block:
        return self_block
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    user.active = False
    invalidate_user_sessions(user_id)
    log_platform_admin('user_disable', target_user_id=user_id, tenant='*', details={'active': False, 'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'active': False})


@app.route('/api/platform-admin/users/<int:user_id>/promote', methods=['POST'])
def platform_admin_promote_user(user_id):
    auth_error = require_platform_owner()
    if auth_error:
        return auth_error
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404
    data = request.get_json(silent=True) or {}
    role = (data.get('role') or '').strip()
    community_id = (data.get('community_id') or '').strip() or None
    if role == 'PlatformOwner':
        user.role = 'PlatformOwner'
        if hasattr(user, 'platform_role'):
            user.platform_role = 'PlatformOwner'
    elif role in ('Admin', 'CommunityAdmin', 'CommunityOwner'):
        if not community_id:
            return jsonify({'success': False, 'error': 'community_id is required for community role promotion'}), 400
        community = Community.query.filter_by(community_id=community_id).first()
        if not community:
            return jsonify({'success': False, 'error': 'Community not found'}), 404
        membership = CommunityMember.query.filter_by(user_id=user_id, community_id=community_id).first()
        if not membership:
            membership = CommunityMember(user_id=user_id, community_id=community_id, role=role, status='Active')
            db.session.add(membership)
        else:
            membership.role = role
            membership.status = 'Active'
    else:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400
    log_platform_admin('user_promote', target_user_id=user_id, tenant=community_id or '*', details={'role': role, 'result': 'success'})
    db.session.commit()
    return jsonify({'success': True, 'user': user.to_dict()})




@app.errorhandler(Exception)
def community_admin_json_exception(error):
    if request.path.startswith('/api/community-admin'):
        logger.exception(
            'Community admin endpoint failed request_id=%s path=%s user_id=%s selected_community_id=%s '
            'impersonating_community_id=%s resolved_community_id=%s resolved_slug=%s failure_reason=%s',
            getattr(g, 'request_id', None),
            request.path,
            session.get('user_id'),
            session.get('selected_community_id'),
            session.get('impersonating_community_id'),
            getattr(getattr(g, 'community', None), 'community_id', None) or getattr(g, 'community_id', None),
            getattr(getattr(g, 'community', None), 'slug', None),
            getattr(error, 'description', type(error).__name__),
        )
        code = getattr(error, 'code', 500) or 500
        if code >= 500:
            return jsonify({'success': False, 'error': 'Unable to load community admin data'}), 500
        return jsonify({'success': False, 'error': getattr(error, 'description', 'Unable to load community admin data')}), code
    raise error

COMMUNITY_ADMIN_ROLES = {'CommunityOwner', 'CommunityAdmin', 'Owner', 'Admin'}
COMMUNITY_SUPPORTED_ROLES = {
    'CommunityOwner', 'CommunityAdmin', 'Owner', 'Admin', 'Police', 'Officer',
    'LEO', 'Dispatch', 'Dispatcher', 'EMS', 'DOJ', 'Staff',
    'Civilian', 'Member', 'BusinessOwner'
}
CAD_ELIGIBLE_ROLES = set(CANONICAL_CAD_ACCESS_ROLES)
COMMUNITY_SETTING_KEYS = {
    'departments', 'officer_ranks', 'ranks', 'call_types', 'penal_code_categories',
    'business_categories', 'invite_policy', 'cad_access_policy', 'accent_color', 'background_color', 'text_color'
}
COMMUNITY_SETTING_ALIASES = {
    'ranks': 'officer_ranks',
}

PUBLIC_INVITE_ERROR = 'Invite is invalid, expired, revoked, or no longer available'

DEFAULT_COMMUNITY_RANKS = [
    'Cadet', 'Officer', 'Senior Officer', 'Corporal', 'Sergeant',
    'Lieutenant', 'Captain', 'Assistant Chief', 'Chief'
]
COMMUNITY_LIST_SETTING_KEYS = {
    'departments', 'officer_ranks', 'ranks', 'call_types',
    'penal_code_categories', 'business_categories'
}


def role_is_cad_permission_eligible(role):
    """Return True only for policy-approved roles that may receive Police CAD permissions."""
    return role_allows_police_cad(normalize_community_role(role))


def _normalize_text_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or '').strip()]
    if isinstance(value, dict):
        values = list(value.values())
        if all(not isinstance(item, (dict, list)) for item in values):
            return [str(item).strip() for item in values if str(item or '').strip()]
        flattened = []
        for item in values:
            if isinstance(item, list):
                flattened.extend(str(entry).strip() for entry in item if str(entry or '').strip())
        return flattened
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parsed = _json_loads(text, None)
        if parsed is not None and parsed is not value:
            return _normalize_text_list(parsed)
        return [part.strip() for part in text.replace(',', '\n').splitlines() if part.strip()]
    return []


def _normalize_community_list_setting(key, value):
    normalized = _normalize_text_list(value)
    if COMMUNITY_SETTING_ALIASES.get(key, key) == 'officer_ranks' and not normalized:
        return list(DEFAULT_COMMUNITY_RANKS)
    return normalized


def _revoke_community_admin_cad_permission(community_id, user_id):
    perms = _community_admin_cad_permissions(community_id)
    removed = perms.pop(str(user_id), None) is not None
    if removed:
        _community_admin_save_cad_permissions(community_id, perms)
    return removed

INVITE_JOIN_ROLES = COMMUNITY_SUPPORTED_ROLES - {'PlatformOwner'}
INVITE_ADMIN_LEVEL_ROLES = {'CommunityAdmin', 'CommunityOwner', 'Owner', 'Admin'}


def _invite_code_input(value):
    return ''.join(ch for ch in (value or '').strip().upper() if ch.isalnum())


def mask_invite_code(value):
    code = (value or '').strip()
    if not code:
        return None
    return f"{code[:4]}****" if len(code) >= 4 else "****"


def _invite_uses_remaining(invite):
    if invite.max_uses is None:
        return None
    return max(0, int(invite.max_uses or 0) - int(invite.uses or 0))


def _public_invite_payload(invite, community):
    return {
        'code': invite.invite_code,
        'community_id': community.community_id,
        'community_name': community.name,
        'community_slug': community.slug,
        'cad_name': community.cad_name,
        'role': invite.role,
        'expires_at': invite.expires_at.isoformat() if invite.expires_at else None,
        'uses_remaining': _invite_uses_remaining(invite),
    }


def _community_is_joinable(community):
    if not community:
        return False
    return (community.status or '').strip().lower() == 'active'


def _validate_invite_for_join(invite_code, lock=False):
    code = _invite_code_input(invite_code)
    if not code:
        return None, None, 'missing_code'

    query = CommunityInvite.query.filter(func.upper(CommunityInvite.invite_code) == code)
    if lock:
        try:
            query = query.with_for_update()
        except Exception:
            pass
    invite = query.first()
    if not invite:
        return None, None, 'not_found'

    community = Community.query.filter_by(community_id=invite.community_id).first()
    role = normalize_community_role(invite.role or 'Civilian')
    if role == 'PlatformOwner' or role not in INVITE_JOIN_ROLES:
        return invite, community, 'invalid_role'
    if not getattr(invite, 'active', True):
        return invite, community, 'revoked'
    if invite.expires_at and datetime.utcnow() > invite.expires_at:
        return invite, community, 'expired'
    if invite.max_uses is not None and int(invite.uses or 0) >= int(invite.max_uses):
        return invite, community, 'maxed'
    if not _community_is_joinable(community):
        return invite, community, 'community_unavailable'
    return invite, community, None


def _audit_invite_event(action, community_id=None, invite=None, result='success', assigned_role=None, details=None):
    safe_details = dict(details or {})
    raw_code = safe_details.pop('invite_code', None)
    if raw_code:
        safe_details['masked_invite_code'] = mask_invite_code(raw_code)
    if assigned_role:
        safe_details['assigned_role'] = assigned_role
    if invite:
        safe_details['invite_id'] = getattr(invite, 'id', None)
        safe_details['masked_invite_code'] = mask_invite_code(getattr(invite, 'invite_code', None))
    safe_details['request_id'] = getattr(g, 'request_id', None)
    db.session.add(AuditLog(
        log_id=f'audit-{uuid.uuid4().hex}',
        community_id=community_id or getattr(invite, 'community_id', None),
        actor=str(session.get('user_id') or ''),
        actor_role=session.get('community_role') or session.get('current_role') or session.get('role') or session.get('platform_role'),
        action=f'invite.{action}.{result}',
        record_type='community_invite',
        record_id=str(getattr(invite, 'id', '') or ''),
        after_state=json.dumps(safe_details),
        ip_address=getattr(g, 'client_ip', request.remote_addr),
    ))


def _set_joined_community_session(user, community, membership):
    session['community_id'] = community.community_id
    session['community_slug'] = community.slug
    session['community_role'] = membership.role
    session['active_community_id'] = community.community_id
    session['selected_community_id'] = community.community_id
    session['selected_community_slug'] = community.slug
    session['current_role'] = membership.role
    session['current_department'] = membership.department
    if hasattr(user, 'community_id'):
        try:
            user.community_id = community.community_id
        except Exception:
            pass
    session.modified = True


@app.route('/api/invites/<invite_code>', methods=['GET'])
def public_invite_lookup(invite_code):
    invite, community, reason = _validate_invite_for_join(invite_code, lock=False)
    if reason:
        if invite or _invite_code_input(invite_code):
            _audit_invite_event('view_failed', getattr(community, 'community_id', None), invite, result='failed', details={'reason': reason, 'invite_code': _invite_code_input(invite_code)})
            db.session.commit()
        return jsonify({'success': False, 'error': PUBLIC_INVITE_ERROR}), 404

    _audit_invite_event('viewed', community.community_id, invite, details={'role': invite.role})
    db.session.commit()
    return jsonify({'success': True, 'invite': _public_invite_payload(invite, community)})


@app.route('/api/invites/<invite_code>/join', methods=['POST'])
@require_auth
def join_invite(invite_code):
    user_id = session.get('user_id')
    user = User.query.get(user_id) if user_id else None
    if not user or not getattr(user, 'active', True):
        return jsonify({'success': False, 'error': 'Authentication required'}), 401

    try:
        invite, community, reason = _validate_invite_for_join(invite_code, lock=True)
        if reason:
            _audit_invite_event('join_failed', getattr(community, 'community_id', None), invite, result='failed', details={'reason': reason, 'invite_code': _invite_code_input(invite_code)})
            db.session.commit()
            return jsonify({'success': False, 'error': PUBLIC_INVITE_ERROR}), 400

        role = normalize_community_role(invite.role or 'Civilian')
        existing = CommunityMember.query.filter_by(user_id=user.id, community_id=community.community_id).first()
        if existing:
            existing.status = 'Active'
            # Do not silently escalate/demote an existing membership. Preserve the current tenant role.
            existing.updated_at = datetime.utcnow()
            membership = existing
            message = 'Already a member of this community'
            increment_usage = False
        else:
            membership = CommunityMember(
                community_id=community.community_id,
                user_id=user.id,
                role=role,
                department=invite.department,
                status='Active',
            )
            db.session.add(membership)
            message = 'Joined community successfully'
            increment_usage = True

        if increment_usage:
            invite.uses = int(invite.uses or 0) + 1
            if invite.max_uses is not None and invite.uses >= invite.max_uses:
                invite.active = False

        _set_joined_community_session(user, community, membership)
        _audit_invite_event('joined', community.community_id, invite, assigned_role=membership.role, details={'existing_member': bool(existing), 'uses': invite.uses})
        db.session.commit()

        redirect_url = f'/c/{community.slug}/'
        return jsonify({
            'success': True,
            'message': message,
            'community': {
                'community_id': community.community_id,
                'name': community.name,
                'slug': community.slug,
            },
            'role': membership.role,
            'redirect': redirect_url,
        })
    except Exception as exc:
        db.session.rollback()
        logger.exception('Invite join failed request_id=%s', getattr(g, 'request_id', None))
        return jsonify({'success': False, 'error': 'Unable to join community right now'}), 500


def _community_admin_generate_invite_code(length=10):
    alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    for _ in range(100):
        code = ''.join(secrets.choice(alphabet) for _ in range(length))
        if not CommunityInvite.query.filter_by(invite_code=code).first():
            return code
    raise RuntimeError('Unable to generate a unique invite code')

def _json_loads(value, default=None):
    if value in (None, ''):
        return default
    if isinstance(value, (dict, list, bool, int, float)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _community_admin_config_value(community_id, key, default=None):
    key = COMMUNITY_SETTING_ALIASES.get(key, key)
    config = Config.query.filter_by(community_id=community_id, key=key).first()
    value = _json_loads(config.value, default) if config else default
    if key in COMMUNITY_LIST_SETTING_KEYS:
        return _normalize_community_list_setting(key, value)
    return value


def _community_admin_set_config_value(community_id, key, value, description=None):
    key = COMMUNITY_SETTING_ALIASES.get(key, key)
    if key in COMMUNITY_LIST_SETTING_KEYS:
        value = _normalize_community_list_setting(key, value)
    config = Config.query.filter_by(community_id=community_id, key=key).first()
    if not config:
        config = Config(community_id=community_id, key=key, description=description or f'Community setting: {key}')
        db.session.add(config)
    config.value = json.dumps(value)
    config.updated_at = datetime.utcnow()
    return config


def _community_admin_cad_permissions(community_id):
    data = _community_admin_config_value(community_id, 'cad_permissions', {})
    return data if isinstance(data, dict) else {}


def _community_admin_save_cad_permissions(community_id, data):
    _community_admin_set_config_value(community_id, 'cad_permissions', data, 'Per-member Police CAD permissions')


def _community_admin_apply_cad_permission_attrs(membership):
    if not membership:
        return membership
    data = _community_admin_cad_permissions(membership.community_id).get(str(membership.user_id), {})
    if isinstance(data, dict):
        if not role_is_cad_permission_eligible(getattr(membership, 'role', None)):
            for key in ('can_access_police_cad', 'can_dispatch', 'can_manage_warrants', 'can_manage_evidence', 'can_manage_arrests'):
                setattr(membership, key, False)
            return membership
        for key in ('can_access_police_cad', 'can_dispatch', 'can_manage_warrants', 'can_manage_evidence', 'can_manage_arrests', 'rank'):
            if key in data:
                if key == 'rank':
                    setattr(membership, key, data.get(key))
                else:
                    setattr(membership, key, parse_bool(data.get(key)))
    return membership


def _community_admin_body():
    return request.get_json(silent=True) or {}


def _community_admin_requested_community_id():
    data = _community_admin_body() if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else {}
    community_id = (request.args.get('community_id') or data.get('community_id') or '').strip()
    slug = (request.args.get('slug') or request.args.get('community_slug') or data.get('slug') or data.get('community_slug') or '').strip()
    if not slug:
        slug = getattr(g, 'community', None).slug if getattr(g, 'community', None) else None
    if slug:
        community = Community.query.filter_by(slug=slug).first()
        if community:
            return community.community_id
    return community_id or None


def _community_admin_available_communities():
    return [c.to_dict() for c in Community.query.order_by(Community.name.asc()).all()]


def _community_admin_context_error(message, status, reason, current_user=None, community=None, membership=None, extra=None):
    logger.warning(
        'community_admin_context route=%s user_id=%s is_platform_owner=%s '
        'session_selected_community_id=%s session_selected_community_slug=%s '
        'session_impersonating_community_id=%s session_impersonating_community_slug=%s '
        'resolved_community_id=%s resolved_slug=%s failure_reason=%s',
        request.path,
        session.get('user_id'),
        is_platform_owner(),
        session.get('selected_community_id'),
        session.get('selected_community_slug'),
        session.get('impersonating_community_id'),
        session.get('impersonating_community_slug'),
        getattr(community, 'community_id', None),
        getattr(community, 'slug', None),
        reason,
    )
    payload = {'success': False, 'error': message}
    if extra:
        payload.update(extra)
    return current_user, community, membership, jsonify(payload), status


def _community_admin_find_community(value=None, slug=None):
    if slug:
        community = Community.query.filter_by(slug=slug).first()
        if community:
            return community
    if value:
        return Community.query.filter_by(community_id=value).first()
    return None


def _community_admin_resolve_context(require_selected=True):
    user_id = session.get('user_id')
    if not user_id:
        return _community_admin_context_error('Authentication required', 401, 'unauthenticated')
    current_user = getattr(g, 'current_user', None) or User.query.get(user_id)
    if not current_user:
        return _community_admin_context_error('Authentication required', 401, 'user_not_found')

    owner = is_platform_owner()
    data = _community_admin_body() if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') else {}
    query_id = (request.args.get('community_id') or data.get('community_id') or '').strip()
    query_slug = (request.args.get('slug') or request.args.get('community_slug') or data.get('slug') or data.get('community_slug') or '').strip()
    route_slug = resolve_community_slug_from_path()
    route_community = _community_admin_find_community(slug=route_slug) if route_slug else None

    candidates = []
    if owner:
        candidates.extend([
            ('query', _community_admin_find_community(query_id, query_slug)),
            ('impersonating_id', _community_admin_find_community(session.get('impersonating_community_id'))),
            ('impersonating_slug', _community_admin_find_community(slug=session.get('impersonating_community_slug'))),
            ('selected_id', _community_admin_find_community(session.get('selected_community_id'))),
            ('selected_slug', _community_admin_find_community(slug=session.get('selected_community_slug'))),
            ('session_id', _community_admin_find_community(session.get('community_id'))),
            ('session_slug', _community_admin_find_community(slug=session.get('community_slug'))),
            ('route', route_community),
        ])
    else:
        candidates.extend([
            ('route', route_community),
            ('query', _community_admin_find_community(query_id, query_slug)),
            ('selected_id', _community_admin_find_community(session.get('selected_community_id'))),
            ('selected_slug', _community_admin_find_community(slug=session.get('selected_community_slug'))),
            ('session_id', _community_admin_find_community(session.get('community_id'))),
            ('session_slug', _community_admin_find_community(slug=session.get('community_slug'))),
        ])

    community = next((candidate for _source, candidate in candidates if candidate), None)
    membership = None
    if community:
        membership = CommunityMember.query.filter_by(user_id=user_id, community_id=community.community_id, status='Active').first()

    if not owner and community and not membership and getattr(community, 'owner_user_id', None) != user_id:
        return _community_admin_context_error('Community admin access required', 403, 'not_community_member', current_user, community, membership)

    if not owner and not community:
        active_memberships = CommunityMember.query.filter_by(user_id=user_id, status='Active').all()
        if len(active_memberships) == 1:
            membership = active_memberships[0]
            community = Community.query.filter_by(community_id=membership.community_id).first()
        else:
            return _community_admin_context_error(
                'Community context required', 400, 'missing_context', current_user, None, None,
                {'requires_community_selection': True}
            )

    if owner and not community and require_selected:
        return _community_admin_context_error(
            'Community context required', 400, 'missing_context', current_user, None, None,
            {'requires_community_selection': True, 'communities': _community_admin_available_communities()}
        )

    if not owner:
        if query_id or query_slug:
            requested = _community_admin_find_community(query_id, query_slug)
            if not requested or not community or requested.community_id != community.community_id:
                return _community_admin_context_error('Community admin access required', 403, 'requested_community_not_allowed', current_user, community, membership)
        if community and community.owner_user_id != user_id and not (membership and normalize_community_role(membership.role) in COMMUNITY_ADMIN_ROLES):
            return _community_admin_context_error('Community admin access required', 403, 'missing_admin_permission', current_user, community, membership)

    if community:
        logger.info(
            'community_admin_context route=%s user_id=%s is_platform_owner=%s '
            'session_selected_community_id=%s session_selected_community_slug=%s '
            'session_impersonating_community_id=%s session_impersonating_community_slug=%s '
            'resolved_community_id=%s resolved_slug=%s failure_reason=%s',
            request.path,
            user_id,
            owner,
            session.get('selected_community_id'),
            session.get('selected_community_slug'),
            session.get('impersonating_community_id'),
            session.get('impersonating_community_slug'),
            community.community_id,
            community.slug,
            None,
        )
        session['selected_community_id'] = community.community_id
        session['selected_community_slug'] = community.slug
        session.modified = True
    return current_user, community, membership, None, None

def require_community_admin():
    current_user, community, membership, error, status = _community_admin_resolve_context()
    if error:
        return None, None, None, error, status
    if is_platform_owner():
        return current_user, community, membership, None, None
    if community and community.owner_user_id == current_user.id:
        return current_user, community, membership, None, None
    if membership and normalize_community_role(membership.role) in COMMUNITY_ADMIN_ROLES:
        return current_user, community, membership, None, None
    return current_user, community, membership, jsonify({'success': False, 'error': 'Community admin access required'}), 403


def _community_admin_member_dict(membership, user=None):
    user = user or User.query.get(membership.user_id)
    _community_admin_apply_cad_permission_attrs(membership)
    return {
        'member_id': membership.id,
        'user_id': membership.user_id,
        'username': user.username if user else f'user-{membership.user_id}',
        'email': user.email if user else None,
        'community_role': membership.role,
        'role': membership.role,
        'cad_access': user_can_access_police_cad(False, membership.role, user=user, membership=membership),
        'can_access_police_cad': user_can_access_police_cad(False, membership.role, user=user, membership=membership),
        'department': membership.department,
        'rank': getattr(membership, 'rank', None),
        'callsign': membership.callsign,
        'joined_at': membership.joined_at.isoformat() if membership.joined_at else None,
        'status': membership.status or 'Active',
    }


def _community_admin_permission_dict(membership, user=None):
    user = user or User.query.get(membership.user_id)
    _community_admin_apply_cad_permission_attrs(membership)
    can_access = parse_bool(getattr(membership, 'can_access_police_cad', False)) or user_can_access_police_cad(False, membership.role, user=user, membership=membership)
    return {
        'user_id': membership.user_id,
        'username': user.username if user else f'user-{membership.user_id}',
        'community_role': membership.role,
        'can_access_police_cad': can_access,
        'department': membership.department,
        'rank': getattr(membership, 'rank', None) or '',
        'callsign': membership.callsign,
        'can_dispatch': parse_bool(getattr(membership, 'can_dispatch', False)),
        'can_manage_warrants': parse_bool(getattr(membership, 'can_manage_warrants', False)),
        'can_manage_evidence': parse_bool(getattr(membership, 'can_manage_evidence', False)),
        'can_manage_arrests': parse_bool(getattr(membership, 'can_manage_arrests', False)),
    }


def _community_admin_audit(community_id, action, result='success', target_user_id=None, details=None):
    safe_details = details or {}
    db.session.add(AuditLog(
        log_id=f'audit-{uuid.uuid4().hex}',
        community_id=community_id,
        actor=str(session.get('user_id') or ''),
        actor_role=session.get('role') or session.get('platform_role'),
        action=f'community_admin.{action}.{result}',
        record_type='community_admin',
        record_id=str(target_user_id or community_id),
        after_state=json.dumps(safe_details),
        ip_address=getattr(g, 'client_ip', request.remote_addr),
    ))


def _community_admin_activity_rows(community_id, limit=50):
    rows = []
    try:
        for row in AuditLog.query.filter_by(community_id=community_id).order_by(AuditLog.created_at.desc()).limit(limit).all():
            rows.append({
                'id': row.log_id,
                'action': row.action,
                'actor': row.actor,
                'actor_role': row.actor_role,
                'target': row.record_id,
                'details': _json_loads(row.after_state, row.after_state) or '',
                'ip_address': row.ip_address,
                'created_at': row.created_at.isoformat() if row.created_at else None,
            })
    except Exception as exc:
        logger.warning('Community admin audit log lookup failed community_id=%s error=%s', community_id, exc)
    if rows:
        return rows
    try:
        for row in ActivityLog.query.filter_by(community_id=community_id).order_by(ActivityLog.created_at.desc()).limit(limit).all():
            rows.append(activity_log_to_dict(row))
    except Exception as exc:
        logger.warning('Community admin activity log lookup failed community_id=%s error=%s', community_id, exc)
    try:
        remaining = max(limit - len(rows), 0)
        if remaining:
            for row in PlatformAdminLog.query.filter_by(tenant=community_id).order_by(PlatformAdminLog.created_at.desc()).limit(remaining).all():
                rows.append({
                    'id': row.id,
                    'action': row.action,
                    'actor': row.actor_user_id,
                    'target': row.target_user_id,
                    'details': _json_loads(row.details, row.details) or '',
                    'ip_address': row.ip_address,
                    'created_at': row.created_at.isoformat() if row.created_at else None,
                })
    except Exception as exc:
        logger.warning('Community admin platform log lookup failed community_id=%s error=%s', community_id, exc)
    return rows[:limit]


def _community_admin_settings_dict(community):
    keys = sorted(COMMUNITY_SETTING_KEYS | {'cad_permissions'})
    settings = {
        'community_id': community.community_id,
        'name': community.name,
        'cad_name': community.cad_name,
        'slug': community.slug,
        'status': community.status,
        'logo_url': community.logo_url or '',
        'primary_color': community.primary_color or '#1a1a1a',
        'secondary_color': community.secondary_color or '#0066cc',
        'accent_color': _community_admin_config_value(community.community_id, 'accent_color', '#ff2d2d'),
        'background_color': _community_admin_config_value(community.community_id, 'background_color', '#0b0b0d'),
        'text_color': _community_admin_config_value(community.community_id, 'text_color', '#f6f6f6'),
    }
    for key in keys:
        if key in {'accent_color', 'background_color', 'text_color'}:
            continue
        public_key = 'ranks' if key == 'officer_ranks' else key
        settings[public_key] = _community_admin_config_value(community.community_id, key, [] if key in COMMUNITY_LIST_SETTING_KEYS else {})
    settings.pop('cad_permissions', None)
    return settings


@app.route('/api/community-admin/overview', methods=['GET'])
@require_auth
def community_admin_overview():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    warnings = []

    def section(name, fallback, fn):
        try:
            return fn()
        except Exception as exc:
            logger.warning(
                'Community admin overview section failed path=%s user_id=%s resolved_community_id=%s resolved_slug=%s '
                'has_selected_community=%s has_impersonating_community=%s section=%s failure_reason=%s',
                request.path,
                session.get('user_id'),
                community.community_id,
                community.slug,
                bool(session.get('selected_community_id')),
                bool(session.get('impersonating_community_id')),
                name,
                type(exc).__name__,
            )
            warnings.append(f'{name} unavailable')
            return fallback

    members = section('members', [], lambda: CommunityMember.query.filter_by(community_id=community.community_id).all())
    user_ids = [m.user_id for m in members]
    users = section('users', {}, lambda: {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {})
    member_rows = section('member_rows', [], lambda: [_community_admin_member_dict(m, users.get(m.user_id)) for m in members])
    invites = section('invites', [], lambda: [i.to_dict() for i in CommunityInvite.query.filter_by(community_id=community.community_id).order_by(CommunityInvite.created_at.desc()).limit(25).all()])
    activity = section('activity', [], lambda: _community_admin_activity_rows(community.community_id, 25))
    permissions = section('cad_permissions', [], lambda: [_community_admin_permission_dict(m, users.get(m.user_id)) for m in members])
    settings = section('settings', {}, lambda: _community_admin_settings_dict(community))

    def count(fn):
        return section('metrics', 0, fn)

    roles = [normalize_community_role(m.role) for m in members]
    current_role = membership.role if membership else ('PlatformOwner' if is_platform_owner() else None)
    payload = {
        'success': True,
        'community': {
            'community_id': community.community_id,
            'name': community.name,
            'slug': community.slug,
            'cad_name': community.cad_name,
            'owner_user_id': community.owner_user_id,
            'status': (community.status or 'ACTIVE').upper(),
        },
        'current_user': {
            'id': current_user.id,
            'username': current_user.username,
            'role': current_user.role,
            'platform_role': getattr(current_user, 'platform_role', None) or session.get('platform_role'),
            'community_role': current_role,
            'is_platform_owner': is_platform_owner(),
        },
        'metrics': {
            'members': len(members),
            'officers': sum(1 for role in roles if role in {'Police', 'Officer', 'LEO'}),
            'dispatchers': sum(1 for role in roles if role in {'Dispatch', 'Dispatcher'}),
            'civilians': count(lambda: Civilian.query.filter_by(community_id=community.community_id).count()),
            'active_warrants': count(lambda: Warrant.query.filter(Warrant.community_id == community.community_id, or_(Warrant.status == 'Active', Warrant.warrant_status == 'Active')).count()),
            'open_reports': count(lambda: Incident.query.filter_by(community_id=community.community_id, status='Open').count()),
            'pending_applications': count(lambda: Application.query.filter_by(community_id=community.community_id, status='Pending').count()),
            'active_invites': count(lambda: CommunityInvite.query.filter_by(community_id=community.community_id, active=True).count()),
        },
        'members': member_rows,
        'invites': invites,
        'activity': activity,
        'cad_permissions': permissions,
        'settings': settings,
        'stats': {},
        'recent_activity': activity,
        'members_count': len(members),
        'invites_count': count(lambda: CommunityInvite.query.filter_by(community_id=community.community_id).count()),
        'departments': settings.get('departments', []),
        'ranks': settings.get('ranks', list(DEFAULT_COMMUNITY_RANKS)),
        'current_user_permissions': {
            'can_manage_community': True,
            'can_manage_members': True,
            'can_manage_settings': True,
            'can_manage_cad_permissions': True,
        },
        'warnings': warnings,
        'available_communities': _community_admin_available_communities() if is_platform_owner() else [],
    }
    payload['stats'] = payload['metrics']
    payload['overview'] = payload
    return jsonify(payload)


@app.route('/api/community-admin/members', methods=['GET'])
@require_auth
def community_admin_members():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    members = CommunityMember.query.filter_by(community_id=community.community_id).order_by(CommunityMember.joined_at.desc()).all()
    users = {u.id: u for u in User.query.filter(User.id.in_([m.user_id for m in members])).all()} if members else {}
    return jsonify({'success': True, 'members': [_community_admin_member_dict(m, users.get(m.user_id)) for m in members]})


@app.route('/api/community-admin/members', methods=['POST'])
@require_auth
def community_admin_member_create():
    current_user, community, acting_membership, error, status = require_community_admin()
    if error:
        return error, status
    data = _community_admin_body()
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    temporary_password = data.get('temporary_password') or data.get('password') or ''
    role = normalize_community_role(data.get('role') or 'Civilian')
    department = (data.get('department') or '').strip() or None
    callsign = (data.get('callsign') or data.get('badge') or data.get('badge_call_sign') or '').strip() or None
    status_value = (data.get('status') or 'Active').strip() or 'Active'

    if not username or len(username) > 255:
        return jsonify({'success': False, 'error': 'Valid username is required'}), 400
    if not email or '@' not in email or len(email) > 255:
        return jsonify({'success': False, 'error': 'Valid email is required'}), 400
    if role == 'PlatformOwner' or role not in COMMUNITY_SUPPORTED_ROLES:
        return jsonify({'success': False, 'error': 'Invalid community role'}), 400
    acting_role = normalize_community_role(getattr(acting_membership, 'role', None)) if acting_membership else None
    if role == 'CommunityOwner' and not (is_platform_owner() or community.owner_user_id == current_user.id or acting_role == 'CommunityOwner'):
        return jsonify({'success': False, 'error': 'Only PlatformOwner or CommunityOwner can assign CommunityOwner'}), 403
    if status_value not in {'Active', 'Inactive', 'Suspended'}:
        return jsonify({'success': False, 'error': 'Invalid member status'}), 400
    requested_cad_access = parse_bool(data.get('can_access_police_cad'))
    cad_eligible = role_is_cad_permission_eligible(role)
    if requested_cad_access and not cad_eligible:
        return jsonify({'success': False, 'error': 'Selected role is not eligible for Police CAD access'}), 400

    user = User.query.filter(func.lower(User.email) == email).first()
    created_user = False
    if user:
        username_owner = User.query.filter(func.lower(User.username) == username.lower(), User.id != user.id).first()
        if username_owner:
            return jsonify({'success': False, 'error': 'Username is already in use'}), 409
    else:
        if not validate_password_policy(temporary_password):
            return jsonify({'success': False, 'error': 'Password does not meet security requirements'}), 400
        if User.query.filter(func.lower(User.username) == username.lower()).first():
            return jsonify({'success': False, 'error': 'Username is already in use'}), 409
        user = User(username=username, email=email, password_hash=hash_password(temporary_password), role='Civilian', active=True)
        db.session.add(user)
        db.session.flush()
        created_user = True

    member = CommunityMember.query.filter_by(community_id=community.community_id, user_id=user.id).first()
    if not member:
        member = CommunityMember(
            community_id=community.community_id,
            user_id=user.id,
            role=role,
            department=department,
            callsign=callsign,
            status=status_value,
            joined_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(member)
    else:
        member.role = role
        member.department = department
        member.callsign = callsign
        member.status = status_value
        member.updated_at = datetime.utcnow()

    if not cad_eligible:
        if _revoke_community_admin_cad_permission(community.community_id, user.id):
            _community_admin_audit(community.community_id, 'cad_permission_revoked', target_user_id=user.id, details={'reason': 'role_not_eligible', 'role': role})
    elif data.get('can_access_police_cad') is not None:
        perms = _community_admin_cad_permissions(community.community_id)
        user_perms = perms.get(str(user.id), {})
        user_perms = user_perms if isinstance(user_perms, dict) else {}
        user_perms['can_access_police_cad'] = requested_cad_access
        user_perms['department'] = department or ''
        user_perms['callsign'] = callsign or ''
        perms[str(user.id)] = user_perms
        _community_admin_save_cad_permissions(community.community_id, perms)
        _community_admin_apply_cad_permission_attrs(member)

    _community_admin_audit(community.community_id, 'member_added', target_user_id=user.id, details={'role': role, 'created_user': created_user})
    invalidate_user_sessions(user.id)
    db.session.commit()
    return jsonify({'success': True, 'created_user': created_user, 'member': _community_admin_member_dict(member, user)}), 201


@app.route('/api/community-admin/members/<int:user_id>/role', methods=['POST'])
@require_auth
def community_admin_member_role(user_id):
    current_user, community, acting_membership, error, status = require_community_admin()
    if error:
        return error, status
    data = _community_admin_body()
    role = normalize_community_role(data.get('role'))
    if role == 'PlatformOwner' or role not in COMMUNITY_SUPPORTED_ROLES:
        return jsonify({'success': False, 'error': 'Invalid community role'}), 400
    target = CommunityMember.query.filter_by(community_id=community.community_id, user_id=user_id).first()
    if not target:
        return jsonify({'success': False, 'error': 'Member not found in this community'}), 404
    if community.owner_user_id == user_id and not is_platform_owner():
        return jsonify({'success': False, 'error': 'Community owner cannot be demoted'}), 403
    if role == 'CommunityOwner' and not (is_platform_owner() or normalize_community_role(getattr(acting_membership, 'role', None)) == 'CommunityOwner' or community.owner_user_id == current_user.id):
        return jsonify({'success': False, 'error': 'Only PlatformOwner or CommunityOwner can assign CommunityOwner'}), 403
    before = target.role
    target.role = role
    target.updated_at = datetime.utcnow()
    if not role_is_cad_permission_eligible(role) and _revoke_community_admin_cad_permission(community.community_id, user_id):
        _community_admin_audit(community.community_id, 'cad_permission_revoked', target_user_id=user_id, details={'reason': 'role_not_eligible', 'before_role': before, 'after_role': role})
    _community_admin_audit(community.community_id, 'member_role_changed', target_user_id=user_id, details={'before_role': before, 'after_role': role})
    invalidate_user_sessions(user_id)
    db.session.commit()
    return jsonify({'success': True, 'member': _community_admin_member_dict(target)})


@app.route('/api/community-admin/members/<int:user_id>/remove', methods=['POST'])
@require_auth
def community_admin_member_remove(user_id):
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    data = _community_admin_body()
    if community.owner_user_id == user_id and not (is_platform_owner() and data.get('confirm_owner')):
        return jsonify({'success': False, 'error': 'Community owner cannot be removed without PlatformOwner confirmation'}), 403
    target = CommunityMember.query.filter_by(community_id=community.community_id, user_id=user_id).first()
    if not target:
        return jsonify({'success': False, 'error': 'Member not found in this community'}), 404
    db.session.delete(target)
    perms = _community_admin_cad_permissions(community.community_id)
    perms.pop(str(user_id), None)
    _community_admin_save_cad_permissions(community.community_id, perms)
    _community_admin_audit(community.community_id, 'member_removed', target_user_id=user_id, details={'result': 'success'})
    invalidate_user_sessions(user_id)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Member removed from community'})


@app.route('/api/community-admin/invites', methods=['GET'])
@require_auth
def community_admin_invites():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    invites = CommunityInvite.query.filter_by(community_id=community.community_id).order_by(CommunityInvite.created_at.desc()).limit(100).all()
    return jsonify({'success': True, 'invites': [i.to_dict() for i in invites]})


@app.route('/api/community-admin/invites/create', methods=['POST'])
@require_auth
def community_admin_invite_create():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    data = _community_admin_body()
    role = normalize_community_role(data.get('role') or 'Civilian')
    if role == 'PlatformOwner' or role not in INVITE_JOIN_ROLES:
        return jsonify({'success': False, 'error': 'Invalid community role'}), 400
    acting_role = normalize_community_role(getattr(membership, 'role', None)) if membership else None
    can_create_admin_invite = is_platform_owner() or community.owner_user_id == current_user.id or acting_role in {'CommunityOwner', 'Owner'}
    if role in INVITE_ADMIN_LEVEL_ROLES and not can_create_admin_invite:
        return jsonify({'success': False, 'error': 'Only CommunityOwner or PlatformOwner can create admin-level invites'}), 403
    max_uses = data.get('max_uses', 1)
    try:
        max_uses = None if max_uses in (None, '', 0, '0') else max(1, int(max_uses))
    except Exception:
        return jsonify({'success': False, 'error': 'max_uses must be a positive number'}), 400
    try:
        days = int(data.get('expires_in_days', 7))
    except Exception:
        days = 7
    expires_at = datetime.utcnow() + timedelta(days=max(days, 1)) if days else None
    invite = CommunityInvite(
        invite_code=_community_admin_generate_invite_code(10),
        community_id=community.community_id,
        role=role,
        created_by=current_user.id,
        expires_at=expires_at,
        max_uses=max_uses,
        uses=0,
        active=True,
    )
    db.session.add(invite)
    _community_admin_audit(community.community_id, 'invite_created', details={'role': role, 'max_uses': max_uses, 'expires_at': expires_at.isoformat() if expires_at else None})
    db.session.commit()
    invite_payload = invite.to_dict()
    invite_payload['code'] = invite.invite_code
    invite_payload['invite_link'] = f'https://gtavcad.app/join?code={invite.invite_code}'
    return jsonify({'success': True, 'invite': invite_payload})


@app.route('/api/community-admin/invites/<int:invite_id>/revoke', methods=['POST'])
@require_auth
def community_admin_invite_revoke(invite_id):
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    invite = CommunityInvite.query.filter_by(id=invite_id, community_id=community.community_id).first()
    if not invite:
        return jsonify({'success': False, 'error': 'Invite not found in this community'}), 404
    invite.active = False
    _community_admin_audit(community.community_id, 'invite_revoked', details={'invite_id': invite_id})
    db.session.commit()
    return jsonify({'success': True, 'message': 'Invite revoked'})


@app.route('/api/community-admin/cad-permissions', methods=['GET'])
@require_auth
def community_admin_cad_permissions():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    members = CommunityMember.query.filter_by(community_id=community.community_id).order_by(CommunityMember.joined_at.desc()).all()
    users = {u.id: u for u in User.query.filter(User.id.in_([m.user_id for m in members])).all()} if members else {}
    return jsonify({'success': True, 'permissions': [_community_admin_permission_dict(m, users.get(m.user_id)) for m in members]})


@app.route('/api/community-admin/cad-permissions/<int:user_id>/update', methods=['POST'])
@require_auth
def community_admin_cad_permission_update(user_id):
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    target = CommunityMember.query.filter_by(community_id=community.community_id, user_id=user_id).first()
    if not target:
        return jsonify({'success': False, 'error': 'Member not found in this community'}), 404
    data = _community_admin_body()
    role = normalize_community_role(target.role)
    requested_access = parse_bool(data.get('can_access_police_cad'))
    if requested_access and not role_is_cad_permission_eligible(role):
        return jsonify({'success': False, 'error': 'Selected role is not eligible for Police CAD access'}), 400
    if not role_is_cad_permission_eligible(role):
        _revoke_community_admin_cad_permission(community.community_id, user_id)
        _community_admin_audit(community.community_id, 'cad_permission_revoked', target_user_id=user_id, details={'reason': 'role_not_eligible', 'role': role})
        invalidate_user_sessions(user_id)
        db.session.commit()
        return jsonify({'success': True, 'permission': _community_admin_permission_dict(target)})
    callsign = (data.get('callsign') or '').strip() or None
    if callsign:
        existing = CommunityMember.query.filter(
            CommunityMember.community_id == community.community_id,
            CommunityMember.callsign == callsign,
            CommunityMember.user_id != user_id,
        ).first()
        if existing:
            return jsonify({'success': False, 'error': 'Callsign already exists in this community'}), 409
    target.department = (data.get('department') or '').strip() or target.department
    target.callsign = callsign
    target.updated_at = datetime.utcnow()
    perms = _community_admin_cad_permissions(community.community_id)
    user_perms = perms.get(str(user_id), {}) if isinstance(perms.get(str(user_id), {}), dict) else {}
    for key in ('can_access_police_cad', 'can_dispatch', 'can_manage_warrants', 'can_manage_evidence', 'can_manage_arrests'):
        if key in data:
            user_perms[key] = parse_bool(data.get(key))
    user_perms['rank'] = (data.get('rank') or '').strip()
    perms[str(user_id)] = user_perms
    _community_admin_save_cad_permissions(community.community_id, perms)
    _community_admin_audit(community.community_id, 'cad_permission_updated', target_user_id=user_id, details={'permission': user_perms, 'department': target.department, 'callsign': target.callsign})
    invalidate_user_sessions(user_id)
    db.session.commit()
    return jsonify({'success': True, 'permission': _community_admin_permission_dict(target)})


@app.route('/api/community-admin/settings', methods=['GET'])
@require_auth
def community_admin_settings_get():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    return jsonify({'success': True, 'settings': _community_admin_settings_dict(community)})


@app.route('/api/community-admin/settings', methods=['POST'])
@require_auth
def community_admin_settings_post():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    data = _community_admin_body()
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Community name is required'}), 400
        community.name = name
    if 'cad_name' in data:
        cad_name = (data.get('cad_name') or '').strip()
        if not cad_name:
            return jsonify({'success': False, 'error': 'CAD display name is required'}), 400
        community.cad_name = cad_name
    if 'logo_url' in data:
        community.logo_url = (data.get('logo_url') or '').strip() or None
    def _valid_hex(value):
        return isinstance(value, str) and re.fullmatch(r'#[0-9A-Fa-f]{6}', value.strip())
    for color_key in ('primary_color', 'secondary_color'):
        if color_key in data:
            color = (data.get(color_key) or '').strip()
            if not _valid_hex(color):
                return jsonify({'success': False, 'error': f'{color_key} must be a #RRGGBB hex color'}), 400
            setattr(community, color_key, color)
    for color_key in ('accent_color', 'background_color', 'text_color'):
        if color_key in data:
            color = (data.get(color_key) or '').strip()
            if not _valid_hex(color):
                return jsonify({'success': False, 'error': f'{color_key} must be a #RRGGBB hex color'}), 400
            _community_admin_set_config_value(community.community_id, color_key, color)
    if data.get('reset_colors') is True:
        community.primary_color = '#1a1a1a'
        community.secondary_color = '#0066cc'
        _community_admin_set_config_value(community.community_id, 'accent_color', '#ff2d2d')
        _community_admin_set_config_value(community.community_id, 'background_color', '#0b0b0d')
        _community_admin_set_config_value(community.community_id, 'text_color', '#f6f6f6')
    updated = []
    for raw_key, value in data.items():
        key = COMMUNITY_SETTING_ALIASES.get(raw_key, raw_key)
        if key in COMMUNITY_SETTING_KEYS:
            _community_admin_set_config_value(community.community_id, key, value)
            updated.append(key)
    community.updated_at = datetime.utcnow()
    _community_admin_audit(community.community_id, 'settings_updated', details={'updated_keys': updated, 'name_changed': 'name' in data, 'cad_name_changed': 'cad_name' in data})
    db.session.commit()
    return jsonify({'success': True, 'settings': _community_admin_settings_dict(community)})


@app.route('/api/community-admin/activity', methods=['GET'])
@require_auth
def community_admin_activity():
    current_user, community, membership, error, status = require_community_admin()
    if error:
        return error, status
    return jsonify({'success': True, 'activity': _community_admin_activity_rows(community.community_id, 100)})


@app.route('/api/ai/config', methods=['GET'])
def get_ai_config_status():
    cfg = get_platform_ai_config()
    return jsonify({
        'success': True,
        'ai_enabled': bool(cfg['enabled'] and cfg['has_api_key']),
        'provider': 'OpenRouter',
        'model': cfg['model'],
        'configured': cfg['has_api_key'],
    })


@app.route('/api/community-admin/ai-status', methods=['GET'])
@require_auth
def community_admin_ai_status():
    _current_user, community, _membership, error, status = require_community_admin()
    if error:
        return error, status
    community_id = community.community_id
    cfg = get_platform_ai_config()
    usage_count = scoped_query(AIGenerationLog, community_id).filter_by(community_id=community_id).count()
    return jsonify({
        'success': True,
        'ai_available': bool(cfg['enabled'] and cfg['has_api_key']),
        'provider': 'Platform OpenRouter',
        'model': cfg['model'],
        'usage_count': usage_count,
        'community_id': community_id,
        'community_slug': community.slug,
    })




# ---------------------------------------------------------------------------
# Tenant-scoped CAD Evidence Attachment Routes
# ---------------------------------------------------------------------------

EVIDENCE_ATTACHMENT_PARENT_MODELS = {
    'case_id': (CaseFile, 'case_id'),
    'evidence_id': (Evidence, 'evidence_id'),
    'arrest_id': (Arrest, 'arrest_id'),
    'warrant_id': (Warrant, 'warrant_id'),
    # Court packets are represented by CAD cases in the current schema.
    'court_packet_id': (CaseFile, 'case_id'),
}


def _attachment_parent_values(source):
    values = {key: (source.get(key) or '').strip() for key in EVIDENCE_ATTACHMENT_PARENT_MODELS.keys() if (source.get(key) or '').strip()}
    if not values.get('case_id') and (source.get('caseNumber') or '').strip():
        values['case_id'] = (source.get('caseNumber') or '').strip()
    return values


def _validate_attachment_parents(community_id, parent_values):
    if not parent_values:
        return {}, None
    found = {}
    for field, value in parent_values.items():
        model, attr = EVIDENCE_ATTACHMENT_PARENT_MODELS[field]
        record = scoped_query(model, community_id).filter(getattr(model, attr) == value).first()
        if not record and field == 'case_id':
            record = scoped_query(model, community_id).filter(CaseFile.case_number == value).first()
        if not record and field == 'court_packet_id':
            record = scoped_query(model, community_id).filter(CaseFile.case_number == value).first()
        if not record:
            return None, _cad_json_error('Parent CAD record not found for this community', 404)
        found[field] = record
    return found, None



def _auto_create_attachment_parent(community_id, form):
    case_number = f"CASE-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
    description = (form.get('description') or form.get('evidenceDescription') or '').strip()
    category = (form.get('category') or form.get('evidenceType') or 'Evidence').strip() or 'Evidence'
    case = CaseFile(
        community_id=community_id,
        case_id=case_number,
        case_number=case_number,
        title=f'Auto-generated Evidence Case {case_number}',
        case_type='incident',
        status='open',
        priority='medium',
        report_notes=description or 'Auto-created for evidence upload without a selected parent case.',
        created_by=_actor_name(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    evidence_id = f"EVD-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3).upper()}"
    evidence = Evidence(
        community_id=community_id,
        evidence_id=evidence_id,
        case_number=case_number,
        evidence_type=category,
        evidence_description=description,
        collected_by=_actor_name(),
        officer=_actor_name(),
        storage_status='Attachment Pending',
        status='Active',
        notes='Auto-created parent evidence record for attachment upload.',
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    case.linked_evidence_ids = evidence_id
    case.evidence_ids = evidence_id
    db.session.add(case)
    db.session.add(evidence)
    _cad_audit('case_auto_created_for_evidence', community_id, case.case_id, {'case_number': case_number, 'evidence_id': evidence_id})
    return case, evidence, {
        'case_id': case.case_id,
        'case_number': case.case_number,
        'evidence_id': evidence.evidence_id,
        'court_packet_id': case.case_id,
        'court_packet_warning': 'Court packet is represented by the generated CAD case in the current schema.',
    }

def _primary_attachment_parent(parent_values):
    for field in ('case_id', 'evidence_id', 'arrest_id', 'warrant_id', 'court_packet_id'):
        if parent_values.get(field):
            return field.replace('_id', ''), parent_values[field]
    return 'attachment', 'unlinked'


def _safe_user_summary(user_id):
    user = User.query.get(user_id) if user_id else None
    if not user:
        return {'user_id': user_id}
    return {'user_id': user.id, 'username': user.username}


def _attachment_download_url(attachment):
    if attachment.storage_mode == 'local_volume' and not attachment.is_deleted:
        return f'/api/cad/evidence/attachments/{attachment.attachment_id}/download'
    return None


def _attachment_to_dict(attachment):
    payload = {
        'attachment_id': attachment.attachment_id,
        'community_id': attachment.community_id,
        'case_id': attachment.case_id,
        'evidence_id': attachment.evidence_id,
        'arrest_id': attachment.arrest_id,
        'warrant_id': attachment.warrant_id,
        'court_packet_id': attachment.court_packet_id,
        'original_filename': attachment.original_filename,
        'file_type': attachment.file_type,
        'mime_type': attachment.mime_type,
        'file_size': attachment.file_size,
        'storage_mode': attachment.storage_mode,
        'external_url': attachment.external_url if attachment.storage_mode == 'link_only' else None,
        'download_url': _attachment_download_url(attachment),
        'description': attachment.description or '',
        'category': attachment.category or '',
        'review_status': attachment.review_status or 'submitted',
        'created_at': attachment.created_at.isoformat() if attachment.created_at else None,
        'updated_at': attachment.updated_at.isoformat() if attachment.updated_at else None,
        'deleted_at': attachment.deleted_at.isoformat() if attachment.deleted_at else None,
        'is_deleted': bool(attachment.is_deleted),
        'uploaded_by': _safe_user_summary(attachment.uploaded_by_user_id),
    }
    return payload


def _attachment_admin_decision():
    decision = _current_police_cad_access_decision(request.path)
    role = (decision.get('normalized_role') or decision.get('role') or '').lower()
    platform_role = decision.get('platform_role')
    return platform_role == 'PlatformOwner' or role in {'communityowner', 'communityadmin', 'owner', 'admin', 'supervisor'}


def _safe_external_evidence_url(value):
    raw = (value or '').strip()
    if not raw or len(raw) > 2048 or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, parsed.fragment))


WARRANT_FIELD_MAP = {
    'warrant_type': ('warrant_type', 'warrantType'),
    'warrant_number': ('warrant_number', 'warrantNumber'),
    'judge_or_authority': ('judge_or_authority', 'judgeOrAuthority'),
    'issuing_agency': ('issuing_agency', 'issuingAgency', 'warrantIssuer', 'issuer'),
    'subject_name': ('subject_name', 'subjectName', 'warrantName', 'suspectName'),
    'subject_dob': ('subject_dob', 'subjectDob'),
    'subject_address': ('subject_address', 'subjectAddress'),
    'charges_or_basis': ('charges_or_basis', 'chargesOrBasis', 'warrantCharges', 'charges'),
    'probable_cause': ('probable_cause', 'probableCause', 'warrantNotes', 'justification', 'notes'),
    'search_location': ('search_location', 'searchLocation'),
    'items_to_seize': ('items_to_seize', 'itemsToSeize'),
    'court_case_number': ('court_case_number', 'courtCaseNumber'),
    'bench_failure_reason': ('bench_failure_reason', 'benchFailureReason'),
    'administrative_basis': ('administrative_basis', 'administrativeBasis'),
    'inspection_scope': ('inspection_scope', 'inspectionScope'),
    'originating_jurisdiction': ('originating_jurisdiction', 'originatingJurisdiction'),
    'extradition_location': ('extradition_location', 'extraditionLocation'),
    'fugitive_last_known_location': ('fugitive_last_known_location', 'fugitiveLastKnownLocation'),
    'alias_names': ('alias_names', 'aliasNames'),
    'execution_instructions': ('execution_instructions', 'executionInstructions'),
    'expiration_date': ('expiration_date', 'expirationDate', 'warrantExpiration', 'expiration'),
    'status': ('status', 'warrantStatus'),
}

WARRANT_REQUIRED_BY_TYPE = {
    'Arrest Warrant': ('subject_name', 'charges_or_basis', 'probable_cause'),
    'Search Warrant': ('search_location', 'items_to_seize', 'probable_cause', 'judge_or_authority'),
    'Bench Warrant': ('subject_name', 'court_case_number', 'bench_failure_reason', 'judge_or_authority'),
    'Administrative Warrant': ('subject_name', 'administrative_basis', 'issuing_agency', 'inspection_scope'),
    'Extradition Warrant': ('subject_name', 'originating_jurisdiction', 'extradition_location', 'charges_or_basis'),
    'Fugitive Warrant': ('subject_name', 'charges_or_basis', 'fugitive_last_known_location'),
    'Alias Warrant': ('alias_names', 'charges_or_basis', 'probable_cause'),
}


def _payload_get(data, *keys, default=''):
    for key in keys:
        value = data.get(key) if isinstance(data, dict) else None
        if value not in (None, ''):
            return str(value).strip()
    return default


def _normalize_warrant_payload(data):
    payload = {}
    for field, keys in WARRANT_FIELD_MAP.items():
        payload[field] = _payload_get(data, *keys)
    payload['warrant_type'] = payload.get('warrant_type') or 'Arrest Warrant'
    payload['status'] = payload.get('status') or 'Active'
    if not payload.get('issuing_agency'):
        payload['issuing_agency'] = _actor_name()
    return payload


def _validate_warrant_payload(payload):
    errors = []
    warrant_type = payload.get('warrant_type') or 'Arrest Warrant'
    if warrant_type not in WARRANT_TYPES:
        errors.append(f'warrant_type must be one of: {", ".join(WARRANT_TYPES)}')
        return errors
    if not payload.get('expiration_date'):
        errors.append('expiration_date is required')
    for field in WARRANT_REQUIRED_BY_TYPE.get(warrant_type, ()):
        if not payload.get(field):
            errors.append(f'{field} is required for {warrant_type}')
    if warrant_type == 'Administrative Warrant' and not payload.get('subject_name'):
        errors.append('subject_name or entity name is required for Administrative Warrant')
    return errors


def generate_global_warrant_id():
    for _ in range(100):
        candidate = f"WRT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4).upper()}"
        if not Warrant.query.filter_by(warrant_id=candidate).first():
            return candidate
    raise RuntimeError('Unable to generate unique warrant ID')


def generate_warrant_number(community_id, warrant_type):
    prefix = TYPE_PREFIXES.get(warrant_type, 'WAR')
    date_part = datetime.utcnow().strftime('%Y%m%d')
    for _ in range(25):
        suffix = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
        candidate = f'{prefix}-{date_part}-{suffix}'
        exists = scoped_query(Warrant, community_id).filter_by(warrant_number=candidate).first()
        if not exists:
            return candidate
    return f'{prefix}-{date_part}-{secrets.token_hex(4).upper()}'


def _ensure_warrant_identity(warrant, community_id):
    if not getattr(warrant, 'warrant_type', None):
        warrant.warrant_type = 'Arrest Warrant'
    if not getattr(warrant, 'warrant_number', None):
        warrant.warrant_number = generate_warrant_number(community_id, warrant.warrant_type or 'Arrest Warrant')
    if not getattr(warrant, 'status', None) or not getattr(warrant, 'warrant_status', None):
        _set_warrant_status(warrant, getattr(warrant, 'status', None) or getattr(warrant, 'warrant_status', None) or 'Active')
    return warrant


def _find_warrant_for_cad(community_id, warrant_id):
    warrant = scoped_query(Warrant, community_id).filter(
        or_(Warrant.warrant_id == warrant_id, Warrant.warrant_number == warrant_id)
    ).first()
    if warrant:
        _ensure_warrant_identity(warrant, community_id)
    return warrant


def _apply_warrant_payload(warrant, payload):
    for field, value in payload.items():
        if field == 'warrant_number' and not value:
            continue
        if hasattr(warrant, field):
            setattr(warrant, field, value or None)
    warrant.warrant_name = payload.get('subject_name') or warrant.warrant_name or ''
    warrant.warrant_charges = payload.get('charges_or_basis') or warrant.warrant_charges or ''
    warrant.warrant_issuer = payload.get('issuing_agency') or warrant.warrant_issuer or ''
    warrant.warrant_notes = payload.get('probable_cause') or warrant.warrant_notes or ''
    warrant.justification = payload.get('probable_cause') or warrant.justification or ''
    _set_warrant_status(warrant, payload.get('status') or warrant.status or warrant.warrant_status or 'Active')


def _ordered_civilian_query(community_id):
    return scoped_query(Civilian, community_id).order_by(Civilian.created_at.desc(), Civilian.id.desc())


def _warrant_subject_civilian_matches(subject, community_id, *, fuzzy_limit=10, response_limit=25):
    """Find warrant subject candidates without limiting away exact matches first."""
    normalized_subject = _normalize_name(subject)
    if not normalized_subject:
        return {'exact': [], 'fuzzy': [], 'selected': None, 'multiple_exact': False, 'match_type': 'none'}

    # Do not apply a small fuzzy limit before exact matching; exact same-name civilians
    # may be older than the newest fuzzy candidates in active communities.
    exact = [
        civilian for civilian in _ordered_civilian_query(community_id).all()
        if _normalize_name(_civilian_full_name(civilian)) == normalized_subject
    ]
    if len(exact) == 1:
        return {'exact': exact, 'fuzzy': [], 'selected': exact[0], 'multiple_exact': False, 'match_type': 'exact'}
    if len(exact) > 1:
        return {'exact': exact[:response_limit], 'fuzzy': [], 'selected': None, 'multiple_exact': True, 'match_type': 'multiple_exact'}

    fuzzy = _ordered_civilian_query(community_id).filter(_civilian_name_filter(subject)).limit(fuzzy_limit).all()
    return {'exact': [], 'fuzzy': fuzzy, 'selected': None, 'multiple_exact': False, 'match_type': 'fuzzy' if fuzzy else 'none'}


def _link_warrant_subject_to_civilian(warrant, community_id):
    subject = _warrant_value(warrant, 'subject_name', 'warrant_name')
    matches = _warrant_subject_civilian_matches(subject, community_id)
    civilian = matches.get('selected')
    if not civilian:
        return matches.get('exact') or []
    warrant.civilian_id = civilian.civilian_id
    if not getattr(warrant, 'subject_dob', None) and civilian.date_of_birth:
        warrant.subject_dob = civilian.date_of_birth.isoformat()
    if not getattr(warrant, 'subject_address', None) and civilian.address:
        warrant.subject_address = civilian.address
    return [civilian]





def _traffic_stop_payload_metadata(data):
    keys = (
        'vehicleInfo', 'vehicle_info', 'driverDob', 'driver_dob', 'driverAddress', 'driver_address',
        'citationViolation', 'citationAmount', 'citationCourtRequired', 'citationCourtDate', 'citationNotes',
        'warningReason', 'warningType', 'warningNotes', 'arrestCharges', 'arrestNarrative', 'arrestPenalty',
        'badge', 'officerBadge', 'traffic_stop_id', 'linked_arrest_id', 'linked_case_id',
    )
    return {k: data.get(k) for k in keys if data.get(k) not in (None, '')}


def _traffic_stop_notes_with_metadata(stop, data):
    notes = (data.get('notes') or getattr(stop, 'notes', None) or '').strip()
    metadata = _traffic_stop_payload_metadata(data)
    if not metadata:
        return notes
    payload = {'public_notes': notes, 'metadata': metadata}
    return json.dumps(payload, default=str)


def _traffic_stop_metadata(stop):
    notes = getattr(stop, 'notes', None) or ''
    try:
        parsed = json.loads(notes)
        if isinstance(parsed, dict):
            return parsed.get('metadata') if isinstance(parsed.get('metadata'), dict) else {}
    except Exception:
        pass
    return {}


def _traffic_stop_public_notes(stop):
    notes = getattr(stop, 'notes', None) or ''
    try:
        parsed = json.loads(notes)
        if isinstance(parsed, dict):
            return parsed.get('public_notes') or ''
    except Exception:
        pass
    return notes


def _apply_traffic_stop_payload(stop, data):
    stop.driver_name = (data.get('driverName') or data.get('driver_name') or stop.driver_name or '').strip()
    stop.plate = (data.get('trafficPlate') or data.get('plate') or stop.plate or '').strip()
    stop.reason = (data.get('trafficReason') or data.get('reason') or stop.reason or '').strip()
    stop.outcome = (data.get('trafficOutcome') or data.get('outcome') or stop.outcome or '').strip()
    stop.officer = (data.get('officerName') or data.get('officer') or stop.officer or '').strip()
    stop.location = (data.get('trafficLocation') or data.get('location') or stop.location or '').strip()
    stop.notes = _traffic_stop_notes_with_metadata(stop, data)
    stop.updated_at = datetime.utcnow()


def _find_traffic_stop_for_cad(community_id, traffic_stop_id):
    return scoped_query(TrafficStop, community_id).filter_by(stop_id=traffic_stop_id).first()


def _traffic_stop_safe_dict(stop):
    payload = traffic_stop_to_dict(stop)
    meta = _traffic_stop_metadata(stop)
    payload.update({
        'vehicleInfo': meta.get('vehicleInfo') or meta.get('vehicle_info') or '',
        'driverDob': meta.get('driverDob') or meta.get('driver_dob') or '',
        'driverAddress': meta.get('driverAddress') or meta.get('driver_address') or '',
        'notes': _traffic_stop_public_notes(stop),
        'metadata': {k: v for k, v in meta.items() if k not in {'storage_path', 'api_key'}},
    })
    return payload


@app.route('/api/cad/traffic-stops', methods=['POST'])
def cad_traffic_stop_create():
    community_id, error = _require_cad_community()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    stop_id = (data.get('id') or data.get('traffic_stop_id') or data.get('stop_id') or f"TRF-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}").strip()
    stop = scoped_query(TrafficStop, community_id).filter_by(stop_id=stop_id).first()
    if stop is None:
        stop = TrafficStop(community_id=community_id, stop_id=stop_id, created_at=datetime.utcnow())
        db.session.add(stop)
    _apply_traffic_stop_payload(stop, {**data, 'traffic_stop_id': stop_id})
    _cad_audit('traffic_stop_saved', community_id, stop_id, {'traffic_stop_id': stop_id, 'outcome': stop.outcome})
    db.session.commit()
    return jsonify({'success': True, 'traffic_stop': _traffic_stop_safe_dict(stop), 'traffic_stop_id': stop.stop_id})


def _traffic_pdf_storage_error():
    storage_cfg = get_storage_config()
    storage_root = storage_cfg.get('root')
    if (
        storage_cfg.get('mode') != 'local_volume'
        or not storage_cfg.get('direct_uploads_enabled')
        or not storage_root
        or not os.path.isdir(os.path.expanduser(storage_root))
    ):
        return 'PDF storage is not configured. Enable local evidence storage to generate traffic PDFs.'
    return None


def _traffic_pdf_bytes(kind, stop, data, community_id):
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError('ReportLab is required to generate traffic PDFs') from exc
    community_name, cad_name = _community_display_names(community_id)
    meta = {**_traffic_stop_metadata(stop), **(data or {})}
    number = f"{kind.upper()}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2).upper()}"
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    y = height - 54
    title = 'Traffic Citation' if kind == 'citation' else 'Traffic Warning'
    rows = [
        ('Community', community_name), ('CAD', cad_name), (f'{title} #', number), ('Traffic Stop #', stop.stop_id),
        ('Officer / Badge', f"{stop.officer or meta.get('officerName') or ''} {meta.get('badge') or meta.get('officerBadge') or ''}".strip()),
        ('Driver', stop.driver_name), ('DOB', meta.get('driverDob') or meta.get('driver_dob') or ''),
        ('Address', meta.get('driverAddress') or meta.get('driver_address') or ''),
        ('Vehicle', meta.get('vehicleInfo') or meta.get('vehicle_info') or ''), ('Plate', stop.plate),
        ('Location', stop.location), ('Issue Date', datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')),
    ]
    if kind == 'citation':
        rows.extend([
            ('Violation', meta.get('citationViolation') or stop.reason),
            ('Fine Amount', meta.get('citationAmount') or ''),
            ('Court Required', meta.get('citationCourtRequired') or 'No'),
            ('Court Date', meta.get('citationCourtDate') or ''),
            ('Notes / Narrative', meta.get('citationNotes') or _traffic_stop_public_notes(stop)),
        ])
    else:
        rows.extend([
            ('Warning Reason', meta.get('warningReason') or stop.reason),
            ('Warning Type', meta.get('warningType') or 'Written'),
            ('Notes', meta.get('warningNotes') or _traffic_stop_public_notes(stop)),
        ])
    c.setFont('Helvetica-Bold', 16)
    c.drawString(54, y, title)
    y -= 28
    c.setFont('Helvetica', 10)
    for label, value in rows:
        value = str(value or '—')
        c.setFont('Helvetica-Bold', 9)
        c.drawString(54, y, f'{label}:')
        c.setFont('Helvetica', 9)
        text = c.beginText(170, y)
        for line in [value[i:i+88] for i in range(0, len(value), 88)] or ['—']:
            text.textLine(line)
            y -= 12
        c.drawText(text)
        y -= 5
        if y < 72:
            c.showPage()
            y = height - 54
    c.setFont('Helvetica-Oblique', 8)
    c.drawString(54, 38, 'Generated by GTAVCAD. Review for accuracy before issuing.')
    c.save()
    return buffer.getvalue(), number


def _store_traffic_pdf(kind, stop, data, community_id):
    storage_error = _traffic_pdf_storage_error()
    if storage_error:
        return None, None, storage_error
    pdf_bytes, number = _traffic_pdf_bytes(kind, stop, data, community_id)
    attachment_id = f"ATT-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3).upper()}"
    filename = f"{number}.pdf"
    stored_leaf = f'{secrets.token_hex(12)}_{filename}'
    rel_path = relative_storage_path(community_id, 'traffic_stop', stop.stop_id, attachment_id, stored_leaf)
    _, save_error = _write_pdf_bytes_to_local_storage(pdf_bytes, rel_path)
    if save_error:
        return None, None, 'Traffic PDF could not be stored securely'
    description = f"Generated traffic {kind} PDF {number} for stop {stop.stop_id}"
    attachment = EvidenceAttachment(
        attachment_id=attachment_id,
        community_id=community_id,
        uploaded_by_user_id=session.get('user_id'),
        original_filename=filename,
        stored_filename=stored_leaf,
        file_type=f"Traffic {kind.title()} PDF",
        mime_type='application/pdf',
        file_size=len(pdf_bytes),
        storage_mode='local_volume',
        storage_path=rel_path,
        description=description,
        category=f"Traffic {kind.title()} PDF",
        review_status='Generated',
        is_deleted=False,
        created_at=datetime.utcnow(),
    )
    evidence = Evidence(
        evidence_id=f"EVD-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3).upper()}",
        community_id=community_id,
        evidence_type=f"Traffic {kind.title()} PDF",
        evidence_description=description,
        collected_by=_actor_name(),
        officer=_actor_name(),
        storage_status='Generated',
        chain_of_custody=f'Generated from traffic stop {stop.stop_id}; attachment {attachment_id}',
        status='Active',
        created_at=datetime.utcnow(),
    )
    db.session.add(attachment)
    db.session.add(evidence)
    return attachment, number, None


def _traffic_pdf_endpoint(traffic_stop_id, kind):
    community_id, error = _require_cad_community()
    if error:
        return error
    stop = _find_traffic_stop_for_cad(community_id, traffic_stop_id)
    if not stop:
        return _cad_json_error('Traffic stop not found', 404)
    data = request.get_json(silent=True) or {}
    attachment, number, err = _store_traffic_pdf(kind, stop, data, community_id)
    if err:
        return _cad_json_error(err, 400 if 'configured' in err else 500)
    _cad_audit(f'traffic_{kind}_pdf_generated', community_id, stop.stop_id, {'traffic_stop_id': stop.stop_id, 'attachment_id': attachment.attachment_id})
    db.session.commit()
    return jsonify({'success': True, 'traffic_stop_id': stop.stop_id, f'{kind}_number': number, 'attachment_id': attachment.attachment_id, 'download_url': _attachment_download_url(attachment)})


@app.route('/api/cad/traffic-stops/<traffic_stop_id>/citation-pdf', methods=['POST'])
def cad_traffic_stop_citation_pdf(traffic_stop_id):
    return _traffic_pdf_endpoint(traffic_stop_id, 'citation')


@app.route('/api/cad/traffic-stops/<traffic_stop_id>/warning-pdf', methods=['POST'])
def cad_traffic_stop_warning_pdf(traffic_stop_id):
    return _traffic_pdf_endpoint(traffic_stop_id, 'warning')


def _traffic_arrest_for_stop(community_id, stop_id):
    token = f'traffic_stop_id={stop_id}'
    return scoped_query(Arrest, community_id).filter(Arrest.report_notes.ilike(f'%{token}%')).order_by(Arrest.created_at.desc()).first()


def _create_or_get_traffic_arrest(community_id, stop, data):
    existing = _traffic_arrest_for_stop(community_id, stop.stop_id)
    if existing:
        return existing, False
    meta = {**_traffic_stop_metadata(stop), **(data or {})}
    civilian = _find_civilian_for_arrest('', stop.driver_name)
    arrest_id = f"arr-traffic-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"
    notes = f"Created from traffic stop. traffic_stop_id={stop.stop_id}; vehicle={meta.get('vehicleInfo') or ''}; plate={stop.plate}"
    arrest = Arrest(
        community_id=community_id,
        arrest_id=arrest_id,
        civilian_id=civilian.civilian_id if civilian else '',
        suspect_name=stop.driver_name,
        charges=(meta.get('arrestCharges') or data.get('charges') or stop.reason or '').strip(),
        arresting_officer=stop.officer or data.get('officerName') or '',
        arrest_location=stop.location,
        penalty=(meta.get('arrestPenalty') or data.get('penalty') or '').strip(),
        report_notes=notes,
        narrative=(meta.get('arrestNarrative') or data.get('narrative') or stop.reason or '').strip(),
        status='Active',
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.session.add(arrest)
    return arrest, True


@app.route('/api/cad/traffic-stops/<traffic_stop_id>/create-arrest', methods=['POST'])
def cad_traffic_stop_create_arrest(traffic_stop_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    stop = _find_traffic_stop_for_cad(community_id, traffic_stop_id)
    if not stop:
        return _cad_json_error('Traffic stop not found', 404)
    arrest, created = _create_or_get_traffic_arrest(community_id, stop, request.get_json(silent=True) or {})
    _cad_audit('traffic_stop_arrest_created' if created else 'traffic_stop_arrest_reused', community_id, traffic_stop_id, {'traffic_stop_id': traffic_stop_id, 'arrest_id': arrest.arrest_id})
    db.session.commit()
    return jsonify({'success': True, 'created': created, 'arrest': arrest_to_dict(arrest)})


@app.route('/api/cad/traffic-stops/<traffic_stop_id>/book-jail', methods=['POST'])
def cad_traffic_stop_book_jail(traffic_stop_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    stop = _find_traffic_stop_for_cad(community_id, traffic_stop_id)
    if not stop:
        return _cad_json_error('Traffic stop not found', 404)
    arrest, created = _create_or_get_traffic_arrest(community_id, stop, request.get_json(silent=True) or {})
    inmate, booking, _hearing = _ensure_arrest_custody_and_hearing(arrest)
    _cad_audit('traffic_stop_jail_booked', community_id, traffic_stop_id, {'traffic_stop_id': traffic_stop_id, 'arrest_id': arrest.arrest_id, 'created_arrest': created})
    db.session.commit()
    return jsonify({'success': True, 'arrest': arrest_to_dict(arrest), 'booking': jail_booking_to_dict(booking) if booking else None, 'inmate': inmate_to_dict(inmate) if inmate else None})


@app.route('/api/cad/traffic-stops/<traffic_stop_id>/court-date', methods=['POST'])
def cad_traffic_stop_court_date(traffic_stop_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    stop = _find_traffic_stop_for_cad(community_id, traffic_stop_id)
    if not stop:
        return _cad_json_error('Traffic stop not found', 404)
    arrest, created = _create_or_get_traffic_arrest(community_id, stop, request.get_json(silent=True) or {})
    hearing = scoped_query(Hearing, community_id).filter_by(arrest_id=arrest.arrest_id).first()
    if hearing is None:
        hearing = Hearing(
            community_id=community_id,
            hearing_id=f"hearing-{int(datetime.utcnow().timestamp() * 1000)}-{secrets.token_hex(5)}",
            civilian_id=arrest.civilian_id or '',
            suspect_name=arrest.suspect_name or '',
            charges=arrest.charges or '',
            hearing_type='Arraignment',
            scheduled_at=(request.get_json(silent=True) or {}).get('courtDate') or _default_hearing_time(),
            notes=f'Created from traffic stop {stop.stop_id}.',
            arrest_id=arrest.arrest_id,
            filing_officer=arrest.arresting_officer or _actor_name(),
            status='Scheduled',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(hearing)
    _cad_audit('traffic_stop_court_date_created', community_id, traffic_stop_id, {'traffic_stop_id': traffic_stop_id, 'arrest_id': arrest.arrest_id, 'hearing_id': hearing.hearing_id, 'created_arrest': created})
    db.session.commit()
    return jsonify({'success': True, 'arrest': arrest_to_dict(arrest), 'hearing': hearing_to_dict(hearing)})

@app.route('/api/cad/warrants/find-civilian', methods=['POST'])
def cad_warrant_find_civilian():
    community_id, error = _require_cad_community()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    subject = (data.get('subject_name') or data.get('name') or '').strip()
    if not subject:
        return _cad_json_error('subject_name is required', 400)
    match_result = _warrant_subject_civilian_matches(subject, community_id)
    selected = match_result.get('selected')
    candidates = match_result.get('exact') or match_result.get('fuzzy') or []
    return jsonify({
        'success': True,
        'match_count': len(candidates),
        'multiple': bool(match_result.get('multiple_exact')),
        'match_type': match_result.get('match_type'),
        'civilian': _civilian_response(selected) if selected else None,
        'matches': [_civilian_response(c) for c in candidates],
    })


def _criminal_record_for_civilian(civilian, community_id):
    full_name = _civilian_full_name(civilian)
    warrants = [warrant_to_dict(w) for w in scoped_query(Warrant, community_id).filter(or_(Warrant.civilian_id == civilian.civilian_id, Warrant.subject_name.ilike(full_name), Warrant.warrant_name.ilike(full_name))).order_by(Warrant.created_at.desc()).all() if _warrant_matches_civilian(w, civilian) or (w.civilian_id == civilian.civilian_id)]
    arrests = [arrest_to_dict(a) for a in scoped_query(Arrest, community_id).filter(or_(Arrest.civilian_id == civilian.civilian_id, Arrest.suspect_name.ilike(full_name))).order_by(Arrest.created_at.desc()).all()]
    citations = [citation_to_dict(c) for c in scoped_query(Citation, community_id).filter(Citation.civilian_id == civilian.civilian_id).order_by(Citation.created_at.desc()).all()]
    jail_records = [jail_booking_to_dict(j) for j in scoped_query(JailBooking, community_id).filter(or_(JailBooking.civilian_id == civilian.civilian_id, JailBooking.suspect_name.ilike(full_name))).order_by(JailBooking.created_at.desc()).all()]
    hearings = [hearing_to_dict(h) for h in scoped_query(Hearing, community_id).filter(or_(Hearing.civilian_id == civilian.civilian_id, Hearing.suspect_name.ilike(full_name))).order_by(Hearing.created_at.desc()).all()]
    traffic_stops = [traffic_stop_to_dict(t) for t in scoped_query(TrafficStop, community_id).filter(TrafficStop.driver_name.ilike(full_name)).order_by(TrafficStop.created_at.desc()).all()]
    cases = [_case_to_dict(c) for c in scoped_query(CaseFile, community_id).filter(CaseFile.defendant_civilian_id == civilian.civilian_id).order_by(CaseFile.created_at.desc()).all()]
    evidence_count = scoped_query(EvidenceAttachment, community_id).filter(EvidenceAttachment.is_deleted.is_(False), or_(EvidenceAttachment.case_id.in_([c.get('case_id') for c in cases] or ['']), EvidenceAttachment.arrest_id.in_([a.get('id') for a in arrests] or ['']), EvidenceAttachment.warrant_id.in_([w.get('warrant_id') for w in warrants] or ['']))).count()
    has = any([warrants, arrests, citations, jail_records, hearings, traffic_stops, cases, evidence_count])
    return {'civilian': _civilian_response(civilian), 'warrants': warrants, 'arrests': arrests, 'citations': citations, 'jailRecords': jail_records, 'hearings': hearings, 'trafficStops': traffic_stops, 'cases': cases, 'evidence': [{'evidenceCount': evidence_count}] if evidence_count else [], 'hasCriminalHistory': bool(has)}

@app.route('/api/criminal-records/search', methods=['GET'])
def criminal_records_search():
    community_id, error = _require_cad_community()
    if error:
        return error
    query = (request.args.get('q') or request.args.get('query') or '').strip()
    if not query:
        return _cad_json_error('query is required', 400)
    civilians = _civilian_search_query(query, community_id=community_id).order_by(Civilian.created_at.desc()).limit(25).all()
    records = [_criminal_record_for_civilian(c, community_id) for c in civilians]
    flattened = {
        'civilians': [r['civilian'] for r in records],
        'warrants': [item for r in records for item in r['warrants']],
        'arrests': [item for r in records for item in r['arrests']],
        'citations': [item for r in records for item in r['citations']],
        'jailRecords': [item for r in records for item in r['jailRecords']],
        'hearings': [item for r in records for item in r['hearings']],
        'trafficStops': [item for r in records for item in r['trafficStops']],
        'cases': [item for r in records for item in r['cases']],
        'evidence': [item for r in records for item in r['evidence']],
    }
    return jsonify({'success': True, 'records': records, 'results': flattened['civilians'], 'total': len(records), **flattened})

@app.route('/api/cad/case-packets/generate', methods=['POST'])
def cad_case_packet_generate():
    community_id, error = _require_cad_community()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    arrest_id = (data.get('arrest_id') or '').strip()
    warrant_id = (data.get('warrant_id') or '').strip()
    traffic_stop_id = (data.get('traffic_stop_id') or '').strip()
    civilian_id = (data.get('civilian_id') or '').strip()
    arrest = scoped_query(Arrest, community_id).filter_by(arrest_id=arrest_id).first() if arrest_id else None
    warrant = _find_warrant_for_cad(community_id, warrant_id) if warrant_id else None
    traffic_stop = _find_traffic_stop_for_cad(community_id, traffic_stop_id) if traffic_stop_id else None
    if not civilian_id:
        civilian_id = (arrest.civilian_id if arrest else '') or (warrant.civilian_id if warrant else '')
    title = data.get('title') or f"Case Packet {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
    case_id = f"CASE-PKT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
    packet_notes = data.get('notes') or 'Generated case packet record. Evidence metadata/counts only; storage paths are not exposed.'
    professional_sections = (
        "Packet Cover Page; Charge Sheet; Arrest Report; Incident Narrative; Declaration of Probable Cause; "
        "Booking Records; Identification Records; Evidence Log; Consent to Search / Waiver / Search Authority; "
        "Witness / Victim Statements; Supplementary Reports; Court / Hearing Information; Emergency Protective Order; "
        "Officer Certification / Review Notes. AI-DRAFTED SECTION — OFFICER REVIEW REQUIRED."
    )
    if traffic_stop:
        packet_notes = f'{packet_notes} Linked traffic_stop_id={traffic_stop.stop_id}; plate={traffic_stop.plate}; location={traffic_stop.location}.'
    case = CaseFile(community_id=community_id, case_id=case_id, case_number=case_id, title=title, case_type='case_packet', linked_arrest_id=arrest_id or None, linked_warrant_id=warrant_id or None, defendant_civilian_id=civilian_id or None, charges=(arrest.charges if arrest else None) or (warrant.charges_or_basis if warrant else None), report_notes=f"{packet_notes}\n\n{professional_sections}\nGTAVCAD roleplay court packet. AI-drafted sections require officer review before use in court RP.", created_by=_actor_name(), status='open', created_at=datetime.utcnow())
    db.session.add(case)
    evidence = Evidence(
        community_id=community_id,
        evidence_id=f"EVD-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3).upper()}",
        case_number=case_id,
        evidence_type='CASE PACKET PDF',
        evidence_description=f'Generated court packet for {(arrest.suspect_name if arrest else (warrant.subject_name if warrant else "Unknown Subject"))} — {((arrest.charges if arrest else None) or (warrant.charges_or_basis if warrant else None) or case_id)}',
        officer=_actor_name(),
        storage_status='Generated'
    )
    db.session.add(evidence)
    case.linked_evidence_ids = evidence.evidence_id
    case.evidence_ids = evidence.evidence_id
    _cad_audit('case_packet_generated', community_id, case_id, {'case_id': case_id, 'arrest_id': arrest_id, 'warrant_id': warrant_id, 'traffic_stop_id': traffic_stop_id, 'civilian_id': civilian_id})
    db.session.commit()
    return jsonify({'success': True, 'case_packet': _case_to_dict(case), 'case_id': case_id})

@app.route('/api/cad/case-packets', methods=['GET'])
def cad_case_packets_list():
    community_id, error = _require_cad_community()
    if error:
        return error
    packets = scoped_query(CaseFile, community_id).filter(func.lower(CaseFile.case_type) == 'case_packet').order_by(CaseFile.created_at.desc()).limit(200).all()
    return jsonify({'success': True, 'case_packets': [_case_to_dict(packet) for packet in packets]})

@app.route('/api/cad/case-packets/<case_id>', methods=['DELETE'])
def cad_case_packet_delete(case_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    case = scoped_query(CaseFile, community_id).filter(or_(CaseFile.case_id == case_id, CaseFile.case_number == case_id), func.lower(CaseFile.case_type) == 'case_packet').first()
    if not case:
        return _cad_json_error('Case packet not found', 404)
    case.status = 'archived'
    case.updated_at = datetime.utcnow()
    _cad_audit('case_packet_archived', community_id, case.case_id, {'case_id': case.case_id})
    db.session.commit()
    return jsonify({'success': True})

@app.route('/api/cad/warrants', methods=['GET'])
def cad_warrants_list():
    community_id, error = _require_cad_community()
    if error:
        return error
    warrants = scoped_query(Warrant, community_id).order_by(Warrant.created_at.desc()).all()
    changed = False
    for warrant in warrants:
        before = (getattr(warrant, 'warrant_type', None), getattr(warrant, 'warrant_number', None), getattr(warrant, 'status', None), getattr(warrant, 'warrant_status', None))
        _ensure_warrant_identity(warrant, community_id)
        changed = changed or before != (warrant.warrant_type, warrant.warrant_number, warrant.status, warrant.warrant_status)
    if changed:
        db.session.commit()
    return jsonify({'success': True, 'warrants': [warrant_to_dict(w) for w in warrants]})


@app.route('/api/cad/warrants', methods=['POST'])
def cad_warrants_create():
    community_id, error = _require_cad_community()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    payload = _normalize_warrant_payload(data)
    errors = _validate_warrant_payload(payload)
    if errors:
        return jsonify({'success': False, 'error': 'Warrant validation failed', 'details': {'errors': errors}, 'request_id': getattr(g, 'request_id', None)}), 400
    warrant_type = payload['warrant_type']
    warrant_number = payload.get('warrant_number') or generate_warrant_number(community_id, warrant_type)
    while scoped_query(Warrant, community_id).filter_by(warrant_number=warrant_number).first():
        warrant_number = generate_warrant_number(community_id, warrant_type)
    warrant_id = generate_global_warrant_id()
    warrant = Warrant(
        community_id=community_id,
        warrant_id=warrant_id,
        warrant_number=warrant_number,
        warrant_type=warrant_type,
        created_by_user_id=session.get('user_id'),
        created_at=datetime.utcnow(),
    )
    _apply_warrant_payload(warrant, payload)
    _link_warrant_subject_to_civilian(warrant, community_id)
    db.session.add(warrant)
    _cad_audit('warrant_created', community_id, warrant.warrant_id, {'warrant_id': warrant.warrant_id, 'warrant_number': warrant.warrant_number, 'warrant_type': warrant.warrant_type})
    db.session.commit()
    return jsonify({'success': True, 'warrant': warrant_to_dict(warrant)}), 201


@app.route('/api/cad/warrants/<warrant_id>', methods=['GET'])
def cad_warrant_detail(warrant_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    warrant = _find_warrant_for_cad(community_id, warrant_id)
    if not warrant:
        return _cad_json_error('Warrant not found', 404)
    db.session.commit()
    return jsonify({'success': True, 'warrant': warrant_to_dict(warrant)})


@app.route('/api/cad/warrants/<warrant_id>/status', methods=['POST'])
def cad_warrant_status(warrant_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    warrant = _find_warrant_for_cad(community_id, warrant_id)
    if not warrant:
        return _cad_json_error('Warrant not found', 404)
    data = request.get_json(silent=True) or {}
    new_status = _payload_get(data, 'status', 'warrantStatus')
    if not new_status:
        return _cad_json_error('status is required', 400)
    _set_warrant_status(warrant, new_status)
    _cad_audit('warrant_status_updated', community_id, warrant.warrant_id, {'warrant_id': warrant.warrant_id, 'status': new_status})
    db.session.commit()
    return jsonify({'success': True, 'warrant': warrant_to_dict(warrant)})


def _write_pdf_bytes_to_local_storage(pdf_bytes, relative_path):
    path, error = resolve_local_path(relative_path)
    if error:
        return None, error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    return path, None


def _community_display_names(community_id):
    community = Community.query.filter_by(community_id=community_id).first()
    if not community:
        return community_id, DEFAULT_COMMUNITY_CAD_NAME
    community_name = getattr(community, 'name', None) or getattr(community, 'community_name', None) or community_id
    cad_name = getattr(community, 'cad_name', None) or getattr(community, 'display_name', None) or DEFAULT_COMMUNITY_CAD_NAME
    return community_name, cad_name


@app.route('/api/cad/warrants/<warrant_id>/generate-pdf', methods=['POST'])
def cad_warrant_generate_pdf(warrant_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    storage_cfg = get_storage_config()
    storage_root = storage_cfg.get('root')
    if (
        storage_cfg.get('mode') != 'local_volume'
        or not storage_cfg.get('direct_uploads_enabled')
        or not storage_root
        or not os.path.isdir(os.path.expanduser(storage_root))
    ):
        return _cad_json_error('Direct PDF storage is not configured. Enable local evidence storage to generate warrant PDFs.', 400)
    warrant = _find_warrant_for_cad(community_id, warrant_id)
    if not warrant:
        return _cad_json_error('Warrant not found', 404)
    _ensure_warrant_identity(warrant, community_id)
    community_name, cad_name = _community_display_names(community_id)
    creator = _safe_user_summary(warrant.created_by_user_id or session.get('user_id')).get('username') or _actor_name()
    approver = _safe_user_summary(warrant.approved_by_user_id).get('username') if warrant.approved_by_user_id else ''
    try:
        pdf_bytes = build_warrant_pdf(warrant, community_name=community_name, cad_name=cad_name, created_by=creator, approved_by=approver)
    except RuntimeError as exc:
        return _cad_json_error(str(exc), 500)
    filename = safe_warrant_pdf_filename(warrant.warrant_number, warrant.warrant_type)
    attachment_id = f"ATT-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3).upper()}"
    stored_leaf = f'{secrets.token_hex(12)}_{filename}'
    rel_path = relative_storage_path(community_id, 'warrant', warrant.warrant_id, attachment_id, stored_leaf)
    _, save_error = _write_pdf_bytes_to_local_storage(pdf_bytes, rel_path)
    if save_error:
        return _cad_json_error('Warrant PDF could not be stored securely', 500)
    description = f'Generated {warrant.warrant_type} PDF for warrant {warrant.warrant_number}'
    attachment = EvidenceAttachment(
        attachment_id=attachment_id,
        community_id=community_id,
        warrant_id=warrant.warrant_id,
        uploaded_by_user_id=session.get('user_id'),
        original_filename=filename,
        stored_filename=stored_leaf,
        file_type='Warrant PDF',
        mime_type='application/pdf',
        file_size=len(pdf_bytes),
        storage_mode='local_volume',
        storage_path=rel_path,
        description=description,
        category='Warrant PDF',
        review_status='Generated',
        is_deleted=False,
        created_at=datetime.utcnow(),
    )
    evidence = Evidence(
        evidence_id=f"EVD-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3).upper()}",
        community_id=community_id,
        evidence_type='Warrant PDF',
        evidence_description=description,
        collected_by=_actor_name(),
        officer=_actor_name(),
        storage_status='Generated',
        chain_of_custody=f'Generated from warrant {warrant.warrant_number}; attachment {attachment_id}',
        status='Active',
        created_at=datetime.utcnow(),
    )
    warrant.pdf_attachment_id = attachment.attachment_id
    warrant.pdf_generated_at = datetime.utcnow()
    warrant.updated_at = datetime.utcnow()
    db.session.add(attachment)
    db.session.add(evidence)
    _cad_audit('warrant_pdf_generated', community_id, warrant.warrant_id, {'warrant_id': warrant.warrant_id, 'attachment_id': attachment.attachment_id})
    db.session.commit()
    return jsonify({
        'success': True,
        'warrant_id': warrant.warrant_id,
        'warrant_number': warrant.warrant_number,
        'attachment_id': attachment.attachment_id,
        'pdf_generated_at': warrant.pdf_generated_at.isoformat() if warrant.pdf_generated_at else None,
        'download_url': _warrant_pdf_download_url(warrant),
        'message': 'Added to evidence log',
    })


@app.route('/api/cad/warrants/<warrant_id>/download-pdf', methods=['GET'])
def cad_warrant_download_pdf(warrant_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    warrant = _find_warrant_for_cad(community_id, warrant_id)
    if not warrant or not warrant.pdf_attachment_id:
        return _cad_json_error('Warrant PDF not found', 404)
    attachment = scoped_query(EvidenceAttachment, community_id).filter_by(attachment_id=warrant.pdf_attachment_id, is_deleted=False).first()
    if not attachment or attachment.storage_mode != 'local_volume' or attachment.warrant_id != warrant.warrant_id:
        return _cad_json_error('Warrant PDF not found', 404)
    path, path_error = resolve_local_path(attachment.storage_path)
    if path_error or not path.exists() or not path.is_file():
        return _cad_json_error('Warrant PDF not found', 404)
    _cad_audit('warrant_pdf_downloaded', community_id, warrant.warrant_id, {'warrant_id': warrant.warrant_id, 'attachment_id': attachment.attachment_id})
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=attachment.original_filename or 'warrant.pdf', mimetype='application/pdf', conditional=True)


@app.route('/api/cad/evidence/attachments/config', methods=['GET'])
def cad_evidence_attachment_config():
    community_id, error = _require_cad_community()
    if error:
        return error
    cfg = get_storage_config()
    return jsonify({
        'success': True,
        'community_id': community_id,
        'storage_mode': cfg.get('mode'),
        'direct_uploads_enabled': bool(cfg.get('direct_uploads_enabled')),
        'direct_upload_message': LINK_ONLY_DISABLED_MESSAGE,
    })


@app.route('/api/cad/evidence/attachments', methods=['POST'])
def cad_evidence_attachment_create():
    community_id, error = _require_cad_community()
    if error:
        return error
    form = request.form if request.form else (request.get_json(silent=True) or {})
    parent_values = _attachment_parent_values(form)
    generated_parent = None
    parent_records, parent_error = _validate_attachment_parents(community_id, parent_values)
    if parent_error:
        return parent_error
    for field, record in parent_records.items():
        _, attr = EVIDENCE_ATTACHMENT_PARENT_MODELS[field]
        parent_values[field] = getattr(record, attr)
    if not parent_values:
        case, evidence, generated_parent = _auto_create_attachment_parent(community_id, form)
        parent_values['case_id'] = case.case_id
        parent_values['evidence_id'] = evidence.evidence_id
        parent_values['court_packet_id'] = case.case_id

    file_obj = request.files.get('file') if request.files else None
    if file_obj is not None and not (getattr(file_obj, 'filename', '') or '').strip():
        file_obj = None
    external_url = (form.get('external_url') or form.get('evidenceLink') or '').strip()
    if not file_obj and not external_url:
        if generated_parent:
            db.session.rollback()
        return _cad_json_error('Either file or external_url is required', 400)

    if external_url:
        external_url = _safe_external_evidence_url(external_url)
        if not external_url:
            if generated_parent:
                db.session.rollback()
            return _cad_json_error('external_url must be a valid http or https URL without embedded credentials or control characters', 400)

    storage_cfg = get_storage_config()
    attachment_id = f"ATT-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{secrets.token_hex(3).upper()}"
    attachment = EvidenceAttachment(
        attachment_id=attachment_id,
        community_id=community_id,
        uploaded_by_user_id=session.get('user_id'),
        case_id=parent_values.get('case_id'),
        evidence_id=parent_values.get('evidence_id'),
        arrest_id=parent_values.get('arrest_id'),
        warrant_id=parent_values.get('warrant_id'),
        court_packet_id=parent_values.get('court_packet_id'),
        description=(form.get('description') or '').strip(),
        category=(form.get('category') or '').strip(),
        review_status='submitted',
        created_at=datetime.utcnow(),
    )

    if file_obj:
        if storage_cfg.get('mode') != 'local_volume' or not storage_cfg.get('direct_uploads_enabled'):
            if generated_parent:
                db.session.rollback()
            return _cad_json_error(LINK_ONLY_DISABLED_MESSAGE, 400)
        meta, validation_error = validate_upload(file_obj)
        if validation_error:
            if generated_parent:
                db.session.rollback()
            return _cad_json_error(validation_error, 400)
        parent_type, parent_id = _primary_attachment_parent(parent_values)
        rel_path = relative_storage_path(community_id, parent_type, parent_id, attachment_id, meta['stored_leaf'])
        save_error = save_local_file(file_obj, rel_path)
        if save_error:
            if generated_parent:
                db.session.rollback()
            return _cad_json_error('Evidence file could not be stored securely', 500)
        attachment.original_filename = meta['original_filename']
        attachment.stored_filename = meta['stored_leaf']
        attachment.file_type = meta['file_type']
        attachment.mime_type = meta['mime_type']
        attachment.file_size = meta['file_size']
        attachment.storage_mode = 'local_volume'
        attachment.storage_path = rel_path
    else:
        attachment.original_filename = None
        attachment.file_type = 'external_link'
        attachment.storage_mode = 'link_only'
        attachment.external_url = external_url

    db.session.add(attachment)
    _cad_audit('evidence_attachment_created', community_id, attachment.case_id, {
        'attachment_id': attachment.attachment_id,
        'storage_mode': attachment.storage_mode,
        'parent_fields': sorted(parent_values.keys()),
    })
    db.session.commit()
    response = {'success': True, 'attachment': _attachment_to_dict(attachment)}
    if generated_parent:
        response['generated_parent'] = generated_parent
        response.update({k: v for k, v in generated_parent.items() if k in {'case_id', 'case_number', 'evidence_id', 'court_packet_id'}})
        if generated_parent.get('court_packet_warning'):
            response['warning'] = generated_parent['court_packet_warning']
    return jsonify(response), 201


@app.route('/api/cad/evidence/attachments', methods=['GET'])
def cad_evidence_attachment_list():
    community_id, error = _require_cad_community()
    if error:
        return error
    query = scoped_query(EvidenceAttachment, community_id)
    for field in EVIDENCE_ATTACHMENT_PARENT_MODELS.keys():
        value = (request.args.get(field) or '').strip()
        if value:
            query = query.filter(getattr(EvidenceAttachment, field) == value)
    include_deleted = (request.args.get('include_deleted') or '').lower() in {'1', 'true', 'yes'}
    if include_deleted and not _attachment_admin_decision():
        return _cad_json_error('Admin evidence attachment access required', 403)
    if not include_deleted:
        query = query.filter(EvidenceAttachment.is_deleted.is_(False))
    attachments = query.order_by(EvidenceAttachment.created_at.desc()).all()
    return jsonify({'success': True, 'attachments': [_attachment_to_dict(a) for a in attachments]})


@app.route('/api/cad/evidence/attachments/<attachment_id>/download', methods=['GET'])
def cad_evidence_attachment_download(attachment_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    attachment = scoped_query(EvidenceAttachment, community_id).filter_by(attachment_id=attachment_id).first()
    if not attachment or attachment.is_deleted:
        return _cad_json_error('Evidence attachment not found', 404)
    if attachment.storage_mode != 'local_volume':
        return _cad_json_error('External evidence links are opened by URL, not downloaded by the CAD server', 400)
    path, path_error = resolve_local_path(attachment.storage_path)
    if path_error:
        return _cad_json_error('Evidence attachment is not available', 404)
    if not path.exists() or not path.is_file():
        return _cad_json_error('Evidence attachment is not available', 404)
    _cad_audit('evidence_attachment_downloaded', community_id, attachment.case_id, {'attachment_id': attachment.attachment_id})
    db.session.commit()
    return send_file(path, as_attachment=True, download_name=attachment.original_filename or 'evidence_attachment', mimetype=attachment.mime_type or None, conditional=True)


@app.route('/api/cad/evidence/attachments/<attachment_id>', methods=['DELETE'])
def cad_evidence_attachment_delete(attachment_id):
    community_id, error = _require_cad_community()
    if error:
        return error
    attachment = scoped_query(EvidenceAttachment, community_id).filter_by(attachment_id=attachment_id).first()
    if not attachment or attachment.is_deleted:
        return _cad_json_error('Evidence attachment not found', 404)
    if attachment.uploaded_by_user_id != session.get('user_id') and not _attachment_admin_decision():
        return _cad_json_error('Evidence attachment delete permission required', 403)
    attachment.is_deleted = True
    attachment.deleted_at = datetime.utcnow()
    attachment.updated_at = datetime.utcnow()
    _cad_audit('evidence_attachment_deleted', community_id, attachment.case_id, {'attachment_id': attachment.attachment_id})
    db.session.commit()
    return jsonify({'success': True, 'attachment': _attachment_to_dict(attachment)})


@app.route('/api/cad/ai/status', methods=['GET'])
def cad_ai_status():
    guard, err = _cad_ai_guard()
    if err:
        return err
    cfg = get_ai_config()
    return jsonify({'success': True, 'ai_enabled': cfg['enabled'], 'provider': cfg['provider'], 'model': cfg['model'], 'configured': cfg['configured'], 'has_api_key': cfg['has_api_key'], 'community_id': guard.get('community_id'), 'community_slug': guard.get('community_slug')})


def _cad_ai_guard(case_id=None):
    if not session.get('user_id'):
        return None, (jsonify({'success': False, 'error': 'Unauthorized'}), 401)
    if not current_role_allows_police_cad():
        return None, (jsonify({'success': False, 'error': 'CAD AI access requires an authorized CAD role in this community.'}), 403)
    community_ctx = resolve_active_community()
    community_id = (community_ctx or {}).get('community_id')
    if not community_id:
        return None, (jsonify({'success': False, 'error': 'Community context required'}), 400)
    case_obj = None
    if case_id:
        case_obj = scoped_query(CaseFile, community_id).filter_by(case_id=case_id).first()
        if not case_obj:
            return None, (jsonify({'success': False, 'error': 'Case not found'}), 404)
    return {'community_id': community_id, 'community_slug': (community_ctx or {}).get('slug'), 'case': case_obj}, None


def _log_ai_generation(generation_type, success, input_params, output_summary='', tokens_used=None, error_message=None):
    try:
        log = AIGenerationLog(
            log_id=f"AI-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}",
            community_id=community_id,
            generation_type=generation_type,
            input_params=json.dumps(input_params)[:4000],
            output_summary=(output_summary or '')[:4000],
            tokens_used=tokens_used if isinstance(tokens_used, int) else None,
            status='Success' if success else 'Failure',
            error_message=(error_message or '')[:500],
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _ai_json_route(route_type, system_prompt, user_prompt, input_meta):
    data, err = ai_runtime_or_error()
    if err:
        _log_ai_generation(route_type, False, input_meta, error_message=err)
        return jsonify({'success': False, 'error': err}), 503
    out, provider_err, usage = chat_json(system_prompt, user_prompt)
    if provider_err:
        _log_ai_generation(route_type, False, input_meta, error_message=provider_err)
        return jsonify({'success': False, 'error': 'AI provider request failed'}), 502
    _log_ai_generation(route_type, True, {**(input_meta or {}), 'provider': data.get('provider'), 'model': data.get('model'), 'user_id': session.get('user_id')}, output_summary=json.dumps(out)[:1200], tokens_used=(usage or {}).get('total_tokens'))
    return out, data


def _default_ai_system_rules(extra=''):
    base = (
        "Do not invent evidence, clip/bodycam links, officer names, suspect statements, or facts. "
        "Do not decide guilt. Charge outputs are suggestions only unless already supplied as official charges. "
        "Preserve provided facts exactly. Flag conflicts and missing court-sensitive details. "
        "Do not claim to have reviewed links/clips/screenshots. Use professional GTA RP law-enforcement tone."
    )
    return f"{base} {extra}".strip()


@app.route('/api/cad/ai/generate-911-call', methods=['POST'])
def cad_ai_generate_911_call():
    guard, err = _cad_ai_guard()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    area = (data.get('area_of_play') or 'City Only').strip() or 'City Only'
    prompt = f"Generate realistic GTA V Los Santos 911 call JSON with caller_name, location, incident_type, description, priority, recommended_units, status. Area of play: {area}. Input: {json.dumps(data)}"
    ai_result = _ai_json_route('generate_911_call', 'Do not invent impossible locations. City Only unless explicitly provided. No markdown.', prompt, data)
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    out, _cfg = ai_result
    return jsonify({'success': True, 'call': {**out, 'status': 'Pending'}})


@app.route('/api/cad/ai/cleanup-report', methods=['POST'])
def cad_ai_cleanup_report():
    guard, err = _cad_ai_guard()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    prompt = f"Clean this police RP report for grammar/tone without inventing facts. Return JSON keys cleaned_text, missing_info, notes. Input: {json.dumps(data)}"
    ai_result = _ai_json_route('cleanup_report', 'Preserve facts exactly. Professional police report style.', prompt, data)
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    out, _cfg = ai_result
    return jsonify({'success': True, 'cleaned_text': out.get('cleaned_text',''), 'missing_info': out.get('missing_info', []), 'notes': out.get('notes', [])})


@app.route('/api/cad/ai/incident-report', methods=['POST'])
def cad_ai_incident_report():
    data = request.get_json(silent=True) or {}
    guard, err = _cad_ai_guard(case_id=data.get('case_id'))
    if err:
        return err
    prompt = f"Write incident report JSON: narrative,timeline,probable_cause,officer_actions,suspect_actions,scene_control,evidence_references,recommended_missing_fields. Use only provided input: {json.dumps(data)}"
    ai_result = _ai_json_route('incident_report', 'Do not invent evidence, names, links, or statements.', prompt, data)
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    out, _cfg = ai_result
    return jsonify({'success': True, 'report': out})


def _safe_split_csv(raw):
    if not raw:
        return []
    return [x.strip() for x in str(raw).split(',') if x and x.strip()]


def _incident_context_from_inputs(payload, community_id):
    case_id = (payload.get('case_id') or '').strip()
    call_id = (payload.get('call_id') or '').strip()
    civilian_id = (payload.get('civilian_id') or '').strip()
    vehicle_id = (payload.get('vehicle_id') or '').strip()
    plate_number = (payload.get('plate_number') or '').strip()

    context = {'case': None, 'dispatch_call': None, 'civilian': None, 'vehicle': None, 'charges': [], 'warrants': [], 'bolos': [], 'evidence': [], 'arrests': [], 'traffic_stops': [], 'related_cases': [], 'missing_info': []}

    if case_id:
        context['case'] = scoped_query(CaseFile, community_id).filter_by(case_id=case_id).first()
        if not context['case']:
            context['missing_info'].append('case_id_not_found')
    if call_id:
        context['dispatch_call'] = scoped_query(DispatchCall, community_id).filter_by(call_id=call_id).first()
        if not context['dispatch_call']:
            c911 = scoped_query(Call911, community_id).filter_by(call_id=call_id).first()
            if c911:
                context['dispatch_call'] = c911
            else:
                context['missing_info'].append('call_id_not_found')
    if civilian_id:
        context['civilian'] = scoped_query(Civilian, community_id).filter_by(civilian_id=civilian_id).first()
        if not context['civilian']:
            context['missing_info'].append('civilian_id_not_found')
    if vehicle_id:
        context['vehicle'] = scoped_query(Vehicle, community_id).filter_by(vehicle_id=vehicle_id).first()
    if not context['vehicle'] and plate_number:
        context['vehicle'] = scoped_query(Vehicle, community_id).filter_by(plate=plate_number).first()
    if (vehicle_id or plate_number) and not context['vehicle']:
        context['missing_info'].append('vehicle_not_found')

    case_obj = context['case']
    if case_obj:
        context['charges'] = scoped_query(CaseCharge, community_id).filter_by(case_id=case_obj.case_id).all()
        context['related_cases'] = scoped_query(CaseFile, community_id).filter(CaseFile.case_id != case_obj.case_id, or_(CaseFile.defendant_civilian_id == case_obj.defendant_civilian_id, CaseFile.arrest_id == case_obj.arrest_id)).limit(5).all()
        linked_call = (case_obj.linked_911_call_id or '').strip()
        if linked_call and not context['dispatch_call']:
            context['dispatch_call'] = scoped_query(DispatchCall, community_id).filter_by(call_id=linked_call).first()
        linked_evidence = _safe_split_csv(case_obj.linked_evidence_ids) + _safe_split_csv(case_obj.evidence_ids)
        if linked_evidence:
            context['evidence'] = scoped_query(Evidence, community_id).filter(Evidence.evidence_id.in_(linked_evidence)).all()

    civ_id = civilian_id or (case_obj.defendant_civilian_id if case_obj else '')
    if civ_id:
        if not context['civilian']:
            context['civilian'] = scoped_query(Civilian, community_id).filter_by(civilian_id=civ_id).first()
        context['warrants'] = scoped_query(Warrant, community_id).filter_by(civilian_id=civ_id).all()
        context['arrests'] = scoped_query(Arrest, community_id).filter_by(civilian_id=civ_id).order_by(Arrest.created_at.desc()).limit(5).all()
        context['traffic_stops'] = scoped_query(TrafficStop, community_id).filter(or_(TrafficStop.driver_name == (f"{context['civilian'].first_name} {context['civilian'].last_name}" if context.get('civilian') else ''), TrafficStop.plate == (context['vehicle'].plate if context.get('vehicle') else ''))).order_by(TrafficStop.created_at.desc()).limit(5).all()

    bolo_query = scoped_query(Bolo, community_id)
    if context['civilian']:
        full_name = f"{context['civilian'].first_name} {context['civilian'].last_name}".strip().lower()
        context['bolos'] = [b for b in bolo_query.filter_by(status='Active').limit(50).all() if full_name in (b.suspect_name or '').lower()]
    elif context['vehicle']:
        plate = (context['vehicle'].plate or '').lower()
        context['bolos'] = [b for b in bolo_query.filter_by(status='Active').limit(50).all() if plate and plate in ((b.vehicle or '').lower() + ' ' + (b.description or '').lower())]

    return context


def _serialize_incident_context(ctx):
    civ = ctx.get('civilian')
    veh = ctx.get('vehicle')
    case_obj = ctx.get('case')
    dispatch = ctx.get('dispatch_call')
    dispatch_payload = None
    if dispatch:
        dispatch_payload = {
            'call_id': dispatch.call_id,
            'caller_name': dispatch.caller_name,
            'location': dispatch.location,
            'call_type': getattr(dispatch, 'call_type', getattr(dispatch, 'incident_type', None)),
            'priority': dispatch.priority,
            'assigned_unit': dispatch.assigned_unit,
            'status': dispatch.status,
            'notes': getattr(dispatch, 'notes', getattr(dispatch, 'dispatch_notes', None)),
        }

    return {
        'case': {'case_id': case_obj.case_id, 'title': case_obj.title, 'incident_type': case_obj.case_type, 'location': case_obj.location, 'priority': case_obj.priority, 'notes': case_obj.report_notes} if case_obj else None,
        'dispatch_call': dispatch_payload,
        'civilian': {'civilian_id': civ.civilian_id, 'name': f"{civ.first_name} {civ.last_name}", 'dob': civ.date_of_birth.isoformat() if civ.date_of_birth else None, 'address': civ.address, 'driver_license_status': civ.driver_license_status, 'firearm_license_status': civ.firearm_license_status, 'business_license_status': civ.business_license_status, 'gang_affiliation': civ.gang_affiliation} if civ else None,
        'vehicle': {'vehicle_id': veh.vehicle_id, 'plate': veh.plate, 'make': veh.make, 'model': veh.model, 'color': veh.color, 'year': veh.year, 'registration_status': veh.registration_status, 'insurance_status': veh.insurance_status, 'owner_name': veh.owner_name} if veh else None,
        'charges': [{'charge_id': c.charge_id, 'charge': c.charge_name, 'penal_code': c.penal_code, 'severity': c.severity, 'counts': c.counts, 'recommended_fine': c.recommended_fine, 'recommended_jail_time': c.recommended_jail_time, 'source': 'existing'} for c in ctx.get('charges', [])],
        'warrants': [{'warrant_id': w.warrant_id, 'status': w.warrant_status, 'charges': w.warrant_charges, 'issuer': w.warrant_issuer} for w in ctx.get('warrants', [])],
        'bolos': [{'bolo_id': b.bolo_id, 'status': b.status, 'description': b.description, 'vehicle': b.vehicle} for b in ctx.get('bolos', [])],
        'evidence': [{'evidence_id': e.evidence_id, 'type': e.evidence_type, 'description': e.evidence_description, 'link': e.clip_link or e.screenshot_link, 'officer': e.officer, 'storage_status': e.storage_status} for e in ctx.get('evidence', [])],
        'arrests': [{'arrest_id': a.arrest_id, 'suspect_name': a.suspect_name, 'charges': a.charges, 'officer': a.arresting_officer, 'penalty': a.penalty, 'disposition': a.status} for a in ctx.get('arrests', [])],
        'traffic_stops': [{'stop_id': t.stop_id, 'location': t.location, 'reason': t.reason, 'officer': t.officer, 'disposition': t.outcome, 'notes': t.notes} for t in ctx.get('traffic_stops', [])],
        'related_cases': [{'case_id': c.case_id, 'title': c.title, 'status': c.status} for c in ctx.get('related_cases', [])],
        'missing_info': ctx.get('missing_info', []),
    }

@app.route('/api/cad/ai/incident-from-notes', methods=['POST'])
def cad_ai_incident_from_notes():
    payload = request.get_json(silent=True) or {}
    guard, err = _cad_ai_guard(case_id=payload.get('case_id'))
    if err:
        return err
    notes = (payload.get('officer_notes') or '').strip()
    if not notes:
        return jsonify({'success': False, 'error': 'officer_notes is required'}), 400

    context = _incident_context_from_inputs(payload, guard['community_id'])
    serialized = _serialize_incident_context(context)

    system_prompt = 'Officer notes are primary truth. Use linked CAD records only as support. Never invent charges, warrants, BOLOs, arrests, civilians, vehicles, or evidence. Flag conflicts and missing court details under missing_info. Do not decide guilt. Professional GTA RP law-enforcement tone. Do not claim to review clips/bodycam.'
    user_prompt = f"Build incident report JSON with keys: title, incident_type, location, priority, summary, narrative, timeline, probable_cause, involved_civilians, involved_officers, vehicles, charges, warrants, bolos, evidence, related_cases, arrests, traffic_stops, scene_control, officer_actions, suspect_actions, recommended_next_steps, missing_info, court_risk_notes. Mark non-existing charge ideas as source=suggested. Input notes: {notes}. Linked data: {json.dumps(serialized)}"

    ai_result = _ai_json_route('incident_from_notes', system_prompt, user_prompt, {'case_id': payload.get('case_id'), 'call_id': payload.get('call_id'), 'civilian_id': payload.get('civilian_id'), 'vehicle_id': payload.get('vehicle_id')})
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    out, cfg = ai_result

    report = out if isinstance(out, dict) else {}
    report.setdefault('missing_info', [])
    for mi in serialized.get('missing_info', []):
        if mi not in report['missing_info']:
            report['missing_info'].append(mi)

    return jsonify({'success': True, 'report': report, 'linked_context_counts': {
        'charges': len(serialized['charges']), 'warrants': len(serialized['warrants']), 'bolos': len(serialized['bolos']),
        'evidence': len(serialized['evidence']), 'arrests': len(serialized['arrests']), 'traffic_stops': len(serialized['traffic_stops'])
    }})


@app.route('/api/cad/ai/arrest-report', methods=['POST'])
def cad_ai_arrest_report():
    payload = request.get_json(silent=True) or {}
    guard, err = _cad_ai_guard(case_id=payload.get('case_id'))
    if err:
        return err
    ai_result = _ai_json_route(
        'arrest_report',
        _default_ai_system_rules("Include who/what/when/where/why/how when provided."),
        f"Return JSON keys: arrest_narrative, probable_cause, charges_summary, evidence_summary, jail_fine_explanation, miranda_checklist, transport_booking_notes, missing_info. Input: {json.dumps(payload)}",
        payload,
    )
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    return jsonify({'success': True, 'report': ai_result[0]})


@app.route('/api/cad/ai/use-of-force-report', methods=['POST'])
def cad_ai_uof_report():
    payload = request.get_json(silent=True) or {}
    guard, err = _cad_ai_guard(case_id=payload.get('case_id'))
    if err:
        return err
    ai_result = _ai_json_route(
        'use_of_force_report',
        _default_ai_system_rules("Do not justify force beyond supplied facts; flag weak documentation."),
        f"Return JSON keys: narrative, justification_summary, de_escalation_notes, medical_aid_notes, policy_court_risk_checklist, missing_info. Input: {json.dumps(payload)}",
        payload,
    )
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    return jsonify({'success': True, 'report': ai_result[0]})


@app.route('/api/cad/ai/court-summary', methods=['POST'])
def cad_ai_court_summary():
    payload = request.get_json(silent=True) or {}
    case_id = (payload.get('case_id') or '').strip()
    if not case_id:
        return jsonify({'success': False, 'error': 'case_id is required'}), 400
    guard, err = _cad_ai_guard(case_id=case_id)
    if err:
        return err
    case_obj = guard.get('case')
    if not case_obj:
        return jsonify({'success': False, 'error': 'Case not found'}), 404
    case_data = {'case_id': case_obj.case_id, 'title': case_obj.title, 'type': case_obj.case_type, 'location': case_obj.location, 'report_notes': case_obj.report_notes}
    ai_result = _ai_json_route(
        'court_summary',
        _default_ai_system_rules("Court packet summary is draft-only and must be reviewed by staff."),
        f"Return JSON keys: court_ready_summary, charges_table_summary, evidence_list, officer_narrative, witness_summary, arrest_warrant_summary, use_of_force_summary, prosecution_notes, defense_risk_notes, missing_documentation_checklist. Case input: {json.dumps(case_data)}",
        {'case_id': case_id},
    )
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    return jsonify({'success': True, 'summary': ai_result[0], 'review_required': True})


_EVIDENCE_AI_METADATA_KEYS = {
    # Parent-scope IDs are safe metadata and should survive the sanitizer.
    'case_id', 'evidence_id', 'attachment_id', 'parent_id', 'parent_type',
    'arrest_id', 'warrant_id', 'court_packet_id',
    'title', 'name', 'display_name', 'file_name', 'filename', 'original_filename',
    'evidence_type', 'type', 'attachment_type', 'source_type', 'content_type',
    'mime_type', 'size_bytes', 'file_size', 'description', 'evidence_description',
    'officer', 'uploaded_by', 'created_by', 'created_at', 'updated_at', 'upload_time', 'status',
    'storage_status', 'review_status', 'chain_of_custody', 'notes', 'officer_notes', 'tags',
}
_EVIDENCE_AI_URL_KEYS = {'external_url', 'clip_link', 'screenshot_link', 'download_url', 'url', 'link'}
_EVIDENCE_AI_NESTED_KEYS = {'evidence', 'attachments', 'evidence_attachments', 'items'}


def _evidence_ai_metadata_only(value):
    """Strip evidence AI input to metadata so files, paths, and raw URLs are never sent to the provider."""
    if isinstance(value, list):
        return [_evidence_ai_metadata_only(item) for item in value]
    if not isinstance(value, dict):
        return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)[:200]

    sanitized = {}
    for key, item in value.items():
        key_str = str(key)
        if key_str in _EVIDENCE_AI_METADATA_KEYS:
            sanitized[key_str] = _evidence_ai_metadata_only(item)
        elif key_str in _EVIDENCE_AI_URL_KEYS:
            sanitized[f'has_{key_str}'] = bool(item)
        elif key_str in _EVIDENCE_AI_NESTED_KEYS:
            sanitized[key_str] = _evidence_ai_metadata_only(item)
    return sanitized


@app.route('/api/cad/ai/evidence-summary', methods=['POST'])
def cad_ai_evidence_summary():
    payload = request.get_json(silent=True) or {}
    guard, err = _cad_ai_guard(case_id=payload.get('case_id'))
    if err:
        return err
    community_id = guard['community_id']
    attachment_query = scoped_query(EvidenceAttachment, community_id).filter(EvidenceAttachment.is_deleted.is_(False))
    for field in ('case_id', 'evidence_id', 'arrest_id', 'warrant_id', 'court_packet_id'):
        if payload.get(field):
            attachment_query = attachment_query.filter(getattr(EvidenceAttachment, field) == str(payload.get(field)).strip())
    attachments = []
    for attachment in attachment_query.order_by(EvidenceAttachment.created_at.desc()).limit(25).all():
        attachments.append({
            'attachment_id': attachment.attachment_id,
            'filename': attachment.original_filename,
            'file_type': attachment.file_type,
            'category': attachment.category,
            'description': attachment.description,
            'upload_time': attachment.created_at.isoformat() if attachment.created_at else None,
            'case_id': attachment.case_id,
            'evidence_id': attachment.evidence_id,
            'arrest_id': attachment.arrest_id,
            'warrant_id': attachment.warrant_id,
            'court_packet_id': attachment.court_packet_id,
            'mime_type': attachment.mime_type,
            'file_size': attachment.file_size,
            'review_status': attachment.review_status,
            'external_url': bool(attachment.external_url),
            'download_url': bool(_attachment_download_url(attachment)),
        })
    metadata_payload = {
        'officer_notes': payload.get('officer_notes') or payload.get('notes') or '',
        'case_id': payload.get('case_id'),
        'evidence_id': payload.get('evidence_id'),
        'arrest_id': payload.get('arrest_id'),
        'warrant_id': payload.get('warrant_id'),
        'court_packet_id': payload.get('court_packet_id'),
        'attachments': attachments,
    }
    safe_metadata_payload = _evidence_ai_metadata_only(metadata_payload)
    ai_result = _ai_json_route(
        "evidence_summary",
        _default_ai_system_rules(
            "Based on the attachment metadata and officer-entered descriptions only. "
            "Do not claim you viewed any image, video, PDF, download, link, or binary file contents."
        ),
        (
            "Return JSON keys: evidence_summary, chain_of_custody_narrative, "
            "relevance_to_charges, missing_evidence_checklist, review_notes. "
            "Start narrative wording with 'Based on the attachment metadata and officer-entered descriptions...' "
            f"Input metadata only: {json.dumps(safe_metadata_payload)}"
        ),
        {
            "case_id": payload.get("case_id"),
            "attachment_count": len(attachments),
        },
    )
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    return jsonify({'success': True, 'source': 'attachment_metadata_only', **ai_result[0]})


def _cad_traffic_ai(kind, payload):
    guard, err = _cad_ai_guard(case_id=payload.get('case_id'))
    if err:
        return err
    cfg = get_ai_config()
    if not cfg.get('configured'):
        return jsonify({'success': False, 'error': 'CAD AI is not configured.'}), 503
    schemas = {
        'traffic_citation': 'violation, citation_amount, court_required, court_date, notes, missing_info, review_required',
        'traffic_warning': 'warning_reason, warning_type, notes, missing_info, review_required',
        'traffic_arrest': 'charges, arrest_narrative, probable_cause, jail_recommendation, court_recommendation, missing_info, review_required',
    }
    ai_result = _ai_json_route(
        kind,
        _default_ai_system_rules('Traffic stop AI must preserve officer-entered facts, fill missing fields only, label suggestions review-only, and never create records.'),
        f"Return JSON keys: {schemas[kind]}. Preserve all supplied facts and use empty strings for unknowns. Input: {json.dumps(payload)}",
        {'traffic_stop_id': payload.get('traffic_stop_id'), 'outcome': payload.get('trafficOutcome') or payload.get('outcome')},
    )
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    out = ai_result[0]
    out['review_required'] = True
    return jsonify({'success': True, 'suggestions': out, **out})


@app.route('/api/cad/ai/traffic-citation', methods=['POST'])
def cad_ai_traffic_citation():
    return _cad_traffic_ai('traffic_citation', request.get_json(silent=True) or {})


@app.route('/api/cad/ai/traffic-warning', methods=['POST'])
def cad_ai_traffic_warning():
    return _cad_traffic_ai('traffic_warning', request.get_json(silent=True) or {})


@app.route('/api/cad/ai/traffic-arrest', methods=['POST'])
def cad_ai_traffic_arrest():
    return _cad_traffic_ai('traffic_arrest', request.get_json(silent=True) or {})

@app.route('/api/cad/ai/charge-suggestions', methods=['POST'])
def cad_ai_charge_suggestions():
    payload = request.get_json(silent=True) or {}
    guard, err = _cad_ai_guard(case_id=payload.get('case_id'))
    if err:
        return err
    ai_result = _ai_json_route(
        'charge_suggestions',
        _default_ai_system_rules("Suggestions only; never auto-apply charges."),
        f"Return JSON keys: suggestions, warnings. Each suggestion must include charge, penal_code, severity, reason, confidence, source='suggested'. Input: {json.dumps(payload)}",
        payload,
    )
    if not ai_result or not isinstance(ai_result[0], dict):
        return ai_result
    out = ai_result[0]
    return jsonify({'success': True, 'suggestions': out.get('suggestions', []), 'warnings': out.get('warnings', [])})
# For production with gunicorn + eventlet:
# gunicorn --worker-class eventlet -w 1 server:app --bind 0.0.0.0:$PORT


def _serialize_notification(n, recipient=None):
    return {
        'id': n.id,
        'community_id': n.community_id,
        'target_scope': n.target_scope,
        'target_role': n.target_role,
        'target_department': n.target_department,
        'target_user_id': n.target_user_id,
        'title': n.title,
        'message': n.message,
        'category': n.category,
        'priority': n.priority,
        'action_url': n.action_url,
        'created_at': n.created_at.isoformat() if n.created_at else None,
        'read': bool(recipient and recipient.read_at),
        'dismissed': bool(recipient and recipient.dismissed_at),
    }


def get_active_community_auth_context(user_id, community_id):
    context = {
        'is_platform_owner': False,
        'community_role': None,
        'department': None,
        'membership': None,
    }
    if not isinstance(user_id, int):
        return context
    user = User.query.get(user_id)
    context['is_platform_owner'] = bool(user and (getattr(user, 'role', None) == 'PlatformOwner' or getattr(user, 'platform_role', None) == 'PlatformOwner'))
    if not community_id:
        return context
    membership = CommunityMember.query.filter_by(user_id=user_id, community_id=community_id, status='Active').first()
    if membership:
        context['membership'] = membership
        context['community_role'] = normalize_community_role(getattr(membership, 'role', None))
        context['department'] = (getattr(membership, 'department', None) or '').strip() or None
    return context


def _notification_visibility_filter(user_id, community_id, auth_context):
    role = auth_context.get('community_role')
    department = auth_context.get('department')
    filters = [
        and_(Notification.target_scope == 'user', Notification.target_user_id == user_id),
    ]
    if community_id and auth_context.get('membership'):
        filters.append(and_(Notification.target_scope == 'community', Notification.community_id == community_id))
        if role:
            filters.append(and_(Notification.target_scope == 'role', Notification.community_id == community_id, Notification.target_role == role))
        if department:
            filters.append(and_(Notification.target_scope == 'department', Notification.community_id == community_id, Notification.target_department == department))
    if auth_context.get('is_platform_owner'):
        filters.append(and_(Notification.target_scope.in_(['platform_owner', 'global'])))
    return or_(*filters)


def _query_notifications_for_user(user_id, community_id, auth_context):
    q = Notification.query.filter(
        (Notification.expires_at.is_(None)) | (Notification.expires_at > datetime.utcnow())
    )
    return q.filter(_notification_visibility_filter(user_id, community_id, auth_context))





def _model_to_dict(row):
    if hasattr(row, 'to_dict'):
        return row.to_dict()
    data = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        data[column.name] = value
    return data


def _limit_param(default=100, maximum=500):
    try:
        return min(max(int(request.args.get('limit', default)), 1), maximum)
    except (TypeError, ValueError):
        return default


def _community_query(model, community_id):
    q = model.query
    if hasattr(model, 'community_id') and community_id:
        q = q.filter_by(community_id=community_id)
    return q


def _shared_collection(model, authz, default_limit=100):
    rows = _community_query(model, authz['community_id']).order_by(getattr(model, 'id').desc()).limit(_limit_param(default_limit)).all()
    return jsonify({'success': True, 'community_id': authz['community_id'], 'items': [_model_to_dict(row) for row in rows]})


def _active_departments_for_community(community_id):
    departments = []
    try:
        configured = Config.query.filter_by(key='departments').first()
        if configured and configured.value:
            parsed = json.loads(configured.value)
            if isinstance(parsed, list):
                departments.extend(parsed)
    except Exception:
        logger.debug('Department config parse skipped', exc_info=True)
    member_departments = [d[0] for d in db.session.query(CommunityMember.department).filter(
        CommunityMember.community_id == community_id,
        CommunityMember.status == 'Active',
        CommunityMember.department.isnot(None),
    ).distinct().all()]
    departments.extend(member_departments)
    normalized = []
    seen = set()
    for dept in departments or DEFAULT_COMMUNITY_DEPARTMENTS:
        name = dept.get('name') if isinstance(dept, dict) else str(dept)
        code = dept.get('id') if isinstance(dept, dict) else name
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        normalized.append({'id': code, 'name': name})
    return normalized


@app.route('/api/shared/config', methods=['GET'])
def shared_backend_config():
    """Public connection metadata for gtavcad.app and gtavcad.com clients."""
    return jsonify({
        'success': True,
        'api_base_url': os.environ.get('API_BASE_URL') or request.url_root.rstrip('/'),
        'socketio_path': '/socket.io',
        'allowed_origins': sorted(_allowed_web_origins()),
        'auth': {'session_cookie': True, 'bearer_token': True, 'token_type': 'Bearer'},
        'push': {'provider': os.environ.get('PUSH_PROVIDER', 'fcm'), 'configured': bool(os.environ.get('FCM_SERVER_KEY') or os.environ.get('FCM_SERVICE_ACCOUNT_JSON'))},
    })


@app.route('/api/auth/token', methods=['POST'])
@require_auth
def refresh_api_token():
    user_id = session.get('user_id')
    user = User.query.get(user_id) if isinstance(user_id, int) else None
    if not user:
        return jsonify({'success': False, 'error': 'Authentication required'}), 401
    return jsonify({
        'success': True,
        'api_token': issue_api_token(user, session.get('active_community_id') or session.get('selected_community_id')),
        'token_type': 'Bearer',
        'expires_in': JWT_MAX_AGE_SECONDS,
    })


@app.route('/api/users', methods=['GET'])
@require_auth
def api_users_index():
    authz, denied = _require_modules('community_admin', 'member_management', 'cad', 'dispatch')
    if denied:
        return denied
    members = CommunityMember.query.filter_by(community_id=authz['community_id']).all()
    user_ids = [m.user_id for m in members]
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return jsonify({'success': True, 'community_id': authz['community_id'], 'users': [{**users.get(m.user_id).to_dict(), 'community_role': m.role, 'department': m.department, 'callsign': m.callsign, 'membership_status': m.status} for m in members if users.get(m.user_id)]})


@app.route('/api/departments', methods=['GET'])
@require_auth
def api_departments_index():
    authz, denied = _require_modules('cad', 'dispatch', 'community_admin', 'member_management')
    if denied:
        return denied
    return jsonify({'success': True, 'community_id': authz['community_id'], 'departments': _active_departments_for_community(authz['community_id'])})


@app.route('/api/calls', methods=['GET'])
@require_auth
def api_calls_index():
    authz, denied = _require_modules('cad', 'dispatch', 'call_logs', 'report_911', 'civilian_portal')
    if denied:
        return denied
    return _shared_collection(DispatchCall, authz)


@app.route('/api/vehicles', methods=['GET'])
@require_auth
def api_vehicles_index():
    authz, denied = _require_modules('cad', 'dmv_lookup', 'dmv')
    if denied:
        return denied
    return _shared_collection(Vehicle, authz)


@app.route('/api/warrants', methods=['GET'])
@require_auth
def api_warrants_index():
    authz, denied = _require_modules('cad', 'police_records')
    if denied:
        return denied
    return _shared_collection(Warrant, authz)


@app.route('/api/units', methods=['GET'])
@require_auth
def api_units_index():
    authz, denied = _require_modules('cad', 'dispatch', 'unit_status')
    if denied:
        return denied
    return _shared_collection(OfficerSession, authz)


@app.route('/api/reports', methods=['GET'])
@require_auth
def api_reports_index():
    authz, denied = _require_modules('cad', 'police_records', 'reports')
    if denied:
        return denied
    incidents = _community_query(Incident, authz['community_id']).order_by(Incident.id.desc()).limit(_limit_param()).all()
    arrests = _community_query(Arrest, authz['community_id']).order_by(Arrest.id.desc()).limit(_limit_param()).all()
    citations = _community_query(Citation, authz['community_id']).order_by(Citation.id.desc()).limit(_limit_param()).all()
    uof = _community_query(UseOfForceReport, authz['community_id']).order_by(UseOfForceReport.id.desc()).limit(_limit_param()).all()
    return jsonify({'success': True, 'community_id': authz['community_id'], 'reports': {'incidents': [_model_to_dict(r) for r in incidents], 'arrests': [_model_to_dict(r) for r in arrests], 'citations': [_model_to_dict(r) for r in citations], 'use_of_force': [_model_to_dict(r) for r in uof]}})


@app.route('/api/push/register', methods=['POST'])
@require_auth
def register_push_token():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    provider = (data.get('provider') or os.environ.get('PUSH_PROVIDER') or 'fcm').strip().lower()
    if not token:
        return jsonify({'success': False, 'error': 'Push token is required'}), 400
    user_id = session.get('user_id')
    community_id = get_current_community_id()
    row = MobilePushToken.query.filter_by(user_id=user_id, provider=provider, token=token).first()
    if not row:
        row = MobilePushToken(user_id=user_id, community_id=community_id, provider=provider, token=token)
        db.session.add(row)
    row.community_id = community_id
    row.platform = (data.get('platform') or '').strip()[:32] or None
    row.device_name = (data.get('device_name') or data.get('deviceName') or '').strip()[:255] or None
    row.active = True
    row.last_seen_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'provider': provider, 'configured': bool(os.environ.get('FCM_SERVER_KEY') or os.environ.get('FCM_SERVICE_ACCOUNT_JSON'))})


@app.route('/api/push/unregister', methods=['POST'])
@require_auth
def unregister_push_token():
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    provider = (data.get('provider') or os.environ.get('PUSH_PROVIDER') or 'fcm').strip().lower()
    if not token:
        return jsonify({'success': False, 'error': 'Push token is required'}), 400
    MobilePushToken.query.filter_by(user_id=session.get('user_id'), provider=provider, token=token).update({'active': False, 'updated_at': datetime.utcnow()})
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/mobile/context', methods=['GET'])
@require_auth
def mobile_context():
    user_id = session.get('user_id')
    user = User.query.get(user_id) if isinstance(user_id, int) else None
    community_id = get_current_community_id()
    auth_context = get_active_community_auth_context(user_id, community_id)
    membership = auth_context.get('membership')
    community = Community.query.filter_by(community_id=community_id).first() if community_id else None
    community_slug = None
    if community:
        community_slug = getattr(community, 'slug', None) or getattr(community, 'community_slug', None)
    allowed_modules = _module_policy_for_auth_context(user, community, membership, auth_context)
    unread_count = 0
    notif_context = get_active_community_auth_context(user_id, community_id)
    rows = _query_notifications_for_user(user_id, community_id, notif_context).all() if isinstance(user_id, int) else []
    notif_ids = [n.id for n in rows]
    read_ids = set()
    if notif_ids and isinstance(user_id, int):
        read_ids = {r.notification_id for r in NotificationRecipient.query.filter(NotificationRecipient.user_id == user_id, NotificationRecipient.notification_id.in_(notif_ids), NotificationRecipient.read_at.isnot(None)).all()}
    unread_count = len([nid for nid in notif_ids if nid not in read_ids])
    return jsonify({
        'success': True,
        'user': {
            'id': user.id if user else None,
            'username': getattr(user, 'username', None),
            'display_name': getattr(user, 'username', None),
        },
        'active_community': {
            'id': community_id,
            'slug': community_slug,
            'name': getattr(community, 'name', None) or getattr(community, 'community_name', None) if community else None,
        },
        'community_role': auth_context.get('community_role'),
        'department': auth_context.get('department'),
        'allowed_modules': allowed_modules,
        'platform_owner': bool(auth_context.get('is_platform_owner')),
        'notification_count': unread_count,
        'branding': {
            'cad_name': getattr(community, 'cad_name', None) if community else None,
            'logo_url': '/assets/images/gtavcad-logo.png',
        },
    })

@app.route('/api/notifications', methods=['GET'])
@require_auth
def get_notifications():
    user_id = session.get('user_id')
    community_id = get_current_community_id()
    auth_context = get_active_community_auth_context(user_id, community_id)
    category = (request.args.get('category') or '').strip()
    unread_only = parse_bool(request.args.get('unread'), default=False)

    rows = _query_notifications_for_user(user_id, community_id, auth_context).order_by(Notification.created_at.desc()).limit(100).all()
    notif_ids = [n.id for n in rows]
    rec_map = {}
    if notif_ids:
        recipients = NotificationRecipient.query.filter(NotificationRecipient.user_id == user_id, NotificationRecipient.notification_id.in_(notif_ids)).all()
        rec_map = {r.notification_id: r for r in recipients}
    results = []
    for n in rows:
        item = _serialize_notification(n, rec_map.get(n.id))
        if category and item['category'].lower() != category.lower():
            continue
        if unread_only and item['read']:
            continue
        results.append(item)
    return jsonify({'success': True, 'notifications': results})


@app.route('/api/notifications/unread-count', methods=['GET'])
@require_auth
def notifications_unread_count():
    user_id = session.get('user_id')
    community_id = get_current_community_id()
    auth_context = get_active_community_auth_context(user_id, community_id)
    rows = _query_notifications_for_user(user_id, community_id, auth_context).all()
    notif_ids = [n.id for n in rows]
    read_ids = set()
    if notif_ids:
        read_ids = {r.notification_id for r in NotificationRecipient.query.filter(NotificationRecipient.user_id == user_id, NotificationRecipient.notification_id.in_(notif_ids), NotificationRecipient.read_at.isnot(None)).all()}
    unread = len([nid for nid in notif_ids if nid not in read_ids])
    return jsonify({'success': True, 'unread_count': unread})


@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
@require_auth
def mark_notification_read(notification_id):
    user_id = session.get('user_id')
    community_id = get_current_community_id()
    auth_context = get_active_community_auth_context(user_id, community_id)
    visible = _query_notifications_for_user(user_id, community_id, auth_context).filter(Notification.id == notification_id).first()
    if not visible:
        return jsonify({'success': False, 'error': 'Notification not found'}), 404
    rec = NotificationRecipient.query.filter_by(notification_id=notification_id, user_id=user_id).first()
    if not rec:
        rec = NotificationRecipient(notification_id=notification_id, user_id=user_id)
        db.session.add(rec)
    rec.read_at = datetime.utcnow()
    db.session.commit()
    socketio.emit('notification:read', {'notification_id': notification_id}, room=f'user:{user_id}')
    return jsonify({'success': True})


@app.route('/api/notifications/read-all', methods=['POST'])
@require_auth
def mark_all_notifications_read():
    user_id = session.get('user_id')
    community_id = get_current_community_id()
    auth_context = get_active_community_auth_context(user_id, community_id)
    now = datetime.utcnow()
    rows = _query_notifications_for_user(user_id, community_id, auth_context).all()
    for n in rows:
        rec = NotificationRecipient.query.filter_by(notification_id=n.id, user_id=user_id).first()
        if not rec:
            rec = NotificationRecipient(notification_id=n.id, user_id=user_id)
            db.session.add(rec)
        rec.read_at = now
    db.session.commit()
    socketio.emit('notification:count', {'unread_count': 0}, room=f'user:{user_id}')
    return jsonify({'success': True})

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"GTAVCAD server running on 0.0.0.0:{port}")
    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=False,
        allow_unsafe_werkzeug=True
    )
