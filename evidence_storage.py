import mimetypes
import os
import re
import secrets
from pathlib import Path
from werkzeug.utils import secure_filename

LINK_ONLY_DISABLED_MESSAGE = 'Direct uploads are not configured. Attach an external evidence link instead.'

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm'}
DOCUMENT_EXTENSIONS = {'.pdf', '.txt'}
ZIP_EXTENSIONS = {'.zip'}
DANGEROUS_EXTENSIONS = {'.exe', '.bat', '.cmd', '.sh', '.js', '.html', '.htm', '.php', '.py', '.jar', '.msi', '.scr'}

CATEGORY_BY_EXTENSION = {
    **{ext: 'image' for ext in IMAGE_EXTENSIONS},
    **{ext: 'video' for ext in VIDEO_EXTENSIONS},
    **{ext: 'document' for ext in DOCUMENT_EXTENSIONS},
    '.zip': 'archive',
}

MIME_PREFIXES = {
    'image': ('image/',),
    'video': ('video/',),
}

MIME_EXACT = {
    'document': {'application/pdf', 'text/plain'},
    'archive': {'application/zip', 'application/x-zip-compressed'},
}


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def get_storage_config():
    mode = (os.getenv('EVIDENCE_STORAGE_MODE') or 'link_only').strip().lower()
    if mode not in {'link_only', 'local_volume'}:
        mode = 'link_only'
    root = (os.getenv('EVIDENCE_STORAGE_ROOT') or '').strip()
    if mode == 'local_volume' and not root:
        return {
            'mode': 'link_only',
            'root': None,
            'direct_uploads_enabled': False,
            'disabled_message': LINK_ONLY_DISABLED_MESSAGE,
        }
    return {
        'mode': mode,
        'root': root or None,
        'direct_uploads_enabled': mode == 'local_volume' and bool(root),
        'disabled_message': LINK_ONLY_DISABLED_MESSAGE,
    }


def max_bytes_for_category(category):
    mb = {
        'image': _env_int('EVIDENCE_MAX_IMAGE_MB', 10),
        'document': _env_int('EVIDENCE_MAX_DOCUMENT_MB', 15),
        'archive': _env_int('EVIDENCE_MAX_DOCUMENT_MB', 15),
        'video': _env_int('EVIDENCE_MAX_VIDEO_MB', 100),
    }.get(category, _env_int('EVIDENCE_MAX_DOCUMENT_MB', 15))
    return max(1, mb) * 1024 * 1024


def sanitize_original_filename(filename):
    base = secure_filename(os.path.basename(filename or ''))
    base = re.sub(r'_{2,}', '_', base).strip('._')
    return base or None


def allowed_extensions():
    extensions = set(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS)
    if _env_bool('EVIDENCE_ALLOW_ZIP', False):
        extensions |= ZIP_EXTENSIONS
    return extensions


def validate_upload(file_storage):
    original = sanitize_original_filename(getattr(file_storage, 'filename', None))
    if not original:
        return None, 'File must have a safe filename with an allowed extension'
    ext = Path(original).suffix.lower()
    if not ext or ext in DANGEROUS_EXTENSIONS or ext not in allowed_extensions():
        return None, 'File type is not allowed for evidence uploads'
    category = CATEGORY_BY_EXTENSION.get(ext)
    if not category:
        return None, 'File type is not allowed for evidence uploads'

    stream = file_storage.stream
    position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(position)
    if size <= 0:
        return None, 'Uploaded file is empty'
    if size > max_bytes_for_category(category):
        return None, f'Uploaded {category} exceeds the configured size limit'

    supplied_mime = (getattr(file_storage, 'mimetype', None) or '').lower().split(';', 1)[0]
    guessed_mime = (mimetypes.guess_type(original)[0] or '').lower()
    mime_to_store = supplied_mime or guessed_mime or 'application/octet-stream'
    mime_candidates = {m for m in (supplied_mime, guessed_mime) if m}
    if category in MIME_PREFIXES and mime_candidates:
        if not any(any(m.startswith(prefix) for prefix in MIME_PREFIXES[category]) for m in mime_candidates):
            return None, 'Uploaded file MIME type does not match its extension'
    if category in MIME_EXACT and mime_candidates:
        if not any(m in MIME_EXACT[category] for m in mime_candidates):
            return None, 'Uploaded file MIME type does not match its extension'

    stored_leaf = f'{secrets.token_hex(12)}_{original}'
    return {
        'original_filename': original,
        'stored_leaf': stored_leaf,
        'extension': ext,
        'file_type': category,
        'mime_type': mime_to_store,
        'file_size': size,
    }, None


def relative_storage_path(community_id, parent_type, parent_id, attachment_id, stored_leaf):
    safe_community = secure_filename(str(community_id))
    safe_parent_type = secure_filename(str(parent_type or 'attachment')) or 'attachment'
    safe_parent_id = secure_filename(str(parent_id or 'unlinked')) or 'unlinked'
    safe_attachment = secure_filename(str(attachment_id))
    safe_leaf = secure_filename(stored_leaf)
    return str(Path('evidence') / safe_community / safe_parent_type / safe_parent_id / f'{safe_attachment}_{safe_leaf}')


def resolve_local_path(relative_path):
    cfg = get_storage_config()
    root = cfg.get('root')
    if not root:
        return None, 'Evidence storage root is not configured'
    root_path = Path(root).expanduser().resolve()
    candidate = (root_path / (relative_path or '')).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError:
        return None, 'Invalid evidence storage path'
    return candidate, None


def save_local_file(file_storage, relative_path):
    destination, error = resolve_local_path(relative_path)
    if error:
        return error
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_storage.stream.seek(0)
    file_storage.save(destination)
    return None
