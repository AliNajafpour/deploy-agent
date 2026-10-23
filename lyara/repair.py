"""Stage bounded deployment repairs. Never run project code or package installers here."""
import ast
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

from .project import snapshot
from .security import redact


DEPLOYMENT_NAMES = {'liara.json', 'Dockerfile', 'Procfile', 'requirements.txt',
    'package.json', 'package-lock.json', 'pyproject.toml', 'runtime.txt', '.python-version',
    '.nvmrc', 'app.py', 'main.py', 'server.py', 'wsgi.py', 'asgi.py', 'gunicorn.conf.py',
    'server.js', 'server.cjs', 'server.mjs', 'app.js', 'app.cjs', 'app.mjs'}
MAX_FILE = 24000
MAX_TOTAL = 60000


def allowed_path(name):
    if not isinstance(name, str) or '\\' in name or ':' in name:
        return False
    path = PurePosixPath(name)
    return (str(path) == name and not path.is_absolute() and '..' not in path.parts
            and len(path.parts) in (1, 2) and path.name in DEPLOYMENT_NAMES
            and (len(path.parts) == 1 or path.parts[0] in {'backend', 'server'}))


def deployment_files(artifact, secrets=()):
    files, omitted, total = {}, [], 0
    with tarfile.open(artifact['path']) as archive:
        for member in archive.getmembers():
            if not allowed_path(member.name) or not member.isfile():
                continue
            if member.size > MAX_FILE or total + member.size > MAX_TOTAL:
                omitted.append(member.name)
                continue
            raw = archive.extractfile(member).read()
            try:
                content = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                omitted.append(member.name)
                continue
            if redact(content, secrets) != content:
                omitted.append(member.name)
                continue
            files[member.name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'content': content}
            total += member.size
    return files, omitted


def validate_edits(edits, files):
    if not isinstance(edits, list) or not 1 <= len(edits) <= 8:
        raise ValueError('اصلاح انتشار باید شامل یک تا هشت فایل مشخص باشد.')
    names, total = set(), 0
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'path', 'before_sha256', 'content'}:
            raise ValueError('هر فایل اصلاحی به path، before_sha256 و content نیاز دارد.')
        name, content = edit['path'], edit['content']
        if not allowed_path(name) or name.casefold() in names:
            raise ValueError('مسیر فایل اصلاحی خارج از محدودهٔ انتشار یا تکراری است.')
        if PurePosixPath(name).name == 'package-lock.json':
            raise ValueError('اصلاح خودکار lockfile پشتیبانی نمی‌شود؛ آن را با ابزار مدیریت پکیج بازتولید کنید.')
        names.add(name.casefold())
        if not isinstance(content, str) or not content.strip() or len(content.encode('utf8')) > MAX_FILE:
            raise ValueError('محتوای فایل اصلاحی خالی یا بیش از حد بزرگ است.')
        total += len(content.encode('utf8'))
        if total > MAX_TOTAL or '[REDACTED]' in content or redact(content) != content:
            raise ValueError('فایل اصلاحی محرمانه یا بیش از ظرفیت مجاز است.')
        previous = files.get(name)
        if edit['before_sha256'] != (previous['sha256'] if previous else None):
            raise ValueError('هش فایل اصلاحی با نسخهٔ بررسی‌شده تطابق ندارد.')
        if previous and previous['content'].replace('\r\n', '\n') == content.replace('\r\n', '\n'):
            raise ValueError('اصلاح پیشنهادی تغییری در فایل ایجاد نمی‌کند.')


def check_candidate(root, names):
    checks = []
    node = shutil.which('node')
    # Only syntax is checked; imports, npm hooks and project scripts never run on the host.
    environment = {k: v for k, v in os.environ.items()
                   if k.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'SYSTEMDRIVE'}}
    for name in names:
        path = root / name
        text = path.read_text(encoding='utf-8-sig')
        try:
            if path.suffix == '.py':
                ast.parse(text, filename=name)
                kind = 'Python syntax'
            elif path.suffix in {'.js', '.mjs', '.cjs'}:
                if not node:
                    raise ValueError('برای بررسی نحو فایل Node، نصب محلی Node.js لازم است.')
                result = subprocess.run([node, '--check', str(path)], cwd=root, env=environment,
                    capture_output=True, timeout=15, shell=False)
                if result.returncode:
                    raise ValueError('نحو فایل Node معتبر نیست: ' + name)
                kind = 'Node syntax'
            elif path.suffix == '.json':
                if not isinstance(json.loads(text), dict):
                    raise ValueError('فایل JSON باید یک شیء باشد: ' + name)
                kind = 'JSON structure'
            elif path.name == 'pyproject.toml':
                tomllib.loads(text)
                kind = 'TOML structure'
            else:
                kind = 'UTF-8 bounded text, runtime validation deferred to build'
        except (SyntaxError, ValueError) as error:
            raise ValueError('بررسی ساختاری فایل اصلاحی ناموفق بود: ' + name) from error
        checks.append({'path': name, 'check': kind, 'passed': True})
    return checks


def stage_repair(artifact, edits, files, artifact_dir, secrets=()):
    validate_edits(edits, files)
    if any(redact(edit['content'], secrets) != edit['content'] for edit in edits):
        raise ValueError('محتوای محرمانه در فایل اصلاحی مجاز نیست.')
    source = Path(artifact['path'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != artifact['sha256']:
        raise ValueError('بستهٔ پایه پس از بررسی تغییر کرده است.')
    diff, changes, modes = [], [], {}
    with tempfile.TemporaryDirectory(prefix='lyara-repair-') as directory:
        root = Path(directory)
        with tarfile.open(source) as archive:
            seen = set()
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if (not member.isfile() or path.is_absolute() or '..' in path.parts or ':' in member.name
                        or '\\' in member.name or member.name.casefold() in seen):
                    raise ValueError('بستهٔ پایه مسیر نامعتبر یا فایل پیوندی دارد.')
                seen.add(member.name.casefold())
                modes[member.name] = member.mode
                target = root.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        for edit in edits:
            name, content = edit['path'], edit['content']
            path = root / name
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if actual != edit['before_sha256']:
                raise ValueError('فایل پنهان یا بررسی‌نشده قابل بازنویسی نیست: ' + name)
            old = files.get(name, {}).get('content', '')
            diff.extend(difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True),
                        fromfile='before/' + name, tofile='after/' + name))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode('utf8'))
            changes.append({'path': name, 'before_sha256': actual,
                            'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        for edit in edits:
            path = root / edit['path']
            if path.name == 'package.json':
                package = json.loads(path.read_text(encoding='utf8'))
                try:
                    old = json.loads(files.get(edit['path'], {}).get('content', '{}'))
                except ValueError:
                    old = {}
                lock = path.with_name('package-lock.json')
                keys = ['dependencies', 'devDependencies', 'optionalDependencies']
                changed = any(package.get(key, {}) != old.get(key, {}) for key in keys)
                other_locks = [path.with_name(n) for n in ['yarn.lock', 'pnpm-lock.yaml', 'npm-shrinkwrap.json']]
                if changed and any(p.exists() for p in other_locks):
                    raise ValueError('تغییر وابستگی با این lockfile پشتیبانی نمی‌شود؛ فایل قفل را با ابزار پروژه آماده کنید.')
                if changed and lock.exists():
                    raise ValueError('افزودن وابستگی در پروژهٔ قفل‌شده به بازتولید معتبر lockfile نیاز دارد؛ اصلاح خودکار آن فعلاً پشتیبانی نمی‌شود.')
            if path.name == 'pyproject.toml' and any(path.with_name(n).exists() for n in ['uv.lock', 'poetry.lock']):
                raise ValueError('اصلاح pyproject همراه lockfile به بازتولید فایل قفل با ابزار پروژه نیاز دارد.')
        checks = check_candidate(root, [edit['path'] for edit in edits])
        candidate = snapshot(root, artifact_dir, file_modes=modes)
        with tarfile.open(candidate['path']) as archive:
            for change in changes:
                try:
                    raw = archive.extractfile(change['path']).read()
                except KeyError as error:
                    raise ValueError('فایل اصلاحی به دلیل ignore در بسته نیست: ' + change['path']) from error
                if hashlib.sha256(raw).hexdigest() != change['after_sha256']:
                    raise ValueError('فایل اصلاحی در بسته با diff مطابقت ندارد.')
    return candidate, {'base_sha256': artifact['sha256'], 'changes': changes, 'diff': ''.join(diff),
        'checks': checks, 'dependency_note': 'Dependencies are installed by the deployment build from the approved manifest. No host installation occurs.',
        'limits': ['Structural checks do not prove runtime health.',
                   'Repairs affect the staged deployment only; original workspace files are preserved.',
                   'Backend entrypoints and deployment files only; no UI or product feature changes.']}
