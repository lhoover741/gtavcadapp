import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1/chat/completions'
DEFAULT_OPENROUTER_MODEL = 'openai/gpt-4o-mini'


def _env_first(*names):
    for name in names:
        val = os.getenv(name)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ''


def get_ai_config():
    enabled_raw = (os.getenv('AI_ENABLED', 'true') or 'true').strip().lower()
    enabled = enabled_raw in ('1', 'true', 'yes', 'on')
    api_key = _env_first('OPENROUTER_API_KEY', 'OPEN_ROUTER_API_KEY', 'AI_OPENROUTER_API_KEY')
    model = _env_first('OPENROUTER_MODEL', 'OPEN_ROUTER_MODEL', 'AI_OPENROUTER_MODEL') or DEFAULT_OPENROUTER_MODEL
    return {
        'enabled': enabled,
        'provider': 'OpenRouter',
        'model': model,
        'configured': bool(api_key) and enabled,
        'has_api_key': bool(api_key),
        'api_key': api_key,
    }


def ai_runtime_or_error():
    cfg = get_ai_config()
    if not cfg['enabled'] or not cfg['api_key']:
        return None, 'AI assistant is not configured by the platform owner'
    return cfg, None


def chat_json(system_prompt, user_prompt, max_tokens=900, temperature=0.3, timeout=25):
    cfg, err = ai_runtime_or_error()
    if err:
        return None, err, None

    payload = json.dumps({
        'model': cfg['model'],
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'max_tokens': max_tokens,
        'temperature': temperature,
        'response_format': {'type': 'json_object'},
    }).encode('utf-8')

    req = urllib.request.Request(
        OPENROUTER_BASE_URL,
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f"Bearer {cfg['api_key']}",
            'X-Title': 'GTAVCAD Police CAD',
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            raw = result.get('choices', [{}])[0].get('message', {}).get('content', '{}')
            usage = result.get('usage', {})
            return json.loads(raw), None, usage
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning('AI provider request failed: %s', type(e).__name__)
        return None, 'AI provider request failed', None
