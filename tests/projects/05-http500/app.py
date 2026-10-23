import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def average_cart(items):
    # Intentional bug: an empty cart raises ZeroDivisionError.
    return sum(items) / len(items)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            result = {'average': average_cart([])}
            status = 200
        except ZeroDivisionError:
            traceback.print_exc()
            result, status = {'error':'checkout calculation failed'}, 500
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','3000'))), Handler).serve_forever()
