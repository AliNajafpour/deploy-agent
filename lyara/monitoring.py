"""Opt-in, read-only polling for one operator and one active service instance."""
import json
import math
import threading
import time
import uuid
from datetime import datetime, timezone
from statistics import median
from urllib.parse import urlsplit

from .models import RunRequest
from .diagnostics import analyse
from .security import public_https, redact


class Monitoring:
    def __init__(self, assistant):
        self.assistant, self.store = assistant, assistant.store
        self._lock = threading.RLock()
        self._tick_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        with self.store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS monitoring_watches (project TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS monitoring_incidents (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS monitoring_open_incident
                    ON monitoring_incidents(project) WHERE state='open';
            ''')

    def _json(self, value, project):
        return json.dumps(redact(value, self.assistant._secret_values(project)), ensure_ascii=False)

    def _save_watch(self, watch):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO monitoring_watches VALUES (?,?)',
                       (watch['project'], self._json(watch, watch['project'])))

    def _save_incident(self, incident):
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO monitoring_incidents VALUES (?,?,?,?)',
                       (incident['id'], incident['project'], incident['state'], self._json(incident, incident['project'])))
            db.execute('''DELETE FROM monitoring_incidents WHERE state='resolved' AND id NOT IN (
                SELECT id FROM monitoring_incidents WHERE state='resolved' ORDER BY rowid DESC LIMIT 100)''')

    def configure(self, project, enabled, interval=60, health_url=''):
        RunRequest(project=project, message='Read-only monitoring', health_url=health_url)
        if type(enabled) is not bool or type(interval) is not int or not 60 <= interval <= 86400:
            raise ValueError('پایش به انتخاب روشن یا خاموش و فاصلهٔ ۶۰ تا ۸۶۴۰۰ ثانیه نیاز دارد.')
        if health_url:
            # Query strings can contain credentials, so never persist them as a watch destination.
            if urlsplit(health_url).query:
                raise ValueError('آدرس پایش نباید اطلاعات ورود یا پارامتر محرمانه داشته باشد.')
            try:
                public_https(health_url)
            except (OSError, ValueError) as error:
                raise ValueError('آدرس پایش باید یک مقصد عمومی HTTPS و بدون اطلاعات ورود باشد.') from error
        with self._lock:
            watches = self.list()
            previous = next((watch for watch in watches if watch['project'] == project), None)
            if enabled and not (previous and previous['enabled']) and sum(w['enabled'] for w in watches) >= 25:
                raise ValueError('حداکثر ۲۵ سایت می‌توانند هم‌زمان پایش شوند.')
            watch = dict(previous or {}, project=project, enabled=enabled, interval=interval, health_url=health_url)
            if not previous or any(watch[key] != previous[key] for key in ('enabled', 'interval', 'health_url')):
                watch.update(next_check=0, failure_count=0, signal_counts={}, recovery_counts={},
                             status='unknown' if enabled else 'disabled')
                if previous and health_url != previous['health_url']:
                    watch.update(latency_history=[], latency_baseline_ms=None, resource_last_at={})
            watch.setdefault('last_check', None)
            watch.setdefault('latest', {})
            self._save_watch(watch)
            return redact(watch, self.assistant._secret_values(project))

    def list(self):
        with self._lock, self.store.connect() as db:
            watches = [json.loads(row[0]) for row in db.execute('SELECT payload FROM monitoring_watches ORDER BY project')]
        return [redact(watch, self.assistant._secret_values(watch['project'])) for watch in watches]

    def incidents(self, project=None):
        if project is not None:
            RunRequest(project=project, message='Read-only monitoring')
        with self._lock, self.store.connect() as db:
            rows = db.execute('''SELECT payload FROM monitoring_incidents
                WHERE (? IS NULL OR project=?) ORDER BY rowid DESC LIMIT 100''', (project, project))
            incidents = [json.loads(row[0]) for row in rows]
        return [redact(incident, self.assistant._secret_values(incident['project'])) for incident in incidents]

    def _diagnose(self, incident, watch):
        try:
            release_id = watch['latest']['observations'].get('releases', {}).get('data', {}).get('currentRelease')
            source = self.store.latest_source(watch['project'], release_id or None)
            request = RunRequest(project=watch['project'], intent='diagnose', source=source,
                health_url=watch['health_url'], message='پایش فقط‌خواندنی: نشانهٔ پایدار ثبت شد: ' +
                '، '.join({'outage': 'ناموفق‌بودن اتصال سایت', 'latency': 'کندی نسبت به خط مبنا',
                           'cpu': 'فشار پردازنده با ظرفیت مشخص', 'memory': 'فشار حافظه با ظرفیت مشخص'}[cause]
                          for cause in incident.get('reasons', ['outage'])) + '. ' +
                'شواهد را بررسی و علت‌های ممکن و پیشنهاد قابل بازبینی ارائه کن؛ هیچ اقدامی خودکار تأیید نمی‌شود.')
            run = self.assistant.new(request)
            incident['run_id'] = run['id']
            with self._lock:
                self._save_incident(incident)
            if not self._stop.is_set():
                # A diagnostic may propose a mitigation; only a later human approval can execute it.
                self.assistant.process(run['id'])
        except Exception:
            incident['error'] = 'بررسی پیشنهادی کامل نشد؛ برای جلوگیری از تکرار، خودکار دوباره اجرا نمی‌شود.'
            with self._lock:
                self._save_incident(incident)

    def _signals(self, watch, health, observations, moment):
        """Persist measured baselines; never train a baseline on an ongoing slowdown."""
        analysis = analyse(observations, now=moment, secrets=self.assistant._secret_values(watch['project']))
        healthy = health.get('available') is True and health.get('healthy') is True
        failed = (health.get('available') is True or health.get('probe_attempted') is True) and health.get('healthy') is False
        signals = {'outage': True if failed else False if healthy else None, 'latency': None}
        latency = health.get('latency_ms')
        valid_latency = healthy and type(latency) in (int, float) and math.isfinite(latency) and latency >= 0
        history = [item for item in watch.get('latency_history', [])
                   if moment - 86400 <= item['at'] <= moment][-120:]
        baseline = watch.get('latency_baseline_ms')
        sufficient = len(history) >= 5 and history[-1]['at'] - history[0]['at'] >= 240
        if valid_latency and baseline is not None and sufficient:
            signals['latency'] = latency >= max(1000, baseline * 2)
        # Fixed baseline is intentionally not adapted to slow responses. A destination change resets it.
        if valid_latency and (baseline is None or latency < max(1000, baseline * 2)):
            if not history or moment - history[-1]['at'] >= 60:
                history.append({'at': moment, 'latency_ms': latency})
            history = history[-120:]
            if baseline is None and len(history) >= 5 and history[-1]['at'] - history[0]['at'] >= 240:
                baseline = median(item['latency_ms'] for item in history)
        watch.update(latency_history=history, latency_baseline_ms=baseline)
        fresh_readings = {'outage': True, 'latency': True}
        last_at = watch.setdefault('resource_last_at', {})
        for name in ('cpu', 'memory'):
            resource = analysis['resources'][name]
            signals[name] = resource['pressure'] if resource['freshness'] == 'fresh' and resource['sample_count'] >= 5 else None
            stamp = resource.get('latest_at')
            # Re-fetching the same metric window cannot count as another independent sample.
            fresh_readings[name] = stamp is not None and (last_at.get(name) is None or stamp > last_at[name])
            if stamp is not None:
                last_at[name] = max(stamp, last_at.get(name, stamp))
        return signals, fresh_readings, analysis

    def tick(self, now=None):
        if self._stop.is_set() or not self._tick_lock.acquire(blocking=False):
            return []
        acquired = False
        try:
            acquired = self.assistant.lock.acquire(blocking=False)
            if not acquired:
                return []
            moment = time.time() if now is None else float(now)
            checked_at = datetime.fromtimestamp(moment, timezone.utc).isoformat()
            checked = []
            for watch in self.list():
                if self._stop.is_set():
                    break
                if not watch['enabled'] or watch['next_check'] > moment:
                    continue
                try:
                    observations = self.assistant.observe(watch['project'])
                except Exception:
                    observations = {'project': {'available': False, 'error': 'خواندن وضعیت لیارا ممکن نبود.'}}
                if self._stop.is_set():
                    break
                try:
                    health = self.assistant.monitor_probe(watch['project'],observations,watch['health_url'])
                except Exception:
                    health = {'available': False, 'healthy': False, 'error': 'بررسی HTTPS ممکن نبود.'}
                if self._stop.is_set():
                    break
                healthy = health.get('available') is True and health.get('healthy') is True
                telemetry = all(isinstance(observations.get(key), dict) and observations[key].get('available') is True
                                for key in ('project', 'logs', 'events', 'releases', 'cpu', 'memory'))
                with self._lock:
                    # Disabling a watch while its HTTP request is in flight cancels further processing.
                    current = next((item for item in self.list() if item['project'] == watch['project']), None)
                    if not current or not current['enabled'] or any(current[key] != watch[key] for key in ('interval', 'health_url')):
                        continue
                    gap = moment - watch.get('last_check_epoch', moment)
                    if gap > max(180, watch['interval'] * 3) or gap < 0:
                        watch.update(signal_counts={}, recovery_counts={})
                    signals, fresh_readings, analysis = self._signals(watch, health, observations, moment)
                    counts = watch.setdefault('signal_counts', {'outage': watch['failure_count']})
                    for cause, signal in signals.items():
                        counts[cause] = min(3, counts.get(cause, 0) + 1) if signal is True and fresh_readings[cause] else (
                            counts.get(cause, 0) if signal is True else 0)
                    watch['failure_count'] = min(2, counts.get('outage', 0))
                    confirmed = [cause for cause, count in counts.items() if count >= (2 if cause == 'outage' else 3)]
                    watch.update(last_check=checked_at, next_check=moment + watch['interval'],
                        last_check_epoch=moment,
                        latest=redact({'health': health, 'observations': observations, 'signals': signals,
                                       'analysis': analysis}, self.assistant._secret_values(watch['project'])))
                    incident = next((item for item in self.incidents(watch['project']) if item['state'] == 'open'), None)
                    diagnose = bool(confirmed) and incident is None
                    if diagnose:
                        incident = {'id': str(uuid.uuid4()), 'project': watch['project'], 'state': 'open',
                                    'opened_at': checked_at, 'closed_at': None, 'run_id': None, 'health': health,
                                    'reasons': confirmed, 'active_causes': confirmed.copy(), 'evidence': watch['latest']}
                        # Reserve the incident before contacting the model; interrupted diagnostics are never replayed.
                        self._save_incident(incident)
                    elif incident:
                        reasons = set(incident.get('reasons', ['outage'])) | set(confirmed)
                        active = set(incident.get('active_causes', incident.get('reasons', ['outage']))) | set(confirmed)
                        recovery = watch.setdefault('recovery_counts', {})
                        for cause in active.copy():
                            recovery[cause] = recovery.get(cause, 0) + 1 if signals.get(cause) is False and fresh_readings[cause] else (
                                recovery.get(cause, 0) if signals.get(cause) is False else 0)
                            if recovery[cause] >= (1 if cause == 'outage' else 2):
                                active.remove(cause)
                                recovery[cause] = 0
                        incident.update(reasons=sorted(reasons), active_causes=sorted(active))
                        if not active:
                            incident.update(state='resolved', closed_at=checked_at)
                        self._save_incident(incident)
                    watch['status'] = ('unhealthy' if incident and incident['state'] == 'open' else 'pending'
                                       if any(value is True for value in signals.values()) else
                                       'healthy' if healthy and telemetry and all(signals[name] is False
                                           for name in ('cpu', 'memory')) else 'unknown')
                    self._save_watch(watch)
                checked.append(redact(watch, self.assistant._secret_values(watch['project'])))
                if diagnose and not self._stop.is_set():
                    self._diagnose(incident, watch)
            return checked
        finally:
            if acquired:
                self.assistant.lock.release()
            self._tick_lock.release()

    def start(self):
        with self._lock:
            if self._stop.is_set() or self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name='lyara-monitoring', daemon=True)
            self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                # A transient local/database failure must not kill the scheduler or leak credentials to logs.
                pass
            self._stop.wait(1)

    def close(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            # Network and model timeouts belong to their clients; shutdown never waits for them.
            self._thread.join(timeout=0.2)
