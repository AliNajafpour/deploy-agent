import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .security import redact


class UncertainAction(RuntimeError):
    uncertain = True


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, updated TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, updated TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS controls (
                    id INTEGER PRIMARY KEY CHECK(id=1), stopped INTEGER NOT NULL, generation INTEGER NOT NULL);
                INSERT OR IGNORE INTO controls VALUES (1,0,0);
                CREATE TABLE IF NOT EXISTS managed_projects (project TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS controls_audit (
                    id INTEGER PRIMARY KEY, updated TEXT NOT NULL, operation TEXT NOT NULL,
                    project TEXT, enabled INTEGER NOT NULL, generation INTEGER NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, run_id, payload):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?)',
                       (run_id, now(), json.dumps(redact(payload), ensure_ascii=False)))

    def get(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise ValueError('عملیات پیدا نشد.')
        return json.loads(row[0])

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM runs ORDER BY updated DESC LIMIT 50').fetchall()
        return [json.loads(row[0]) for row in rows]

    def actions(self,run_id):
        prefix=run_id+':'
        with self.connect() as db:
            rows=db.execute('SELECT id,status,updated,payload FROM actions WHERE substr(id,1,?)=? ORDER BY updated,id',
                            (len(prefix),prefix)).fetchall()
        return [{'id':key,'status':status,'updated':updated,'result':json.loads(payload)} for key,status,updated,payload in rows]

    @staticmethod
    def _project(project):
        if not isinstance(project, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', project):
            raise ValueError('نام سایت معتبر نیست.')

    @staticmethod
    def _controls(db):
        stopped, generation = db.execute('SELECT stopped,generation FROM controls WHERE id=1').fetchone()
        projects = [row[0] for row in db.execute('SELECT project FROM managed_projects ORDER BY project')]
        return {'stopped': bool(stopped), 'generation': generation, 'managed_projects': projects}

    def controls(self):
        with self.connect() as db:
            db.execute('BEGIN')
            return self._controls(db)

    @staticmethod
    def _expected_generation(db, expected_generation):
        if expected_generation is None:
            return
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError('نسخهٔ کنترل دسترسی معتبر نیست.')
        if expected_generation != db.execute('SELECT generation FROM controls WHERE id=1').fetchone()[0]:
            raise PermissionError('وضعیت دسترسی تغییر کرده است؛ وضعیت تازه را بررسی و دوباره تأیید کنید.')

    def set_emergency(self, enabled, expected_generation=None):
        if type(enabled) is not bool:
            raise ValueError('وضعیت توقف باید روشن یا خاموش باشد.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expected_generation(db, expected_generation)
            db.execute('UPDATE controls SET stopped=?,generation=generation+1 WHERE id=1', (enabled,))
            controls = self._controls(db)
            db.execute('INSERT INTO controls_audit(updated,operation,project,enabled,generation) VALUES (?,?,?,?,?)',
                       (now(), 'set_emergency', None, enabled, controls['generation']))
            return controls

    def set_managed(self, project, managed, expected_generation=None):
        self._project(project)
        if type(managed) is not bool:
            raise ValueError('وضعیت دسترسی باید روشن یا خاموش باشد.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expected_generation(db, expected_generation)
            if managed:
                db.execute('INSERT OR IGNORE INTO managed_projects VALUES (?)', (project,))
            else:
                db.execute('DELETE FROM managed_projects WHERE project=?', (project,))
            db.execute('UPDATE controls SET generation=generation+1 WHERE id=1')
            controls = self._controls(db)
            db.execute('INSERT INTO controls_audit(updated,operation,project,enabled,generation) VALUES (?,?,?,?,?)',
                       (now(), 'set_managed', project, managed, controls['generation']))
            return controls

    def _assert_mutation(self, db, project, generation, creating=False):
        self._project(project)
        if type(generation) is not int or generation < 0 or type(creating) is not bool:
            raise ValueError('نسخهٔ مجوز معتبر نیست.')
        stopped, current = db.execute('SELECT stopped,generation FROM controls WHERE id=1').fetchone()
        if stopped:
            raise PermissionError('توقف اضطراری فعال است؛ تغییر تازه‌ای ارسال نمی‌شود. اقدام قبلاً ارسال‌شده ممکن است ادامه داشته باشد.')
        if generation != current:
            raise PermissionError('دسترسی یا توقف اضطراری پس از پیشنهاد تغییر کرده است؛ پیشنهاد و تأیید تازه لازم است.')
        if not creating and not db.execute('SELECT 1 FROM managed_projects WHERE project=?', (project,)).fetchone():
            raise PermissionError('این سایت فقط خواندنی است؛ ابتدا اجازهٔ مدیریت آن را فعال کنید.')

    def assert_mutation(self, project, generation, creating=False):
        with self.connect() as db:
            db.execute('BEGIN')
            self._assert_mutation(db, project, generation, creating)

    def register_created(self, project, generation):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._assert_mutation(db, project, generation, creating=True)
            db.execute('INSERT OR IGNORE INTO managed_projects VALUES (?)', (project,))
            db.execute('INSERT INTO controls_audit(updated,operation,project,enabled,generation) VALUES (?,?,?,?,?)',
                       (now(), 'register_created', project, True, generation))
            return self._controls(db)

    def control_history(self):
        with self.connect() as db:
            rows = db.execute('SELECT updated,operation,project,enabled,generation FROM controls_audit ORDER BY id DESC LIMIT 100').fetchall()
        return [{'updated': updated, 'operation': operation, 'project': project, 'enabled': bool(enabled), 'generation': generation}
                for updated, operation, project, enabled, generation in rows]

    def known_release(self,project,release_id):
        if not release_id:
            return None
        # ponytail: scan local history for a source lookup; index releases for large operation histories.
        with self.connect() as db:
            for row in db.execute('SELECT payload FROM runs ORDER BY updated DESC'):
                state=json.loads(row[0])
                if state['request']['project']!=project:
                    continue
                candidates=list(enumerate(state.get('results',[])))
                if state.get('pending_release'):
                    candidates.append((state['pending_release']['step_index'],state['pending_release']))
                steps=state.get('plan',{}).get('steps',[])
                for index,result in candidates:
                    if result.get('releaseID')==release_id and result.get('sourceID'):
                        if index>=len(steps) or steps[index]['action'] not in {'deploy','redeploy','repair_deploy'}:
                            continue
                        args=steps[index]['args']
                        return {'sourceID':result['sourceID'],'port':args['port'],'platform':args['platform']}

    def latest_source(self, project, release_id=None):
        sources, releases = {}, []
        # ponytail: scan durable history for source provenance; index releases when histories become large.
        with self.connect() as db:
            for row in db.execute('SELECT payload FROM runs ORDER BY updated DESC, rowid DESC'):
                state = json.loads(row[0])
                request = state.get('request', {})
                if request.get('project') != project:
                    continue
                steps = state.get('plan', {}).get('steps', [])
                for index, result in enumerate(state.get('results', [])):
                    if index >= len(steps) or steps[index]['action'] not in {'deploy', 'repair_deploy', 'redeploy'}:
                        continue
                    if not result.get('releaseID') or not result.get('sourceID') or result.get('release', {}).get('verified') is not True:
                        continue
                    releases.append((result['releaseID'], result['sourceID']))
                    if steps[index]['action'] != 'redeploy' and request.get('source'):
                        sources.setdefault(result['sourceID'], request['source'])
        for deployed_release, source_id in releases:
            if release_id is None or deployed_release == release_id:
                if source_id in sources:
                    return sources[source_id]
        return '.'

    def verified_run(self,project,release_id):
        if not release_id:return None
        with self.connect() as db:
            for row in db.execute('SELECT payload FROM runs ORDER BY updated DESC,rowid DESC'):
                state=json.loads(row[0])
                if state.get('request',{}).get('project')!=project:continue
                steps=state.get('plan',{}).get('steps',[])
                if any(index<len(steps) and steps[index]['action'] in {'deploy','repair_deploy','redeploy'}
                    and result.get('releaseID')==release_id and result.get('release',{}).get('verified') is True
                    for index,result in enumerate(state.get('results',[]))):
                    return state

    def write_once(self, key, function, permission=None):
        with self.connect() as db:
            # Reservation is the dispatch boundary; a stop cannot retract an external request already reserved.
            db.execute('BEGIN IMMEDIATE')
            if permission is not None:
                self._assert_mutation(db, **permission)
            try:
                db.execute('INSERT INTO actions VALUES (?,?,?,?)', (key, 'started', now(), '{}'))
            except sqlite3.IntegrityError:
                state, payload = db.execute('SELECT status,payload FROM actions WHERE id=?', (key,)).fetchone()
                if state == 'succeeded':
                    return json.loads(payload)
                error = UncertainAction if state in {'started', 'uncertain'} else RuntimeError
                raise error('نتیجهٔ یک اقدام قبلی قطعی نیست یا شکست خورده؛ پیش از تکرار وضعیت لیارا را بررسی کنید.')
        try:
            result = function()
        except Exception as error:
            state = 'uncertain' if getattr(error, 'uncertain', False) else 'failed'
            with self.connect() as db:
                db.execute('UPDATE actions SET status=?,updated=? WHERE id=?', (state, now(), key))
            raise
        with self.connect() as db:
            db.execute('UPDATE actions SET status=?,updated=?,payload=? WHERE id=?',
                       ('succeeded', now(), json.dumps(redact(result), ensure_ascii=False), key))
        return result
