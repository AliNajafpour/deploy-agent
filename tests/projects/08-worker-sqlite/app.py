import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from storage import database

started=time.time()

class Handler(BaseHTTPRequestHandler):
    def reply(self,status,payload):
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def do_GET(self):
        with database() as db:
            if self.path.startswith('/jobs/'):
                try:
                    job=db.execute('SELECT * FROM jobs WHERE id=?',(int(self.path.rsplit('/',1)[1]),)).fetchone()
                    return self.reply(200 if job else 404,dict(job) if job else {'error':'missing job'})
                except ValueError:
                    return self.reply(400,{'error':'invalid job id'})
            row=db.execute('SELECT updated FROM heartbeat WHERE id=1').fetchone()
        healthy=bool(row and started<=row['updated']<=time.time() and time.time()-row['updated']<2)
        self.reply(200 if healthy else 503,{'status':'ok' if healthy else 'worker unavailable'})

    def do_POST(self):
        if self.path!='/jobs':
            return self.reply(404,{'error':'unknown endpoint'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=1024:
                return self.reply(400,{'error':'invalid body size'})
            value=json.loads(self.rfile.read(length))['value']
            if type(value) is not int or not -1000000<=value<=1000000:
                return self.reply(400,{'error':'value must be a bounded integer'})
        except (ValueError,KeyError,TypeError):
            return self.reply(400,{'error':'invalid JSON'})
        with database() as db:
            job_id=db.execute("INSERT INTO jobs(value,status) VALUES (?,'pending')",(value,)).lastrowid
        self.reply(201,{'id':job_id,'status':'pending'})


ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','3000'))),Handler).serve_forever()
