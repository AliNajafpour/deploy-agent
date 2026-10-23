import fnmatch
import hashlib
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path


EXCLUDED_DIRS = {'.git', '.hg', '.svn', '.venv', 'venv', 'env', 'node_modules',
                 '__pycache__', '.lyara', '.cache', '.next', '.nuxt', '.codex', '.claude', '.aws'}
SENSITIVE = re.compile(r'(^\.env(?:\.|$)|secret|credential|^id_(rsa|ed25519)|\.(pem|key|p12|pfx)$|^\.npmrc$|^\.netrc$)', re.I)


def read_json(path):
    if not path.exists():
        return {}
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('فایل پیکربندی بیش از حد بزرگ است.')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('پیکربندی پروژه باید یک شیء JSON باشد.')
    return data


def inspect_project(root):
    root = Path(root)
    config = read_json(root / 'liara.json')
    package = read_json(root / 'package.json')
    platform = config.get('platform', '')
    hints, questions = [], []
    if not platform:
        if (root / 'Dockerfile').is_file():
            platform = 'docker'
        elif package:
            platform = 'node'
            if 'next' in package.get('dependencies', {}):
                platform = 'next'
        elif (root / 'manage.py').is_file():
            platform = 'django'
        elif (root / 'requirements.txt').is_file() or (root / 'pyproject.toml').is_file():
            platform = 'python'
        elif (root / 'index.html').is_file():
            platform = 'static'
    port = config.get('port')
    if port is not None and (type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('پورت liara.json معتبر نیست.')
    if (root / 'Dockerfile').is_file() and port is None:
        match = re.search(r'^\s*EXPOSE\s+(\d+)', (root / 'Dockerfile').read_text(encoding='utf-8'), re.M)
        if match:
            port = int(match[1])
    if package and not package.get('scripts', {}).get('start'):
        hints.append('اسکریپت start در package.json تعریف نشده؛ فرمان شروع را بررسی کنید.')
    if not platform:
        questions.append('پلتفرم پروژه چیست؟ Docker، Node، Python یا استاتیک؟')
    env_names = []
    if (root / '.env.example').is_file():
        env_names = re.findall(r'^([A-Za-z_][A-Za-z0-9_]*)=', (root / '.env.example').read_text(encoding='utf-8'), re.M)
    return {'platform': platform, 'port': port, 'project': config.get('app', ''),
            'start': package.get('scripts', {}).get('start'), 'required_env': env_names,
            'hints': hints, 'questions': questions}


def snapshot(root, artifact_dir, excluded_roots=(), file_modes=None):
    root, artifact_dir = Path(root).resolve(), Path(artifact_dir).resolve()
    excluded_roots = [Path(path).resolve() for path in (*excluded_roots, artifact_dir)]
    def excluded(path):
        return any(path.resolve().is_relative_to(private) for private in excluded_roots)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    patterns = []
    if (root / '.liaraignore').is_file():
        patterns = [line.strip() for line in (root / '.liaraignore').read_text(encoding='utf-8').splitlines()
                    if line.strip() and not line.startswith('#')]
        if any(line.startswith('!') for line in patterns):
            raise ValueError('الگوی ! در .liaraignore پشتیبانی نمی‌شود؛ فهرست صریح فایل‌های مستثنا را وارد کنید.')
    entries, total = [], 0
    with tempfile.NamedTemporaryFile(dir=artifact_dir, suffix='.tar.gz', delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, 'w:gz') as archive:
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_DIRS
                                 and not name.startswith('.venv') and not (Path(directory) / name).is_symlink()
                                 and not excluded(Path(directory) / name))
                for name in sorted(files):
                    path = Path(directory) / name
                    relative = path.relative_to(root).as_posix()
                    if (path.is_symlink() or not path.is_file() or SENSITIVE.search(name)
                            or excluded(path)):
                        continue
                    # ponytail: .liaraignore supports glob exclusions only; use gitwildmatch if negation is needed.
                    if any(fnmatch.fnmatch(relative, p.rstrip('/')) or fnmatch.fnmatch(relative, p.rstrip('/') + '/*')
                           or fnmatch.fnmatch(name, p) for p in patterns):
                        continue
                    size = path.stat().st_size
                    total += size
                    if size > 25 * 1024**2 or total > 250 * 1024**2 or len(entries) >= 20000:
                        raise ValueError('بستهٔ پروژه بیش از حد بزرگ است؛ فایل‌های غیرضروری را در .liaraignore قرار دهید.')
                    if size < 1024**2 and path.suffix.lower() not in {'.png', '.jpg', '.webp', '.woff', '.woff2'}:
                        raw = path.read_bytes()
                        if b'PRIVATE KEY-----' in raw or re.search(rb'\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}', raw):
                            raise ValueError('فایل مشکوک به کلید محرمانه در بسته وجود دارد: ' + relative)
                    def preserve_mode(info):
                        if file_modes and relative in file_modes:
                            info.mode = file_modes[relative] & 0o777
                        return info
                    archive.add(path, arcname=relative, recursive=False, filter=preserve_mode)
                    entries.append(relative)
        if not entries:
            raise ValueError('بستهٔ پروژه خالی است؛ مسیر یا الگوهای حذف را بررسی کنید.')
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        destination = artifact_dir / (digest + '.tar.gz')
        temporary.replace(destination)
        return {'path': str(destination), 'sha256': digest, 'files': entries, 'bytes': total,
                'compressed_bytes': destination.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)


def list_sources(workspace, data_dir):
    root, private = Path(workspace).resolve(), Path(data_dir).resolve()
    markers = {'package.json', 'liara.json', 'Dockerfile', 'requirements.txt',
               'pyproject.toml', 'index.html', 'app.py', 'main.py', 'server.mjs', 'server.js'}
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        path = Path(directory)
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED_DIRS
            and not name.startswith('.') and not SENSITIVE.search(name)
            and not (path/name).is_symlink() and not (path/name).resolve().is_relative_to(private))
        if len(path.relative_to(root).parts) >= 2:
            dirs[:] = []
        if path.resolve().is_relative_to(private):
            dirs[:] = []
            continue
        if markers.intersection(files):
            result.append({'source': path.relative_to(root).as_posix(), 'label': path.name})
        if len(result) >= 80:
            break
    return result
