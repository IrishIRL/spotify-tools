import base64
import json
import urllib.parse
import urllib.request
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

from libs import app_logger as log


class CallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    auth_error = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        CallbackHandler.auth_code = params.get("code", [None])[0]
        CallbackHandler.auth_error = params.get("error", [None])[0]

        self.send_response(200)
        self.end_headers()

        if CallbackHandler.auth_code:
            self.wfile.write(b"Spotify authorization complete. You can close this window.")
        else:
            self.wfile.write(b"Spotify authorization failed. Check the terminal.")

    def log_message(self, format, *args):
        return


def parse_redirect_server(redirect_uri):
    parsed = urllib.parse.urlparse(redirect_uri)

    if parsed.scheme != "http":
        raise RuntimeError("Redirect URI must use http for local development.")

    if not parsed.hostname:
        raise RuntimeError("Redirect URI must include a hostname.")

    if parsed.port is None:
        raise RuntimeError("Redirect URI must include a port, for example :8888.")

    if parsed.hostname != "127.0.0.1":
        log.warn("For local Spotify scripts, 127.0.0.1 is usually recommended.")

    return parsed.hostname, parsed.port


def get_authorization_code(client_id, redirect_uri, scope):
    CallbackHandler.auth_code = None
    CallbackHandler.auth_error = None

    host, port = parse_redirect_server(redirect_uri)

    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
    })

    server = HTTPServer((host, port), CallbackHandler)

    log.info("Opening Spotify login in your browser...")
    log.debug(auth_url)

    webbrowser.open(auth_url)
    server.handle_request()

    if CallbackHandler.auth_error:
        raise RuntimeError(f"Spotify authorization failed: {CallbackHandler.auth_error}")

    if not CallbackHandler.auth_code:
        raise RuntimeError("Spotify authorization failed: no authorization code received.")

    return CallbackHandler.auth_code


def get_access_token(client_id, client_secret, redirect_uri, auth_code):
    credentials = f"{client_id}:{client_secret}".encode()
    basic_auth = base64.b64encode(credentials).decode()

    request = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": redirect_uri,
        }).encode(),
        headers={
            "Authorization": f"Basic {basic_auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        token_data = json.loads(response.read())

    return token_data["access_token"]


def get_spotify_access_token(client_id, client_secret, redirect_uri, scope):
    auth_code = get_authorization_code(client_id, redirect_uri, scope)

    return get_access_token(
        client_id,
        client_secret,
        redirect_uri,
        auth_code,
    )


def spotify_get(url, access_token):
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())