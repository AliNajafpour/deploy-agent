import json
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

    def write_once(self, key, function):
        with self.connect() as db:
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
