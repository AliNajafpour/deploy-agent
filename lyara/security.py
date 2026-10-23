import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit


SECRET_KEY = re.compile(r'(password|secret|token|authorization|api.?key|credential|private.?key)', re.I)
# Scan each identifier or scheme once, without backtracking across parsed keys or whitespace.
SECRET_TEXT = re.compile(
    r'(?i)(bearer\s+)[^\s,;"\']+|'
    r'(?<![A-Za-z0-9_])((?>[A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)[A-Za-z0-9_]*)\s*+["\']?\s*+\]?\s*+[=:]\s*+["\']?)[^\s,;"\']+|'
    r'(?<![a-z0-9+.-])([a-z0-9+.-]+://[^\s/:]+:)[^\s@]+(?=@)|'
    r'\b(?:sk-|sk-ant-)[A-Za-z0-9_-]{8,}'
)


def redact(value, secrets=()):
    if isinstance(value, dict):
        env_entry = 'key' in value and 'value' in value
        result = {}
        for key, item in value.items():
            if key in {'input_tokens','output_tokens','total_tokens','cached_tokens','reasoning_tokens'} and (item is None or type(item) is int and item>=0):
                result[key]=item
            elif key == 'env_refs' and isinstance(item, dict):
                result[key] = {str(k): v if isinstance(v, str) and re.fullmatch(r'LYARA_DEPLOY_[A-Za-z0-9_]+', v)
                               else '[REDACTED]' for k, v in item.items()}
            elif key == 'env_fingerprints' and isinstance(item, dict):
                result[key] = {str(k): v if isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v)
                               else '[REDACTED]' for k, v in item.items()}
            else:
                result[str(key)] = ('[REDACTED]' if SECRET_KEY.search(str(key)) or (env_entry and key == 'value')
                                    else redact(item, secrets))
        return result
    if isinstance(value, (tuple, list)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = (value.replace(secret, '[REDACTED]') if len(secret)>=8 else
                         re.sub(r'(?<![A-Za-z0-9_])'+re.escape(secret)+r'(?![A-Za-z0-9_])','[REDACTED]',value))
        return SECRET_TEXT.sub(lambda m: (next((g for g in m.groups() if g), '') + '[REDACTED]'), value)
    return value


def workspace_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_dir():
        raise ValueError('مسیر پروژه باید یک پوشه داخل فضای کاری مجاز باشد.')
    return path


def public_https(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError('بررسی سلامت فقط برای آدرس عمومی HTTPS مجاز است.')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('آدرس سلامت نباید به شبکهٔ خصوصی یا محلی اشاره کند.')
    return {'url': url, 'hostname': parsed.hostname, 'ip': addresses[0][4][0]}
