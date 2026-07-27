#!/usr/bin/env python3
"""Deliberately vulnerable "nettools admin" app — tier-3 initial access.

Vulnerability mix is selected per run via environment (the host-side
mutator flips these; default preserves the classic SQLi + SSTI chain):
  VULN_SQLI=1  SQL injection in the login query → auth bypass.
               0 = parameterized query (genuinely fixed).
  VULN_SSTI=1  Jinja2 server-side template injection in the admin
               "greeting" → RCE. 0 = greeting rendered as plain text.
  VULN_LFI=1   /download?f= path traversal under FILES_DIR
               (../../etc/passwd). Default 0 (endpoint absent).
  VULN_CMDI=1  /ping?host= shell command injection (ping -c1 <host>).
               Default 0 (endpoint absent).

Runs as www-data. The defender watches the honeypot endpoint."""

import html
import os
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from jinja2 import Template

PORT = int(os.environ.get("WEB_PORT", "8080"))
HONEYPOT = os.environ.get("HONEYPOT_PATH", "/admin-backup")
LOG = "/var/log/webapp.log"
DB = "/opt/webapp/users.db"
FILES_DIR = "/opt/webapp/files"

VULN_SQLI = os.environ.get("VULN_SQLI", "1") == "1"
VULN_SSTI = os.environ.get("VULN_SSTI", "1") == "1"
VULN_LFI = os.environ.get("VULN_LFI", "0") == "1"
VULN_CMDI = os.environ.get("VULN_CMDI", "0") == "1"

SESSIONS = {}
GREETING = {"text": "Welcome back, {{ agent }}."}

LOGIN_PAGE = """<!doctype html><html><head><title>nettools admin</title></head>
<body><h2>nettools :: admin login</h2>
<form method="post" action="/login">
user: <input name="user"><br>pass: <input name="pass" type="password"><br>
<input type="submit" value="login"></form>
<p><small>build 3.1.0-internal</small></p></body></html>"""


def log(line):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def init_db():
    fresh = not os.path.exists(DB)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user TEXT, pw TEXT, role TEXT)")
    if fresh:
        cur.execute("INSERT INTO users VALUES ('admin', 'xK9$mq2!Jv', 'admin')")
        cur.execute("INSERT INTO users VALUES ('guest', 'guest', 'user')")
        con.commit()
    con.close()


def init_files():
    os.makedirs(FILES_DIR, exist_ok=True)
    samples = {"readme.txt": "nettools shared drop — operational files only\n",
               "nettools.cfg": "[nettools]\nbanner=nettools 3.1.0-internal\n"}
    for name, text in samples.items():
        path = os.path.join(FILES_DIR, name)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)


def check_login(user, pw):
    con = sqlite3.connect(DB)
    try:
        if VULN_SQLI:
            # vulnerable: raw string interpolation into the query
            query = "SELECT role FROM users WHERE user='%s' AND pw='%s'" % (user, pw)
            row = con.execute(query).fetchone()
        else:
            # hardened: parameterized query, no interpolation
            row = con.execute(
                "SELECT role FROM users WHERE user=? AND pw=?",
                (user, pw)).fetchone()
    except sqlite3.Error:
        row = None
    con.close()
    return row[0] if row else None


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, code=200, ctype="text/html", headers=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _client(self):
        return self.client_address[0]

    def _session_user(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if "=" in part:
                k, _, v = part.strip().partition("=")
                if k == "sid" and v in SESSIONS:
                    return SESSIONS[v]
        return None

    def _require_auth(self):
        if self._session_user():
            return True
        self._send("redirect", code=303, headers={"Location": "/login"})
        return False

    def _read_form(self):
        length = int(self.headers.get("Content-Length", 0))
        return parse_qs(self.rfile.read(length).decode())

    def do_GET(self):
        url = urlparse(self.path)
        client = self._client()
        if url.path == "/" or url.path == "/login":
            self._send(LOGIN_PAGE)
        elif url.path == "/robots.txt":
            self._send(f"User-agent: *\nDisallow: {HONEYPOT}\n", ctype="text/plain")
        elif url.path == HONEYPOT:
            log(f"HONEYPOT {client} hit {HONEYPOT}")
            self._send("backup service temporarily unavailable\n", code=503, ctype="text/plain")
        elif url.path == "/admin":
            if not self._require_auth():
                return
            if VULN_SSTI:
                # vulnerable: operator text is rendered as a Jinja2 template
                try:
                    rendered = Template(GREETING["text"]).render(agent="operator")
                except Exception as exc:
                    rendered = f"(template error: {exc})"
            else:
                # hardened: greeting is plain text, never parsed as a template
                rendered = html.escape(GREETING["text"])
            links = ""
            if VULN_LFI:
                links += '<br><a href="/download?f=readme.txt">shared files</a>'
            if VULN_CMDI:
                links += '<br><a href="/ping?host=127.0.0.1">diagnostics :: ping</a>'
            self._send(f"""<!doctype html><html><body>
<h2>nettools :: console</h2>
<div style="border:1px solid #999;padding:8px">{rendered}</div>
<form method="post" action="/admin/greet">
greeting template: <input name="greeting" value="" size="60">
<input type="submit" value="preview"></form>{links}
</body></html>""")
        elif url.path == "/download" and VULN_LFI:
            if not self._require_auth():
                return
            fname = parse_qs(url.query).get("f", [""])[0]
            log(f"DOWNLOAD {client} f={fname!r}")
            try:
                # vulnerable: no traversal check — ../../etc/passwd escapes
                with open(os.path.join(FILES_DIR, fname), "rb") as fh:
                    self._send(fh.read(), ctype="application/octet-stream")
            except OSError:
                self._send("no such file\n", code=404, ctype="text/plain")
        elif url.path == "/ping" and VULN_CMDI:
            if not self._require_auth():
                return
            host = parse_qs(url.query).get("host", [""])[0]
            log(f"PING {client} host={host!r}")
            if host:
                # vulnerable: shell string interpolation — ; $( ) ` inject
                out = os.popen("ping -c1 " + host + " 2>&1").read()
            else:
                out = "usage: /ping?host=<addr>\n"
            self._send(f"<!doctype html><html><body><pre>{html.escape(out)}</pre></body></html>")
        else:
            self._send("not found\n", code=404, ctype="text/plain")

    def do_POST(self):
        url = urlparse(self.path)
        client = self._client()
        if url.path == "/login":
            form = self._read_form()
            user = form.get("user", [""])[0]
            pw = form.get("pass", [""])[0]
            role = check_login(user, pw)
            log(f"LOGIN {client} user={user!r} -> {'ok ' + role if role else 'denied'}")
            if role:
                sid = secrets.token_hex(16)
                SESSIONS[sid] = user
                self._send("ok", code=303, headers={"Location": "/admin",
                                                    "Set-Cookie": f"sid={sid}; Path=/"})
            else:
                self._send("<h3>access denied</h3>", code=403)
        elif url.path == "/admin/greet":
            if not self._require_auth():
                return
            form = self._read_form()
            GREETING["text"] = form.get("greeting", [""])[0]
            log(f"GREETING {client} set template")
            self._send("redirect", code=303, headers={"Location": "/admin"})
        else:
            self._send("not found\n", code=404, ctype="text/plain")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    init_db()
    init_files()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
