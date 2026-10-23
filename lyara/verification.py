"""Bounded, read-only website checks. Source files are parsed, never executed."""
import ast
import inspect
import json
import math
import re
import statistics
import tarfile
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from .project import EXCLUDED_DIRS, SENSITIVE


MAX_TARGETS = 5
UNSAFE = re.compile(r'logout|sign[-_]?out|delete|remove|destroy|checkout|purchase|buy|payment|mutate|admin|reset|restart|unsubscribe|confirm|activate|submit|send|create|update|callback|webhook', re.I)
PURE_CALLS = {'len', 'sum', 'min', 'max', 'abs', 'all', 'any', 'round', 'sorted', 'str',
              'int', 'float', 'bool', 'list', 'dict', 'tuple', 'set', 'range', 'enumerate',
              'zip', 'jsonify', 'render_template', 'JSONResponse', 'HTMLResponse',
              'PlainTextResponse', 'Response'}
PURE_ATTRIBUTES = {'json.dumps', 'json.loads', 'request.args.get', 'request.query_params.get'}


def _python_read_only(node, functions, visiting=(), blocked=()):
    """A small allowlist, not a proof of behavior of arbitrary Python objects."""
    if node.name in visiting or len(visiting) >= 8:
        return False
    visiting = (*visiting, node.name)
    # Dependencies and extra decorators can run effects before entering the handler.
    if any(isinstance(item, ast.Call) for item in ast.walk(node.args)):
        return False
    nodes = [item for statement in node.body for item in ast.walk(statement)]
    local_bindings = {item.id for item in nodes if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)}
    local_bindings.update(argument.arg for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))
    if len(nodes) > 2000:
        return False
    for item in nodes:
        if isinstance(item, (ast.Global, ast.Nonlocal, ast.Delete, ast.AugAssign, ast.Import, ast.ImportFrom,
                             ast.With, ast.AsyncWith, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return False
        if isinstance(item, (ast.Attribute, ast.Subscript)) and isinstance(item.ctx, ast.Store):
            return False
        if not isinstance(item, ast.Call):
            continue
        if isinstance(item.func, ast.Name):
            name = item.func.id
            if name in functions:
                if functions[name].decorator_list or not _python_read_only(functions[name], functions, visiting, blocked):
                    return False
            elif name not in PURE_CALLS or name in blocked or name in local_bindings:
                return False
        elif isinstance(item.func, ast.Attribute):
            qualified = ast.unparse(item.func)
            if qualified not in PURE_ATTRIBUTES or qualified.split('.')[0] in blocked:
                return False
        else:
            return False
    return True


def _shadowed_calls(tree):
    """Do not treat a user function imported as a builtin/render helper as pure."""
    blocked = set()
    trusted = {'flask': {'jsonify', 'render_template', 'Response'},
               'fastapi.responses': {'JSONResponse', 'HTMLResponse', 'PlainTextResponse', 'Response'},
               'starlette.responses': {'JSONResponse', 'HTMLResponse', 'PlainTextResponse', 'Response'},
               'builtins': PURE_CALLS - {'jsonify', 'render_template', 'JSONResponse', 'HTMLResponse',
                                        'PlainTextResponse', 'Response'}}
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                name = alias.asname or alias.name
                if alias.name == '*':
                    blocked.update(PURE_CALLS | {'json', 'request'})
                elif alias.asname or alias.name not in trusted.get(statement.module, set()):
                    blocked.add(name)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name != 'json' or alias.asname:
                    blocked.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            blocked.update(item.id for item in ast.walk(statement)
                           if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store))
    return blocked


def _js_mask(text):
    """Mask strings/comments while preserving offsets; reject unsupported syntax."""
    chars, index = list(text), 0
    while index < len(text):
        if text.startswith('//', index):
            end = text.find('\n', index)
            end = len(text) if end < 0 else end
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            if end < 0:
                return None
            end += 2
        elif text[index] == '`':
            # Tagged templates execute arbitrary functions without call parentheses.
            return None
        elif text[index] in "\"'":
            quote, end = text[index], index + 1
            while end < len(text):
                if text[end] == '\\':
                    end += 2
                elif text[end] == quote:
                    end += 1
                    break
                elif quote == '`' and text.startswith('${', end):
                    return None
                else:
                    end += 1
            else:
                return None
        elif text[index] == '/':
            # Regex literals and division require a real parser; skip this file.
            return None
        else:
            index += 1
            continue
        for position in range(index, min(end, len(text))):
            if chars[position] != '\n':
                chars[position] = ' '
        index = end
    return ''.join(chars)


def _js_routes(text):
    mask = _js_mask(text)
    if mask is None:
        # Unsupported syntax cannot turn an explicit handler into a static-file probe.
        return [(match[2], match.start(), False) for match in re.finditer(
            r'\b(?:app|router|server)\.get\s*\(\s*([\'"])(/[^\'"\r\n]*)\1\s*,', text)]
    routes = []
    for opening in re.finditer(r'\b(?:app|router|server)\.get\s*\(', mask):
        literal = re.match(r'\s*([\'"])(/[^\'"\r\n]*)\1\s*,', text[opening.end():])
        if not literal:
            continue
        start, depth, end = opening.end() + literal.end(), 1, opening.end()
        while end < len(mask) and end - opening.start() < 20000 and depth:
            depth += (mask[end] == '(') - (mask[end] == ')')
            end += 1
        if depth:
            continue
        body = mask[start:end - 1]
        inline = re.match(r'\s*(?:async\s+)?(?:function\s*\([^)]*\)|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)', body)
        if not inline:
            routes.append((literal[2], opening.start(), False))
            continue
        safe = not re.search(r'\+\+|--|(?<![=!<>])=(?!=|>)|\b(?:new|throw|delete|yield)\b', body)
        # Permit only the inline parameter list and pure response/JSON calls.
        statements = body[inline.end():]
        for call in re.finditer(r'([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*|\))\s*\(', statements):
            name = re.sub(r'\s+', '', call[1])
            if name not in {'res.json', 'res.send', 'res.end', 'res.status', 'response.json',
                            'response.send', 'response.end', 'response.status', 'JSON.stringify',
                            'JSON.parse', 'Object.keys', 'Object.values', 'Number', 'String',
                            'Boolean', 'Math.round', 'Math.floor', 'Math.ceil', 'Math.min', 'Math.max'}:
                safe = False
        # Chained calls and computed properties are outside the supported subset.
        if re.search(r'\]\s*\(|\)\s*\.|\.\s*constructor\b', statements):
            safe = False
        routes.append((literal[2], opening.start(), safe))
    return routes


def safe_path(value):
    if not isinstance(value, str) or len(value) > 300:
        return None
    # No encoding, parameters, queries or browser URL normalization ambiguities.
    if (not re.fullmatch(r'/[A-Za-z0-9_/.-]*', value) or value.startswith('//')
            or '//' in value or any(part.startswith('.') or SENSITIVE.search(part) for part in value.split('/') if part)
            or UNSAFE.search(value)):
        return None
    return value


def _origin(base_url):
    try:
        parsed = urlsplit(base_url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.query or parsed.fragment):
            raise ValueError
    except ValueError:
        raise ValueError('بررسی سایت فقط با آدرس HTTPS بدون اطلاعات ورود مجاز است.') from None
    return 'https://' + parsed.netloc


def discover_routes(artifact, observations=None, base_url=''):
    """Find literal GET routes only; conservative exclusions may omit valid pages."""
    if base_url:
        _origin(base_url)
    found = {}
    evidence_text = json.dumps(observations or {}, ensure_ascii=False)[:120000]
    observed = set(re.findall(r'(?<![\w/:])(/[A-Za-z0-9_/.-]{1,299})', evidence_text))
    candidates, excluded = [], set()

    def add(path, evidence, expected=None):
        path = safe_path(path)
        if path:
            descriptor = {'path': path, 'expected_statuses': expected or list(range(200, 300)),
                          'evidence': evidence}
            if expected and any(code >= 400 for code in expected):
                descriptor['expectation_evidence'] = evidence
            candidates.append(descriptor)

    total = 0
    with tarfile.open(artifact['path']) as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts
                    or any(part.startswith('.') or part in EXCLUDED_DIRS or SENSITIVE.search(part)
                           for part in path.parts) or member.size > 120000):
                continue
            if path.suffix == '.html':
                # Static hosts expose files in public/dist/build or the project root.
                relative = path
                if path.parts[0] in {'public', 'dist', 'build'}:
                    relative = PurePosixPath(*path.parts[1:])
                elif len(path.parts) > 1:
                    continue
                add('/' + str(relative), 'source:' + member.name)
                if str(relative) == 'index.html':
                    add('/', 'source:' + member.name)
                continue
            if path.suffix not in {'.py', '.js', '.mjs', '.cjs', '.ts'}:
                continue
            total += member.size
            if total > 600000:
                break
            try:
                text = archive.extractfile(member).read().decode('utf-8-sig')
            except UnicodeDecodeError:
                continue
            if path.suffix == '.py':
                try:
                    tree = ast.parse(text)
                except (SyntaxError, ValueError, RecursionError):
                    continue
                functions = {node.name: node for node in tree.body
                             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                blocked = _shadowed_calls(tree)
                for node in functions.values():
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for decorator in node.decorator_list:
                        if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                                and decorator.func.attr in {'get', 'route'} and decorator.args
                                and isinstance(decorator.args[0], ast.Constant)
                                and isinstance(decorator.args[0].value, str)):
                            continue
                        keywords = {item.arg: item.value for item in decorator.keywords}
                        methods = keywords.get('methods')
                        if methods is not None:
                            try:
                                if ast.literal_eval(methods) != ['GET']:
                                    continue
                            except (ValueError, TypeError):
                                continue
                        route = decorator.args[0].value
                        extra_decorators = [item for item in node.decorator_list if item is not decorator]
                        dependency = any(keyword.arg == 'dependencies' for keyword in decorator.keywords)
                        if extra_decorators or dependency or not _python_read_only(node, functions, blocked=blocked):
                            # A GET declaration is not permission to exercise a visible write.
                            found.pop(route, None)
                            excluded.add(route)
                            continue
                        expected = None
                        status = keywords.get('status_code')
                        if isinstance(status, ast.Constant) and type(status.value) is int and 200 <= status.value < 500:
                            expected = [status.value]
                        add(route, f'source:{member.name}:{decorator.lineno}', expected)
            else:
                # ponytail: inline literal JavaScript subset only; use AST when a JS parser is already available.
                for route, offset, safe in _js_routes(text):
                    if safe:
                        add(route, f'source:{member.name}:{text.count(chr(10), 0, offset) + 1}')
                    else:
                        excluded.add(route)
    # Error paths get priority only when supported by a source route; arbitrary log URLs are not probes.
    for descriptor in sorted(candidates, key=lambda item: 0 if item['path'] == '/' else 1 if item['path'] in observed else 2):
        if descriptor['path'] in excluded:
            continue
        if descriptor['path'] not in found:
            found[descriptor['path']] = descriptor
        elif descriptor.get('expectation_evidence'):
            found[descriptor['path']] = descriptor
        if len(found) >= MAX_TARGETS:
            break
    return list(found.values())


def _targets(targets):
    result = []
    for target in targets[:MAX_TARGETS]:
        path = safe_path(target.get('path'))
        if path is None:
            raise ValueError('مسیر بررسی سایت مجاز نیست.')
        statuses = target.get('expected_statuses', list(range(200, 300)))
        if (not isinstance(statuses, list) or not statuses or any(type(code) is not int or
                not (200 <= code < 300 or 400 <= code < 500 and
                     str(target.get('expectation_evidence', '')).startswith('source:')) for code in statuses)):
            raise ValueError('انتظار پاسخ مسیر باید از سورس مشخص باشد.')
        result.append({'path': path, 'expected_statuses': statuses})
    if not result:
        raise ValueError('حداقل یک مسیر برای بررسی لازم است.')
    return result


def summarize(samples):
    latency = sorted(row['latency_ms'] for row in samples if isinstance(row.get('latency_ms'), (int, float))
                     and math.isfinite(row['latency_ms']) and row['latency_ms'] >= 0)
    return {'total': len(samples), 'success': sum(row.get('success') is True for row in samples),
            'errors': sum(row.get('success') is False for row in samples),
            'unknown': sum(row.get('success') is None for row in samples),
            'median_ms': statistics.median(latency) if latency else None,
            'p95_ms': latency[math.ceil(len(latency) * .95) - 1] if latency else None}


def collect_samples(cloud, base_url, targets, samples=3, timeout=45, interval=0,
                    clock=time.monotonic, sleep=time.sleep):
    origin, targets = _origin(base_url), _targets(targets)
    if type(samples) is not int or not 1 <= samples <= 5 or not 0 < timeout <= 180 or not 0 <= interval <= 10:
        raise ValueError('تعداد یا زمان بررسی مجاز نیست.')
    deadline, rows = clock() + timeout, []
    accepts_timeout = 'timeout' in inspect.signature(cloud.health).parameters
    for number in range(samples):
        for target in targets:
            remaining = deadline - clock()
            if remaining <= 0:
                return {'targets': targets, 'samples': rows, 'summary': summarize(rows), 'complete': False}
            started = clock()
            try:
                probe = cloud.health(origin + target['path'], **({'timeout': min(10, remaining)} if accepts_timeout else {}))
            except Exception:
                # Exception details can include URLs or credentials; keep only a generic failure.
                probe = {'probe_attempted': False, 'available': False}
            status = probe.get('status')
            known = probe.get('available') is True and type(status) is int
            success = status in target['expected_statuses'] if known else False if probe.get('probe_attempted') is True else None
            latency = probe.get('latency_ms')
            if not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
                latency = None
            rows.append({'path': target['path'], 'sample': number + 1,
                         'status': status if known else None, 'latency_ms': latency,
                         'success': success, 'elapsed_ms': round(max(0, clock() - started) * 1000),
                         'error': None if success is True else 'پاسخ مسیر مطابق انتظار نبود.' if known else
                                  'اتصال ناموفق بود.' if success is False else 'دادهٔ پاسخ در دسترس نیست.'})
        if number + 1 < samples and interval:
            sleep(min(interval, max(0, deadline - clock())))
    return {'targets': targets, 'samples': rows, 'summary': summarize(rows), 'complete': clock() <= deadline}


def verify_stability(cloud, project, release_id, base_url, targets, rounds=3, timeout=90,
                     interval=2, clock=time.monotonic, sleep=time.sleep):
    if not release_id or type(rounds) is not int or not 3 <= rounds <= 5 or not 0 < timeout <= 180 or not 0 <= interval <= 10:
        raise ValueError('نسخه و مهلت بررسی پایداری معتبر نیست.')
    deadline, checks, rows = clock() + timeout, [], []

    def active():
        try:
            remaining = deadline - clock()
            if remaining <= 0:
                return {'current_release': None, 'confirmed': False}
            accepts_timeout = 'timeout' in inspect.signature(cloud.releases).parameters
            data = cloud.releases(project, **({'timeout': min(10, remaining)} if accepts_timeout else {}))
            current = data.get('currentRelease')
            release = next((item for item in data.get('releases', []) if item.get('_id') == release_id), {})
            return {'current_release': current if current == release_id else 'other' if current else None,
                    'confirmed': current == release_id and str(release.get('state', '')).upper() in
                                 {'READY', 'SUCCESS', 'SUCCEEDED', 'RUNNING'}}
        except Exception:
            return {'current_release': None, 'confirmed': False}

    for number in range(rounds):
        if clock() >= deadline:
            break
        before = active()
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sample = collect_samples(cloud, base_url, targets, samples=1, timeout=min(180, remaining), clock=clock, sleep=sleep)
        after = active() if clock() < deadline else {'current_release': None, 'confirmed': False}
        rows.extend(sample['samples'])
        checks.append({'round': number + 1, 'before': before, 'after': after,
                       'success': sample['complete'] and sample['summary']['total'] == len(sample['targets'])
                                  and sample['summary']['success'] == len(sample['targets'])})
        if number + 1 < rounds:
            sleep(min(interval, max(0, deadline - clock())))
    confirmed = len(checks) == rounds and all(item['before']['confirmed'] and item['after']['confirmed'] for item in checks)
    return {'stable': confirmed and all(item['success'] for item in checks) and clock() <= deadline,
            'release_confirmed': confirmed, 'rounds': checks, 'samples': rows,
            'summary': summarize(rows), 'complete': len(checks) == rounds and clock() <= deadline}


def compare_results(before, after, stability=None):
    old, new = before.get('summary', {}), after.get('summary', {})
    evidence = {'before': old, 'after': new,
                'release_confirmed': bool(stability and stability.get('release_confirmed')),
                'stable': bool(stability and stability.get('stable'))}
    old_paths = {row['path'] for row in before.get('samples', [])}
    new_paths = {row['path'] for row in after.get('samples', [])}
    enough = (old_paths == new_paths and bool(old_paths) and all(
        sum(row['path'] == path for row in dataset.get('samples', [])) >= 3
        for path in old_paths for dataset in (before, after)))
    known = enough and before.get('complete') and after.get('complete') and not old.get('unknown', 1) and not new.get('unknown', 1)
    if not known:
        outcome = 'نامشخص'
    elif old.get('errors', 0) > 0 and new.get('errors') == 0 and stability and stability.get('stable'):
        outcome = 'رفع شد'
    elif not stability or not stability.get('release_confirmed'):
        outcome = 'نامشخص'
    elif (new.get('success', 0) / new['total'] > old.get('success', 0) / old['total'] or
          new.get('success', 0) / new['total'] >= old.get('success', 0) / old['total'] and
          old.get('median_ms') and new.get('median_ms') is not None and new['median_ms'] < old['median_ms'] * .8):
        outcome = 'بهتر شد'
    elif old.get('errors') == 0 and new.get('errors') == 0:
        outcome = 'نامشخص'
    else:
        outcome = 'هنوز مشکل دارد'
    evidence['comparable'] = bool(known)
    return {'outcome': outcome, 'evidence': evidence,
            'message': f"{outcome}؛ قبل: {old.get('success', 0)} پاسخ موفق و {old.get('errors', 0)} خطا، بعد: {new.get('success', 0)} پاسخ موفق و {new.get('errors', 0)} خطا."}
