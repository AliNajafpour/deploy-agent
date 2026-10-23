import os
import colorama
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/__lyara_test_stop":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"bye")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"missing dependency repaired")

HTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
