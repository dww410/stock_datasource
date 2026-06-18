#!/usr/bin/env python3
"""Transparent proxy that pads nodeExtract with defaults for WeKnora initialization/config API."""
import json, re, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError

TARGET = "http://weknora-app:8080"
PATTERN = re.compile(r"^/api/v1/initialization/config/")

DEFAULT_NODES = [{"name": "Company", "type": "entity"}, {"name": "Person", "type": "entity"}]
DEFAULT_RELATIONS = [{"name": "works_for", "type": "relation"}]
DEFAULT_TEXT = "Based on the given text, complete the information extraction task following these steps, ensuring clear logic and complete, accurate information. Step 1: Extract core entities and enrich entity attributes. Step 2: Identify relationship types and extract valid relationships."

class ProxyHandler(BaseHTTPRequestHandler):
    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        
        # For initialization/config, pad nodeExtract defaults
        if PATTERN.match(self.path):
            try:
                data = json.loads(body)
                if "nodeExtract" in data and data["nodeExtract"].get("enabled", False):
                    ne = data["nodeExtract"]
                    if not ne.get("nodes"):
                        ne["nodes"] = DEFAULT_NODES
                    if not ne.get("relations"):
                        ne["relations"] = DEFAULT_RELATIONS
                    if not ne.get("text"):
                        ne["text"] = DEFAULT_TEXT
                    body = json.dumps(data)
            except json.JSONDecodeError:
                pass
        
        # Forward to weknora-app
        url = TARGET + self.path
        req = Request(url, data=body.encode("utf-8"), method="PUT")
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length"):
                req.add_header(k, v)
        
        try:
            resp = urlopen(req, timeout=10)
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(resp.read())
        except URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        url = TARGET + self.path
        req = Request(url, data=body.encode("utf-8"), method="POST")
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length"):
                req.add_header(k, v)
        try:
            resp = urlopen(req, timeout=10)
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
            self.end_headers()
            self.wfile.write(resp.read())
        except URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_GET(self):
        self._forward_request("GET")
    
    def do_DELETE(self):
        self._forward_request("DELETE")
    
    def _forward_request(self, method):
        url = TARGET + self.path
        req = Request(url, method=method)
        for k, v in self.headers.items():
            if k.lower() not in ("host",):
                req.add_header(k, v)
        try:
            resp = urlopen(req, timeout=10)
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.read())
        except URLError as e:
            self.send_response(502)
            self.end_headers()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18090
    server = HTTPServer(("0.0.0.0", port), ProxyHandler)
    print(f"Proxy listening on 0.0.0.0:{port}, forwarding to {TARGET}")
    server.serve_forever()
