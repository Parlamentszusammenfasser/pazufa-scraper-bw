#!/usr/bin/env python3
"""
Mock PaZuFa backend server for local testing of pazufa-bawue-scraper.

Implements the collector write-API v2 endpoints:
  PUT /api/v2/vorgang
  PUT /api/v2/kalender/{parlament}/{datum}

Prints every incoming request in detail and tries to decode the X-API-Key
as a JWT (base64 payload decode, no signature verification).

Usage:
  python mock_pazufa_server.py [--port 8080]
"""

import argparse
import base64
import json
import re
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ──────────────────────────────────────────────────────────────────────────────
# JWT helpers (stdlib only — no signature verification, decode only)
# ──────────────────────────────────────────────────────────────────────────────


def _b64_decode(data: str) -> bytes:
    """Decode base64url without padding."""
    data += "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data)


def try_decode_jwt(token: str) -> dict | None:
    """
    Try to decode a string as a JWT.
    Returns a dict with 'header' and 'payload' on success, None otherwise.
    Does NOT verify the signature.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(_b64_decode(parts[0]))
        payload = json.loads(_b64_decode(parts[1]))
        return {"header": header, "payload": payload, "signature": parts[2][:8] + "..."}
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Pretty-print helpers
# ──────────────────────────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RED = "\033[91m"
MAGENTA = "\033[95m"
DIM = "\033[2m"


def _h(text: str, color: str) -> str:
    return f"{color}{BOLD}{text}{RESET}"


def print_request(method: str, path: str, headers: dict, body_raw: bytes, extra: dict | None = None):
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sep = "─" * 72

    print(f"\n{_h(sep, CYAN)}")
    print(f"{_h('▶ REQUEST', CYAN)}  {_h(ts, DIM)}")
    print(f"  {_h('Method:', BOLD)} {_h(method, YELLOW)}   {_h('Path:', BOLD)} {path}")

    # Headers
    print(f"\n  {_h('Headers:', BOLD)}")
    for key, val in sorted(headers.items()):
        if key.lower() in ("x-api-key",):
            print(f"    {key}: {_h(val[:20] + '...' if len(val) > 20 else val, MAGENTA)}")
        else:
            print(f"    {key}: {val}")

    # API Key analysis
    api_key = headers.get("X-API-Key") or headers.get("x-api-key")
    if api_key:
        jwt = try_decode_jwt(api_key)
        if jwt:
            print(f"\n  {_h('JWT (X-API-Key decoded):', GREEN)}")
            print(f"    Header:  {json.dumps(jwt['header'], ensure_ascii=False)}")
            print(f"    Payload: {json.dumps(jwt['payload'], indent=4, ensure_ascii=False)}")
            print(f"    Sig:     {jwt['signature']}")

            # Highlight scope/exp/sub
            payload = jwt["payload"]
            if "exp" in payload:
                exp_dt = datetime.fromtimestamp(payload["exp"], tz=UTC)
                now = datetime.now(UTC)
                expired = exp_dt < now
                status = _h("EXPIRED", RED) if expired else _h("valid", GREEN)
                print(f"    → exp: {exp_dt.isoformat()}  [{status}]")
            if "scope" in payload:
                scopes = payload["scope"]
                has_collector = "collector" in str(scopes)
                scope_status = (
                    _h("✓ collector scope present", GREEN) if has_collector else _h("✗ collector scope MISSING", RED)
                )
                print(f"    → scope: {scopes}  [{scope_status}]")
            if "sub" in payload:
                print(f"    → sub: {payload['sub']}")
        else:
            suffix = "..." if len(api_key) > 40 else ""
            print(f"\n  {_h('X-API-Key:', YELLOW)} (not a JWT — plain key) {api_key[:40]}{suffix}")
    else:
        print(f"\n  {_h('⚠ X-API-Key header MISSING', RED)}")

    # Scraper ID
    scraper_id = headers.get("X-Scraper-Id") or headers.get("x-scraper-id")
    if scraper_id:
        print(f"\n  {_h('X-Scraper-Id:', BOLD)} {scraper_id}")
    else:
        print(f"\n  {_h('⚠ X-Scraper-Id header MISSING', RED)}")

    # Extra path params
    if extra:
        print(f"\n  {_h('Path params:', BOLD)} {extra}")

    # Body
    if body_raw:
        print(f"\n  {_h('Body:', BOLD)} ({len(body_raw)} bytes)")
        try:
            parsed = json.loads(body_raw)
            pretty = json.dumps(parsed, indent=4, ensure_ascii=False)
            lines = pretty.splitlines()
            # Print first 60 lines to avoid flooding the terminal
            for line in lines[:60]:
                print(f"    {line}")
            if len(lines) > 60:
                print(f"    {DIM}... ({len(lines) - 60} more lines truncated){RESET}")
        except json.JSONDecodeError:
            print(f"    {DIM}(not JSON){RESET}")
            print(f"    {body_raw[:200]!r}")
    else:
        print(f"\n  {_h('Body:', BOLD)} (empty)")

    print(_h(sep, CYAN))


# ──────────────────────────────────────────────────────────────────────────────
# Request handler
# ──────────────────────────────────────────────────────────────────────────────

# Route patterns → (name, path_param_names)
ROUTES = [
    (re.compile(r"^/api/v2/vorgang$"), "vorgang_put", []),
    (re.compile(r"^/api/v2/kalender/(?P<parlament>[^/]+)/(?P<datum>[^/]+)$"), "kalender_put", ["parlament", "datum"]),
]


class MockPazufaHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress default access log; we have our own
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return self.rfile.read(length)
        return b""

    def _headers_dict(self) -> dict:
        return {k: v for k, v in self.headers.items()}

    def _respond(self, status: int, body: str = ""):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_PUT(self):
        path = self.path.split("?")[0]  # strip query string
        headers = self._headers_dict()
        body = self._read_body()

        for pattern, _name, _ in ROUTES:
            m = pattern.match(path)
            if m:
                extra = m.groupdict() or None
                print_request("PUT", path, headers, body, extra)
                # Validate required headers
                has_key = bool(headers.get("X-API-Key") or headers.get("x-api-key"))
                if not has_key:
                    print(f"  {_h('→ Responding 401 (missing X-API-Key)', RED)}\n")
                    self._respond(401, '{"detail": "Missing X-API-Key header"}')
                    return
                print(f"  {_h('→ Responding 201 Created', GREEN)}\n")
                self._respond(201)
                return

        # Unknown route
        print(f"\n{_h('✗ 404', RED)} PUT {path} (no matching route)")
        self._respond(404, f'{{"detail": "No mock route for PUT {path}"}}')

    def do_GET(self):
        """Health check endpoint."""
        if self.path == "/health":
            self._respond(200, '{"status": "ok", "mock": true}')
        else:
            self._respond(404, '{"detail": "not found"}')


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Mock PaZuFa backend server")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MockPazufaHandler)

    print(f"{_h('PaZuFa Mock Server', GREEN)}")
    print(f"  Listening on {_h(f'http://{args.host}:{args.port}', CYAN)}")
    print("  Endpoints:")
    print(f"    {YELLOW}PUT /api/v2/vorgang{RESET}")
    print(f"    {YELLOW}PUT /api/v2/kalender/{{parlament}}/{{datum}}{RESET}")
    print(f"    {DIM}GET /health{RESET}")
    print("\n  Configure scraper:")
    print(f"    {DIM}[backend]{RESET}")
    print(f'    {DIM}ltzf-api-url = "http://{args.host}:{args.port}"{RESET}')
    print(f'    {DIM}ltzf-api-key = "any-key-or-jwt"{RESET}')
    print("\nWaiting for requests... (Ctrl+C to stop)\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
