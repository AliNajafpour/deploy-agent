"""Offline operator checks. Never initializes the assistant or changes its files."""
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values

from .models import HostingOption


def check_readiness(env_file=None, environ=None, production=False):
    """Return static, secret-free diagnostics without requests or filesystem writes.

    An explicit environ mapping replaces the process environment for this check.
    Environment values override the dotenv file, matching runtime initialization.
    """
    env = dict(os.environ if environ is None else environ)
    checks = []

    def add(name, status, label, detail):
        checks.append({'id': name, 'status': status, 'label': label, 'detail': detail})

    default = Path(__file__).resolve().parents[1] / '.env'
    if not default.is_file():
        default = Path.cwd() / '.env'
    try:
        selected = Path(env_file or env.get('LYARA_ENV_FILE', str(default))).resolve()
        if selected.is_file():
            # Operator secrets must not interpolate other process variables.
            values = dotenv_values(selected, interpolate=False)
            env = {**{key: value for key, value in values.items() if value is not None}, **env}
            base = selected.parent
            add('environment', 'pass', 'Environment', 'Loaded configuration without interpolation; process values take precedence.')
        else:
            base = Path.cwd()
            explicit = env_file is not None or bool(env.get('LYARA_ENV_FILE'))
            add('environment', 'error' if explicit else 'pass', 'Environment',
                'The selected env file does not exist.' if explicit else 'Using process environment configuration.')
    except (OSError, ValueError, TypeError, UnicodeError):
        base = Path.cwd()
        add('environment', 'error', 'Environment', 'Cannot read the selected configuration file.')

    add('python', 'pass' if sys.version_info >= (3, 11) else 'error', 'Python', 'Python 3.11 or newer is required.')
    search_path = env.get('PATH', os.defpath)
    node = shutil.which('node', path=search_path)
    add('node', 'pass' if node else 'warning', 'JavaScript repair',
        'Node is available for syntax checks.' if node else 'Node is missing; JavaScript repair syntax checks will be unavailable.')

    def credential(name):
        value = env.get(name, '')
        return (isinstance(value, str) and bool(value.strip()) and not re.search(
            r'\s|\$\{|^(?:your[_ -]|replace[_ -]|change[_ -]?me|<)', value, re.I))

    provider = env.get('LYARA_PROVIDER', 'api').lower()
    add('provider', 'pass' if provider in {'api', 'codex', 'claude'} else 'error', 'Model provider',
        'Choose api, codex or claude with LYARA_PROVIDER.')
    if provider == 'api':
        add('model_key', 'pass' if credential('LYARA_LLM_API_KEY') else 'error', 'Model API key',
            'LYARA_LLM_API_KEY must contain a key, without whitespace or unresolved placeholders. Account access is not tested.')
        try:
            url = env.get('LYARA_LLM_BASE_URL') or 'https://ai.boxapi.ir/ai/v1'
            parsed = urlsplit(url)
            valid = (not re.search(r'\s', url) and parsed.hostname and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment and parsed.port != 0
                     and (parsed.scheme == 'https' or
                          (parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'})))
        except (ValueError, TypeError):
            valid = False
        add('model_url', 'pass' if valid else 'error', 'Model API address',
            'Use HTTPS, or loopback HTTP, without credentials, query parameters or fragments. No connection is attempted.')
        model = env.get('LYARA_MODEL') or 'openai/gpt-6-luna'
        add('model', 'pass' if model.strip() and not re.search(r'[\r\n]|\$\{', model) else 'error', 'Model name',
            'A model name is configured; availability and permission must be checked separately.')
    elif provider in {'codex', 'claude'}:
        command = env.get('LYARA_' + provider.upper() + '_BIN') or shutil.which(provider, path=search_path)
        found = bool(command and Path(command).is_file())
        if found and os.name == 'nt' and Path(command).suffix.lower() in {'.cmd', '.ps1', '.bat'}:
            package = Path(command).parent / 'node_modules'
            found = bool(node and (package / '@openai/codex/bin/codex.js').is_file()) if provider == 'codex' else (
                package / '@anthropic-ai/claude-code/bin/claude.exe').is_file()
        add('provider_command', 'pass' if found else 'error', 'Model command',
            'The provider executable must be installed or selected with its LYARA_*_BIN setting. Login is not tested.')

    add('liara_token', 'pass' if credential('LIARA_API_TOKEN') else 'error', 'Liara API token',
        'LIARA_API_TOKEN must contain a token, without whitespace or unresolved placeholders. Account access is not tested.')
    token = env.get('LYARA_ACCESS_TOKEN', '')
    strong = credential('LYARA_ACCESS_TOKEN') and len(token) >= 32 and len(set(token)) >= 8
    add('access_token', 'pass' if strong else ('error' if production else 'warning'), 'Web access token',
        'Use a randomly generated LYARA_ACCESS_TOKEN of at least 32 characters for public hosting. Randomness cannot be verified offline.')
    hostname = env.get('LYARA_PUBLIC_HOST', '')
    try:
        host = hostname.encode('idna').decode('ascii')
        valid_host = len(host) <= 253 and '.' in host and all(
            re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part) for part in host.split('.'))
        try:
            ipaddress.ip_address(host)
            valid_host = False
        except ValueError:
            pass
    except (UnicodeError, ValueError):
        valid_host = False
    add('public_host', 'pass' if valid_host else ('error' if production or hostname else 'warning'), 'Public hostname',
        'Set LYARA_PUBLIC_HOST to a public hostname only, without scheme, port, wildcard or path. DNS and TLS are not tested.')

    try:
        workspace = (base / env.get('LYARA_WORKSPACE', '.')).resolve()
        data = (base / env.get('LYARA_DATA_DIR', '.lyara')).resolve()
        valid_paths = workspace.is_dir() and not workspace.is_relative_to(data)
        add('paths', 'pass' if valid_paths else 'error', 'Workspace and private data',
            'The workspace must exist and cannot equal or be inside the private data directory.')
        ancestor = data
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        writable = ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK)
        add('data_directory', 'pass' if writable else 'error', 'Data directory permissions',
            'The existing data directory or its nearest existing parent must be writable. Permissions are inspected without creating a probe file.')
        files = [data / name for name in ['inputs.key', 'inputs.sqlite', 'operations.sqlite', 'checkpoints.sqlite']]
        private_files_ok = all(not path.is_symlink() and (not path.exists() or
                               (path.is_file() and os.access(path, os.R_OK | os.W_OK))) for path in files)
        add('private_files', 'pass' if private_files_ok else 'error', 'Private state files',
            'Existing private state files must be regular, readable, writable files, without symbolic links.')
        keyfile, database = files[:2]
        if keyfile.exists() or database.exists():
            try:
                if not private_files_ok or not keyfile.is_file():
                    raise ValueError()
                cipher = Fernet(keyfile.read_bytes())
                if database.is_file():
                    # Immutable read-only SQLite inspection cannot create journals or shared-memory files.
                    with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                        for row in db.execute('SELECT encrypted FROM inputs UNION ALL SELECT encrypted FROM retired'):
                            cipher.decrypt(row[0])
                add('vault', 'pass', 'Private configuration key', 'The existing Fernet key and saved configuration can be read together.')
            except (OSError, ValueError, TypeError, InvalidToken, sqlite3.Error):
                add('vault', 'error', 'Private configuration key', 'Private configuration cannot be read. Restore the matching inputs.key and inputs.sqlite together; do not regenerate the key.')
            if (data / 'inputs.sqlite-wal').exists():
                add('vault_wal', 'warning', 'Pending SQLite state', 'A vault write-ahead log exists. Stop the running instance and checkpoint or back up the complete data directory before validating a migration.')
        else:
            add('vault', 'pass', 'Private configuration key', 'No existing private configuration was found; the assistant will create its key on first startup.')
        if production:
            add('persistence', 'warning', 'Persistent storage', 'Mount the complete LYARA_DATA_DIR on persistent storage. Disk mounts, backups and single-instance operation require operator verification.')
    except (OSError, ValueError, TypeError):
        add('paths', 'error', 'Workspace and private data', 'Cannot inspect the configured workspace and data paths.')

    try:
        raw = env.get('LYARA_HOSTING_OPTIONS', '[]')
        if len(raw) > 20000:
            raise ValueError()
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) > 20:
            raise ValueError()
        options = [HostingOption.model_validate(value) for value in values]
        if len({option.id for option in options}) != len(options) or any(
                not all(value.strip() for value in [option.id, option.title, option.cost]) for option in options):
            raise ValueError()
        add('hosting_options', 'pass' if options else 'warning', 'Hosting choices',
            'Choices are structurally valid; the operator must verify real plan IDs, current prices and terms.' if options else
            'LYARA_HOSTING_OPTIONS is empty. Creating new sites is blocked; existing sites can keep their current plan.')
    except (ValueError, TypeError):
        add('hosting_options', 'error', 'Hosting choices', 'LYARA_HOSTING_OPTIONS must be a JSON list of unique choices with id, title and cost, at most 20 entries.')
    return {'ready': not any(check['status'] == 'error' for check in checks), 'checks': checks}
