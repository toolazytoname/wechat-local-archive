"""Article/webpage destinations, never attachment credentials or remote fetches."""
from urllib.parse import urlsplit


def safe_web_url(value):
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    # Do not normalize a dangerous or ambiguous value into an allowed one.
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        parsed.port  # validate malformed/out-of-range ports
        host = parsed.hostname
        if any(c in host for c in '<>"\'{}%'):
            return None
        host.encode('idna')
    except (ValueError, UnicodeError):
        return None
    return value
