"""Bounded one-line diagnostics. Never dump headers, prompts, responses or exceptions."""
import json
import re
import unicodedata
from urllib.parse import quote


def safe_text(value, secrets=(), limit=240):
    """JSON-quote scalars after redaction, control escaping and truncation.

    URLs are removed entirely: a PT passkey can occur in a path, not only a query.
    Arbitrary unlabeled personal data in a normal title cannot be detected; title
    details are therefore DEBUG-only, not advertised as anonymized information.
    """
    if value is None or type(value) in (bool, int, float):
        return json.dumps(value)
    if not isinstance(value, str):
        return '"[unsupported]"'
    text = value
    for secret in sorted((s for s in secrets if isinstance(s, str) and s), key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]').replace(quote(secret, safe=''), '[REDACTED]')
    text = re.sub(r'(?i)\b(?:https?|ftp)://[^\s<>\[\]"\']+|\bmagnet:\?[^\s<>]+', '[URL]', text)
    text = re.sub(r'(?i)\b(?:authorization|cookie)\s*:\s*[^\r\n]*', '[REDACTED]', text)
    text = re.sub(r'(?i)\b(?:api[_-]?key|passkey|token|authorization|cookie|password)\s*[:=]\s*'
                  r'(?:Bearer\s+)?[^\s,;\]\[{}"\']+', '[REDACTED]', text)
    text = re.sub(r'(?i)\bBearer\s+[^\s,;\]\[{}"\']+|\bsk-[A-Za-z0-9_-]{6,}', '[REDACTED]', text)
    text = ''.join(' ' if unicodedata.category(c).startswith('C') or c in '\u2028\u2029' else c
                   for c in text)
    if len(text) > limit:
        text = text[:limit] + '…[truncated]'
    return json.dumps(text, ensure_ascii=False)
