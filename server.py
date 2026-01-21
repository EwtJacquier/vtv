#!/usr/bin/env python3
"""
Servidor HTTP com autenticação básica e API de tempo.

Uso:
  python server.py [porta]
  # Default: python server.py 8080

Usuário: vtv
Senha: @@assistir
"""

import base64
import hashlib
import http.cookies
import json
import os
import secrets
import sys
from datetime import datetime
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

USERNAME = "vtv"
PASSWORD = "@@assistir"
TIMEZONE = "America/Sao_Paulo"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8099

# Token de acesso via URL (pode gerar um novo com: python -c "import secrets; print(secrets.token_urlsafe(32))")
AUTH_TOKEN = "vtv_acesso_secreto_2024"
# Nome do cookie de sessão
COOKIE_NAME = "vtv_session"
# Valor do cookie quando autenticado (derivado do token)
COOKIE_VALUE = hashlib.sha256(AUTH_TOKEN.encode()).hexdigest()[:32]


class AuthHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        self.base_directory = directory or os.getcwd()
        super().__init__(*args, directory=directory, **kwargs)

    def translate_path(self, path):
        """Override to follow symlinks outside the base directory."""
        # Decode URL and normalize
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]

        # Unquote URL-encoded characters
        try:
            from urllib.parse import unquote
            path = unquote(path, errors='surrogatepass')
        except (ValueError, UnicodeDecodeError):
            path = unquote(path)

        # Normalize path separators
        path = path.replace('/', os.sep)

        # Remove leading separator to make it relative
        while path.startswith(os.sep):
            path = path[1:]

        # Build the full path from base directory
        full_path = os.path.join(self.base_directory, path)

        # Resolve symlinks to get the actual file path
        resolved_path = os.path.realpath(full_path)

        return resolved_path

    def do_HEAD(self):
        if self.check_auth():
            super().do_HEAD()

    def do_GET(self):
        if not self.check_auth():
            return

        # API de tempo do servidor
        if self.path == "/api/time":
            self.send_time()
            return

        super().do_GET()

    def check_auth(self):
        # 1. Verificar cookie de sessão
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            cookies = http.cookies.SimpleCookie()
            try:
                cookies.load(cookie_header)
                if COOKIE_NAME in cookies and cookies[COOKIE_NAME].value == COOKIE_VALUE:
                    return True
            except Exception:
                pass

        # 2. Verificar token na URL (?auth=TOKEN)
        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)
        auth_param = query_params.get("auth", [None])[0]

        if auth_param == AUTH_TOKEN:
            # Token válido - marcar para setar cookie na resposta
            self._set_auth_cookie = True
            return True

        # 3. Verificar autenticação Basic
        auth_header = self.headers.get("Authorization")
        if auth_header and auth_header.startswith("Basic "):
            try:
                encoded = auth_header[6:]
                decoded = base64.b64decode(encoded).decode("utf-8")
                if ":" in decoded:
                    user, password = decoded.split(":", 1)
                    if user == USERNAME and password == PASSWORD:
                        self._set_auth_cookie = True
                        return True
            except Exception:
                pass

        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="VTV"')
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Acesso Bloqueado</h1>")
        return False

    def send_time(self):
        """Retorna o horário do servidor no timezone configurado"""
        tz = ZoneInfo(TIMEZONE)
        now = datetime.now(tz)

        data = {
            "timezone": TIMEZONE,
            "iso": now.isoformat(),
            "timestamp": now.timestamp(),
            "year": now.year,
            "month": now.month,
            "day": now.day,
            "hour": now.hour,
            "minute": now.minute,
            "second": now.second,
            "weekday": now.weekday(),  # 0=Monday, 6=Sunday
        }

        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Setar cookie de autenticação se necessário
        if getattr(self, "_set_auth_cookie", False):
            # Cookie válido por 30 dias
            cookie = f"{COOKIE_NAME}={COOKIE_VALUE}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Strict"
            self.send_header("Set-Cookie", cookie)
            self._set_auth_cookie = False

        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Range")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()


def main():
    directory = os.getcwd()
    handler = partial(AuthHandler, directory=directory)

    server = HTTPServer(("127.0.0.1", PORT), handler)
    print(f"VTV Server rodando em http://localhost:{PORT}")
    print(f"\nAutenticação:")
    print(f"  Usuário/Senha: {USERNAME} / {PASSWORD}")
    print(f"  URL direta: http://localhost:{PORT}/?auth={AUTH_TOKEN}")
    print(f"\nTimezone: {TIMEZONE}")
    print(f"Diretório: {directory}")
    print("\nAPI: /api/time - retorna horário do servidor")
    print("\nPressione Ctrl+C para parar")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
        server.shutdown()


if __name__ == "__main__":
    main()
