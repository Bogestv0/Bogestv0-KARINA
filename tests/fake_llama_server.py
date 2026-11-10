"""Подделка llama-server для проверок: /health и /v1/chat/completions (потоком). Запуск: fake_llama_server.py --port N --api-key K ..."""
import argparse, json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int); ap.add_argument("--api-key"); ap.add_argument("-m"); ap.add_argument("--host")
a, _ = ap.parse_known_args()
START = time.time()


class H(BaseHTTPRequestHandler):
    def log_message(self, *x): pass

    def _auth(self):
        if self.headers.get("Authorization") != f"Bearer {a.api_key}":
            self.send_response(401); self.end_headers(); return False
        return True

    def do_GET(self):
        if not self._auth(): return
        if time.time() - START < 0.6:
            self.send_response(503); self.end_headers(); return
        b = b'{"status":"ok"}'
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        if not self._auth(): return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        last = body["messages"][-1]["content"]
        for piece in ["<think>секрет", " рассуждений</think>", "Ответ: ", last[:20]]:
            self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n").encode()); self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
