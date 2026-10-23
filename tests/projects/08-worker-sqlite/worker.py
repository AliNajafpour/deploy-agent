import os
import time
from storage import database

if os.environ.get('WORKER_ENABLED','1') == '0':
    raise SystemExit('WorkerDisabled: jobs cannot complete until the worker is enabled')
print('SQLite worker started',flush=True)
while True:
    with database() as db:
        db.execute('INSERT OR REPLACE INTO heartbeat VALUES (1,?)',(time.time(),))
        for job in db.execute("SELECT * FROM jobs WHERE status='pending'").fetchall():
            db.execute("UPDATE jobs SET status='done',result=? WHERE id=?",(job['value']**2,job['id']))
            print('Completed job '+str(job['id']),flush=True)
    time.sleep(.05)
