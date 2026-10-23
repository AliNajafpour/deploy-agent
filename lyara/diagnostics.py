"""Bounded, deterministic telemetry calculations; the model only explains these facts."""
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from statistics import mean

from .security import redact


MAX_ROWS = 1000
MAX_SERIES = 16
MAX_POINTS = 512
FRESH_SECONDS = 300
UNITS = {'b': ('bytes', 1), 'bytes': ('bytes', 1), 'byte': ('bytes', 1),
         'kb': ('bytes', 1000), 'mb': ('bytes', 1000000), 'gb': ('bytes', 1000000000),
         'kib': ('bytes', 1024), 'mib': ('bytes', 1048576), 'gib': ('bytes', 1073741824),
         '%': ('percent', 1), 'percent': ('percent', 1), 'percentage': ('percent', 1),
         'ratio': ('ratio', 1), 'cores': ('cores', 1), 'core': ('cores', 1),
         'millicores': ('cores', .001)}
ERROR = re.compile(r'error|exception|traceback|fatal|failed|timeout|\boom\b|out of memory|cannot find|not found|\b[45]\d\d\b', re.I)
OOM = re.compile(r'\boomkilled\b|\boom[_ -]?kill(?:er|ed)?\b|out of memory|heap out of memory|memoryerror', re.I)
NO_OOM = re.compile(r'\b(?:no|not|without)\s+(?:an?\s+)?(?:oom(?:killed)?|out of memory)|\boom(?:killed)?\s*[=:]\s*(?:false|0)\b', re.I)
RESTART = re.compile(r'\brestart(?:ed|ing|s)?\b|crashloopbackoff|back[- ]off restarting', re.I)
MISSING = re.compile(r'(?:missing|required|not set|undefined|not configured).{0,50}(?:environment|env(?:ironment)? variable|configuration)|(?:environment|env(?:ironment)? variable|configuration).{0,50}(?:missing|required|not set|undefined|not configured)|keyerror:\s*[\'\"]?[A-Z][A-Z0-9_]+|(?-i:[A-Z][A-Z0-9_]{2,})\s+(?:is\s+)?(?:missing|required|not set|undefined|not configured)', re.I)
DEPENDENCY = re.compile(r'modulenotfounderror|no module named|cannot find (?:module|package)|module_not_found|err_module_not_found|importerror', re.I)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _timestamp(value):
    number = _number(value)
    if number is not None:
        return number
    if isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.timestamp() if parsed.tzinfo else None
        except (ValueError, OverflowError, OSError):
            pass
    return None


def _row_time(row):
    return next((stamp for key in ('datetime', 'timestamp', 'time', 'deployedAt', 'createdAt')
                 if (stamp := _timestamp(row.get(key))) is not None), None)


def _data(observations, name):
    observation = observations.get(name)
    return observation.get('data') if isinstance(observation, dict) and observation.get('available') is True else None


def _rows(data, key):
    rows = data.get(key, []) if isinstance(data, dict) else data
    return [row for row in rows[-MAX_ROWS:] if isinstance(row, dict)] if isinstance(rows, list) else []


def _resource(observations, name, now):
    data = _data(observations, name)
    raw = data.get('result', []) if isinstance(data, dict) else []
    series, truncated = [], len(raw) > MAX_SERIES if isinstance(raw, list) else False
    for index, item in enumerate(raw[:MAX_SERIES] if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        values = item.get('values', [])
        truncated |= isinstance(values, list) and len(values) > MAX_POINTS
        points = {}
        for pair in values[-MAX_POINTS:] if isinstance(values, list) else []:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                continue
            stamp, value = _timestamp(pair[0]), _number(pair[1])
            if stamp is not None and value is not None and value >= 0 and now - 900 <= stamp <= now + 30:
                points[stamp] = value
        points = sorted(points.items())
        latest = points[-1][0] if points else None
        # Old points are still evidence of missing freshness, never evidence of zero use.
        raw_stamps = [_timestamp(p[0]) for p in values[-MAX_POINTS:]
                      if isinstance(p, (list, tuple)) and len(p) == 2] if isinstance(values, list) else []
        raw_latest = max((stamp for stamp in raw_stamps if stamp is not None), default=None)
        freshness = ('fresh' if latest is not None and now - latest <= FRESH_SECONDS else
                     'stale' if raw_latest is not None and now - raw_latest > FRESH_SECONDS else 'unknown')
        unit = item.get('unit', data.get('unit'))
        unit = unit[:32].lower() if isinstance(unit, str) else None
        canonical = UNITS.get(unit)
        if canonical and canonical[0] not in ({'bytes', 'percent', 'ratio'} if name == 'memory' else {'cores', 'percent', 'ratio'}):
            canonical = None
        sufficient = len(points) >= 5 and points[-1][0] - points[0][0] >= 60 and freshness == 'fresh'
        trend, slope, utilization, pressure = 'unknown', None, None, None
        reason = 'برای نتیجه‌گیری، نمونه‌های تازه و کافی با واحد مشخص لازم است.'
        if sufficient and canonical:
            times, amounts = zip(*points)
            centre, avg = mean(times), mean(amounts)
            denominator = sum((stamp - centre) ** 2 for stamp in times)
            slope = sum((stamp - centre) * (value - avg) for stamp, value in points) / denominator
            change = slope * (times[-1] - times[0])
            trend = 'stable' if abs(change) <= max(abs(avg) * .05, 1e-12) else 'increasing' if change > 0 else 'decreasing'
            capacity = _number(item.get('capacity', data.get('capacity')))
            capacity_unit = item.get('capacity_unit', data.get('capacity_unit'))
            capacity_unit = UNITS.get(capacity_unit.lower()) if isinstance(capacity_unit, str) else None
            factor = (100 if canonical[0] == 'percent' else 1 if canonical[0] == 'ratio' else
                      capacity * capacity_unit[1] / canonical[1] if capacity and capacity > 0 and capacity_unit and capacity_unit[0] == canonical[0] else None)
            if factor:
                ratios = [value / factor for _, value in points[-3:]]
                if all(0 <= value <= 1 for value in ratios):
                    utilization = round(mean(ratios), 6)
                    pressure = True if min(ratios) >= .9 else False if max(ratios) < .9 else None
            reason = ('مصرف در سه نمونهٔ پایانی نزدیک ظرفیت است.' if pressure is True else
                      'مصرف در سه نمونهٔ پایانی زیر آستانهٔ فشار است.' if pressure is False else
                      'روند محاسبه شد؛ ظرفیت یا نسبت مصرف معتبر برای تشخیص فشار کافی نیست.')
        series.append({'id': f'before.{name}.series.{index}', 'sample_count': len(points), 'freshness': freshness,
                       'latest_at': latest if freshness == 'fresh' else raw_latest, 'unit': unit, 'trend': trend, 'slope_per_second': slope,
                       'utilization': utilization, 'pressure': pressure, 'reason': reason})
    pressures = [item['pressure'] for item in series]
    trends = {item['trend'] for item in series}
    units = {item['unit'] for item in series}
    fresh = all(item['freshness'] == 'fresh' for item in series)
    return {'sample_count': sum(item['sample_count'] for item in series),
            'freshness': 'fresh' if series and fresh else 'stale' if any(item['freshness'] == 'stale' for item in series) else 'unknown',
            'latest_at': max((item['latest_at'] for item in series if item['latest_at'] is not None), default=None),
            'unit': next(iter(units)) if len(units) == 1 else None,
            'trend': next(iter(trends)) if len(trends) == 1 else 'unknown',
            'pressure': True if True in pressures else False if series and all(value is False for value in pressures) and not truncated else None,
            'reason': series[0]['reason'] if len(series) == 1 else 'هر سری جداگانه و با واحد و ظرفیت خودش بررسی می‌شود.',
            'series': series, 'truncated': truncated}


def analyse(observations, now=None, secrets=()):
    """Compute bounded facts from Liara observation envelopes, without any I/O or action."""
    now = _timestamp(now) if now is not None else time.time()
    if now is None:
        raise ValueError('زمان بررسی معتبر نیست.')
    observations = observations if isinstance(observations, dict) else {}
    evidence, findings, errors, signals = {}, [], [], {'missing_configuration': [], 'dependency': [], 'oom': [], 'restart': [], 'exit_137': [], 'oom_denied': []}
    for name, rows_key in (('logs', 'logs'), ('events', 'events'), ('build_logs', 'buildOutput')):
        for index, row in enumerate(_rows(_data(observations, name), rows_key)):
            text = ' '.join(str(row.get(key, ''))[:4096] for key in ('reason', 'message', 'text', 'line', 'type', 'stream') if isinstance(row.get(key, ''), (str, int)))
            text = redact(text[:8192], secrets)
            stamp = _row_time(row)
            identity = f'before.{name}.{index}'
            matched = False
            if NO_OOM.search(text):
                signals['oom_denied'].append(identity)
                matched = True
            for kind, pattern in (('missing_configuration', MISSING), ('dependency', DEPENDENCY), ('oom', OOM), ('restart', RESTART)):
                if pattern.search(text):
                    if kind == 'oom' and NO_OOM.search(text):
                        continue
                    signals[kind].append(identity)
                    matched = True
            if re.search(r'(?:exit(?:ed)?(?: code)?|code)[\s:=]+137\b', text, re.I):
                signals['exit_137'].append(identity)
                matched = True
            if ERROR.search(text) or str(row.get('level', '')).lower() in {'error', 'fatal', 'critical'}:
                signature = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\b0x[0-9a-f]+\b|\b\d+(?:\.\d+)?\b',
                                   lambda match: match[0] if re.fullmatch('[45][0-9]{2}', match[0]) else '#', text, flags=re.I)
                signature = re.sub(r'\s+', ' ', signature).strip()[:400]
                errors.append({'id': identity, 'signature': signature, 'timestamp': stamp})
                matched = True
            if matched:
                evidence[identity] = {'text': text[:300], 'timestamp': stamp}
    counts = Counter(error['signature'] for error in errors)
    repeated = [{'signature': signature, 'count': count,
                 'evidence': [error['id'] for error in errors if error['signature'] == signature][:20]}
                for signature, count in counts.most_common(20)]
    resources = {name: _resource(observations, name, now) for name in ('memory', 'cpu')}
    for resource in resources.values():
        for series in resource['series']:
            evidence[series['id']] = dict(series)

    def finding(kind, summary, support, confidence='medium', opposing=(), missing=()):
        findings.append({'kind': kind, 'summary': summary, 'confidence': confidence,
                         'supporting': list(support)[:20], 'opposing': list(opposing)[:20], 'missing': list(missing)})

    for kind, summary in (('missing_configuration', 'لاگ به اطلاعات یا تنظیمات گمشده اشاره می‌کند.'),
                          ('dependency', 'لاگ به مشکل واردکردن وابستگی اشاره می‌کند.'),
                          ('restart', 'رویداد یا لاگ راه‌اندازی دوباره ثبت شده است.')):
        if signals[kind]:
            finding(kind, summary, signals[kind], missing=['علت اصلی باید با کد و تنظیمات فعلی تطبیق داده شود.'])
    if signals['oom']:
        finding('oom', 'نشانهٔ صریح کمبود حافظه ثبت شده است؛ علت رشد حافظه هنوز مشخص نیست.', signals['oom'], 'high',
                opposing=signals['oom_denied'], missing=['زمان و ظرفیت همان نمونه برای تعیین علت لازم است؛ دادهٔ پس از راه‌اندازی علت قبلی را رد نمی‌کند.'])
    elif signals['exit_137']:
        finding('possible_oom', 'خروج با کد ۱۳۷ ثبت شده است؛ این کد به‌تنهایی کمبود حافظه را ثابت نمی‌کند.', signals['exit_137'], 'low',
                missing=['دلیل خاتمهٔ پردازش یا رویداد صریح OOM موجود نیست.'])
    for item in repeated:
        if item['count'] >= 2:
            finding('repeated_error', f'یک الگوی خطا {item["count"]} بار در دادهٔ دریافت‌شده دیده شد.', item['evidence'], 'high',
                    missing=['تعدادها فقط مربوط به بازه و بخش دریافت‌شدهٔ لاگ هستند.'])
    for name, resource in resources.items():
        pressure_ids = [item['id'] for item in resource['series'] if item['pressure'] is True]
        if pressure_ids:
            finding(name + '_pressure', 'مصرف در چند نمونهٔ تازه نزدیک ظرفیت مشخص‌شده است.', pressure_ids,
                    missing=['این هم‌زمانی به‌تنهایی علت کندی یا خطا را ثابت نمی‌کند.'])
        growing = [item['id'] for item in resource['series'] if item['trend'] == 'increasing' and item['freshness'] == 'fresh']
        if name == 'memory' and growing:
            declining = [item['id'] for item in resource['series'] if item['trend'] == 'decreasing']
            finding('memory_growth', 'روند حافظه در نمونه‌های کافی افزایشی است؛ این مشاهده اثبات نشت حافظه نیست.', growing,
                    opposing=declining, missing=['برای اثبات نشت، بار کاری، بازیافت حافظه و بررسی heap در بازهٔ طولانی‌تر لازم است.'])
    releases = _rows(_data(observations, 'releases'), 'releases')
    dated = [(stamp, index) for index, row in enumerate(releases) if (stamp := _row_time(row)) is not None and stamp <= now + 30]
    relation = {'available': False, 'reason': 'برای مقایسه، زمان معتبر انتشار و زمان خطاها لازم است.'}
    if dated and any(error['timestamp'] is not None for error in errors):
        stamp, index = max(dated)
        before = [error['id'] for error in errors if error['timestamp'] is not None and stamp - 900 <= error['timestamp'] < stamp]
        after = [error['id'] for error in errors if error['timestamp'] is not None and stamp <= error['timestamp'] <= min(stamp + 900, now)]
        identity = f'before.releases.{index}'
        evidence[identity] = {'timestamp': stamp, 'event': 'release timestamp from API'}
        relation = {'available': True, 'release_at': stamp, 'window_seconds': 900, 'before_errors': len(before),
                    'after_errors': len(after), 'after_window_complete': now >= stamp + 900, 'causation_proven': False,
                    'supporting': [identity] + after[:20], 'opposing': before[:20]}
        if after:
            finding('release_correlation', 'خطاهایی پس از انتشار ثبت شده‌اند؛ ترتیب زمانی به‌تنهایی علت را ثابت نمی‌کند.',
                    [identity] + after, 'medium' if len(after) >= 3 and not before else 'low', opposing=before,
                    missing=['کامل‌بودن لاگ و بار مشابه قبل و بعد تأیید نشده است.'])
    # Keep representatives for model/report payloads; counts above cover the bounded input window.
    referenced = [identity for item in findings for key in ('supporting', 'opposing') for identity in item[key]]
    referenced += relation.get('supporting', []) + relation.get('opposing', [])
    selected = list(dict.fromkeys(referenced))[:64]
    evidence = {identity: evidence[identity] for identity in selected if identity in evidence}
    for item in findings:
        for key in ('supporting', 'opposing'):
            omitted = len(item[key]) - sum(identity in evidence for identity in item[key])
            item[key] = [identity for identity in item[key] if identity in evidence]
            if omitted:
                item['missing'].append('بخشی از شاهدهای تکراری برای محدودماندن اندازهٔ پاسخ حذف شد.')
    for item in repeated:
        item['evidence'] = [identity for identity in item['evidence'] if identity in evidence]
    for key in ('supporting', 'opposing'):
        if key in relation:
            relation[key] = [identity for identity in relation[key] if identity in evidence]
    return {'errors': {'total': len(errors), 'patterns': repeated}, 'signals': {key: len(ids) for key, ids in signals.items()},
            'resources': resources, 'release_correlation': relation, 'findings': findings, 'evidence': evidence,
            'missing_data': ['before.' + name for name in ('logs', 'events', 'releases', 'cpu', 'memory')
                             if _data(observations, name) is None],
            'limits': {'log_rows_per_source': MAX_ROWS, 'series': MAX_SERIES, 'points_per_series': MAX_POINTS,
                       'fresh_seconds': FRESH_SECONDS, 'minimum_samples': 5, 'minimum_span_seconds': 60,
                       'pressure_ratio_threshold': .9, 'sustained_pressure_samples': 3,
                       'memory_growth_is_leak_proof': False}}
