"""Portable configuration; no host credential files or proxy-specific imports."""
import os
from urllib.parse import urlsplit


def public_base_url():
    value = os.environ.get('PUBLIC_BASE_URL', 'http://127.0.0.1:18136').strip().rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
            or (parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'))):
        raise ValueError('PUBLIC_BASE_URL must be an HTTPS origin, or HTTP loopback origin')
    # Also reject malformed ports.
    parsed.port
    return value


def notifications_enabled():
    value = os.environ.get('NOTIFICATIONS_ENABLED', 'false').strip().lower()
    if value not in ('true', 'false'):
        raise ValueError('NOTIFICATIONS_ENABLED must be true or false')
    return value == 'true'
