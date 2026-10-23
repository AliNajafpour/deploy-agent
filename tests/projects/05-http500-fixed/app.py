import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def average_cart(items):
    return sum(items) / len(items) if items else 0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'average':average_cart([])}).encode())


ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','3000'))), Handler).serve_forever()
