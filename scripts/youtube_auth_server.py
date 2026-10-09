"""Petit serveur web permanent pour réautoriser YouTube en un clic depuis Discord.

Contrairement à setup_youtube_auth.py (tunnel SSH, usage ponctuel), celui-ci
tourne en continu sur le VPS (voir youtube-auth-server.service), derrière
nginx qui gère le HTTPS (Let's Encrypt), et expose une URL publique cliquable.
Nécessite un client OAuth de type "Web application" (pas "Desktop") avec
comme redirect URI :
    https://shorts-auth.lainocs.fr/oauth2callback
"""
import os
import socketserver
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow

load_dotenv()

CLIENT_SECRET_FILE = "Client Secret Le Sais-Tu Web.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
PORT = 8765  # bind local, derrière nginx
PUBLIC_HOST = os.environ["REAUTH_PUBLIC_HOST"]
REDIRECT_URI = f"https://{PUBLIC_HOST}/oauth2callback"
SECRET_KEY = os.environ["REAUTH_SECRET"]

_pending_flow = {}


def _make_flow() -> Flow:
    return Flow.from_client_secrets_file(CLIENT_SECRET_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _html(self, status: int, body: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/start":
            if params.get("key", [None])[0] != SECRET_KEY:
                self._html(403, "<h2>Lien invalide.</h2>")
                return
            flow = _make_flow()
            auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            _pending_flow["flow"] = flow
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()

        elif parsed.path == "/oauth2callback":
            flow = _pending_flow.get("flow")
            if flow is None:
                self._html(400, "<h2>Session expirée, relance depuis Discord.</h2>")
                return
            flow.fetch_token(authorization_response=f"https://{PUBLIC_HOST}{self.path}")
            Path(TOKEN_FILE).write_text(flow.credentials.to_json(), encoding="utf-8")
            self._html(200, "<h2>Token YouTube régénéré. Tu peux fermer cette page.</h2>")

        else:
            self._html(404, "<h2>404</h2>")


class Server(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with Server(("127.0.0.1", PORT), Handler) as httpd:
        print(f"Serveur de réauth sur le port {PORT} (local), lien public: https://{PUBLIC_HOST}/start?key=***")
        httpd.serve_forever()
