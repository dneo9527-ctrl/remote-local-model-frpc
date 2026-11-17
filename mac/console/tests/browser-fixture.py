"""Local, credential-free streaming fixture for browser UI checks."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import sys
import threading
import time

from app.server import Handler


class Model(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        body = json.dumps({'models':[{'name':'qwen3-8b-8k:latest'}]}).encode()
        self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200); self.send_header('Content-Type','application/x-ndjson'); self.end_headers()
        for event in [{'message':{'thinking':'fixture thinking'}}, {'message':{'content':'网页'}},
                      {'message':{'content':'连接成功'}}, {'done':True}]:
            self.wfile.write((json.dumps(event,ensure_ascii=False)+'\n').encode());self.wfile.flush();time.sleep(.08)


if __name__ == '__main__':
    up = ThreadingHTTPServer(('127.0.0.1',0),Model)
    threading.Thread(target=up.serve_forever,daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.authority = f'127.0.0.1:{server.server_port}'
    server.token = secrets.token_urlsafe(32)
    server.controller = type('Fixture',(),{'config':{'ollama':{'url':f'http://127.0.0.1:{up.server_port}'}}})()
    print('http://'+server.authority,flush=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    sys.stdin.read()
    server.shutdown();server.server_close();up.shutdown();up.server_close()
