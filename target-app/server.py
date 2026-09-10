#!/usr/bin/env python3
"""Riverbend Credit Union - Back Office Console (mock automation target).

A deliberately small stand-in for a legacy bank back-office web app, meant to be
driven by a computer-use agent. It has a lot of clickable surface, but almost all
of it is "bait" that leads to errors, permission denials, or dead ends. Exactly
one real multi-step path works end to end (see README.md).

No third-party dependencies. Python 3.8+ only.

    python3 server.py            # http://localhost:8000

Environment variables:
    PORT          listen port (default 8000)
    SESSION_TTL   session lifetime in seconds (default 1800); lower it to
                  exercise session-timeout handling
    LATENCY_MS    artificial delay on member search (default 0); raise it to
                  exercise wait/retry handling
"""
import os
import html
import time
import uuid
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8000"))
SESSION_TTL = int(os.environ.get("SESSION_TTL", "1800"))
LATENCY_MS = int(os.environ.get("LATENCY_MS", "0"))

VALID_USER = ("operator", "password123")
SESSIONS = {}  # sid -> {"user": str, "created": float}

# Deterministic fixtures. The behavior table in README.md is the contract.
MEMBERS = {
    "10042": {"name": "Sarah Chen", "status": "Active", "joined": "2016-03-11",
              "savings": "$4,182.55", "checking": "$1,203.10"},
    "10077": {"name": "James Okafor", "status": "Active", "joined": "2011-09-02",
              "savings": "$18,940.00", "checking": "$342.88"},
    "10099": {"name": "Maria Delgado", "status": "Active", "joined": "2020-01-27",
              "savings": "$902.14", "checking": "$77.40", "compliance_hold": True},
}
RESTRICTED = {"10043"}
LOCKED = {"10044"}

PUBLIC_PATHS = {"/", "/login", "/logout", "/favicon.ico"}

NAV_ITEMS = [
    ("/dashboard", "Dashboard"),
    ("/members/lookup", "Member Lookup"),
    ("/accounts", "Accounts"),
    ("/transactions", "Transactions"),
    ("/wires", "Wire Transfers"),
    ("/loans", "Loan Servicing"),
    ("/cards", "Card Management"),
    ("/reports", "Reports"),
    ("/audit", "Audit Log"),
    ("/admin", "Admin Console"),
    ("/settings", "Settings"),
]

# path -> (box kind, http status, message)
BAIT = {
    "/accounts": ("info", 200, "The Accounts module is not available in the demo environment."),
    "/transactions": ("info", 200, "The Transactions module is not available in the demo environment."),
    "/wires": ("error", 403, "Access denied: your role (Operator) is not permitted to open Wire Transfers."),
    "/loans": ("error", 500, "System Error TXN-500. Reference ID 7731. Please contact IT support."),
    "/cards": ("info", 200, "The Card Management module is not available in the demo environment."),
    "/reports": ("warn", 503, "The reporting service is starting up. Please retry in a few minutes."),
    "/audit": ("error", 403, "Access denied: your role (Operator) is not permitted to view the Audit Log."),
    "/admin": ("error", 403, "Access denied: administrator privileges required."),
}

STYLE = """
* { box-sizing: border-box; }
body { margin:0; font-family:-apple-system,Segoe UI,Roboto,sans-serif; background:#f4f5f7; color:#1f2933; }
a { color:#2b6cb0; text-decoration:none; }
.topbar { background:#1a365d; color:#fff; display:flex; align-items:center; gap:14px; padding:10px 18px; }
.topbar .brand { font-weight:700; letter-spacing:.5px; }
.topbar form { margin-left:auto; display:flex; gap:6px; }
.topbar input { padding:5px 8px; border:0; border-radius:3px; }
.topbar .user { font-size:13px; opacity:.9; }
.wrap { display:flex; min-height:calc(100vh - 44px); }
.sidebar { width:210px; background:#fff; border-right:1px solid #e2e8f0; padding:12px 0; }
.sidebar a { display:block; padding:9px 18px; color:#334e68; }
.sidebar a:hover { background:#ebf4ff; }
.content { flex:1; padding:24px 28px; }
h1 { font-size:20px; margin:0 0 16px; }
.grid { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:22px; }
.card { background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:14px; }
.card .n { font-size:24px; font-weight:700; }
.card .l { color:#627d98; font-size:13px; margin-bottom:8px; }
table { border-collapse:collapse; width:100%; background:#fff; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid #e2e8f0; font-size:14px; }
.btn { display:inline-block; padding:6px 12px; border-radius:4px; background:#2b6cb0; color:#fff; border:0; cursor:pointer; font-size:13px; }
.btn.secondary { background:#e2e8f0; color:#334e68; }
.box { padding:14px 16px; border-radius:6px; margin-bottom:16px; border:1px solid; }
.box.error { background:#fff5f5; border-color:#feb2b2; color:#9b2c2c; }
.box.warn { background:#fffaf0; border-color:#fbd38d; color:#975a16; }
.box.info { background:#ebf8ff; border-color:#90cdf4; color:#2c5282; }
.box.ok { background:#f0fff4; border-color:#9ae6b4; color:#276749; }
form.lookup { background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:18px; max-width:420px; }
form.lookup input[type=text] { width:100%; padding:8px; border:1px solid #cbd5e0; border-radius:4px; margin:6px 0 12px; }
dl.detail { background:#fff; border:1px solid #e2e8f0; border-radius:6px; padding:16px 20px; max-width:480px; }
dl.detail dt { color:#627d98; font-size:12px; text-transform:uppercase; margin-top:10px; letter-spacing:.4px; }
dl.detail dd { margin:2px 0 0; font-size:16px; }
.actions { margin-top:16px; display:flex; gap:8px; flex-wrap:wrap; }
"""


def box(kind, msg):
    return '<div class="box %s">%s</div>' % (kind, msg)


def layout(title, content, user=None, sidebar=True):
    nav = ""
    if sidebar:
        nav = '<nav class="sidebar">' + "".join(
            '<a href="%s">%s</a>' % (h, html.escape(t)) for h, t in NAV_ITEMS
        ) + "</nav>"
    quick = userbox = ""
    if user:
        quick = ('<form method="post" action="/quicksearch">'
                 '<input type="text" name="q" placeholder="Quick search...">'
                 '<button class="btn" type="submit">Go</button></form>')
        userbox = ('<span class="user">%s &nbsp;|&nbsp; '
                   '<a href="/logout" style="color:#fff">Sign out</a></span>'
                   % html.escape(user))
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>%s - Riverbend Console</title><style>%s</style></head><body>"
        "<div class='topbar'><span class='brand'>RIVERBEND CU</span>"
        "<span style='font-size:13px;opacity:.8'>Back Office Console</span>%s%s</div>"
        "<div class='wrap'>%s<main class='content'>%s</main></div>"
        "</body></html>"
    ) % (html.escape(title), STYLE, quick, userbox, nav, content)


def login_page(error=None, expired=False):
    msg = ""
    if expired:
        msg = box("info", "Your session has expired. Please sign in again.")
    if error:
        msg = box("error", html.escape(error))
    inp = "width:100%;padding:8px;margin:6px 0 12px;border:1px solid #cbd5e0;border-radius:4px"
    content = (
        "<div style='max-width:360px;margin:8vh auto;background:#fff;border:1px solid "
        "#e2e8f0;border-radius:8px;padding:26px'><h1>Sign in</h1>" + msg +
        "<form method='post' action='/login'>"
        "<label>Username</label><input type='text' name='username' style='%s'>"
        "<label>Password</label><input type='password' name='password' style='%s'>"
        "<button class='btn' type='submit'>Sign in</button></form></div>"
    ) % (inp, inp)
    return layout("Sign in", content, user=None, sidebar=False)


def dashboard_page(user):
    cards = [
        ("Pending Approvals", "3", "/wires", "Review"),
        ("Today's Transactions", "1,204", "/transactions", "Open"),
        ("New Applications", "5", "/accounts", "Process"),
        ("System Notices", "1", "/reports", "View"),
        ("Batch Jobs", "2", "/loans", "Run"),
        ("Member Lookup", "-", "/members/lookup", "Open"),
    ]
    grid = '<div class="grid">' + "".join(
        '<div class="card"><div class="l">%s</div><div class="n">%s</div>'
        '<a class="btn" href="%s">%s</a></div>' % c for c in cards
    ) + "</div>"
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td><a href='%s'>view</a></td></tr>" % r
        for r in [
            ("09:42", "Member 10042 balance inquiry", "Sarah Chen", "/members/result?member_id=10042"),
            ("09:31", "Wire release queued", "J. Okafor", "/wires"),
            ("09:12", "Card reissue", "M. Delgado", "/cards"),
            ("08:57", "Batch posting complete", "system", "/reports"),
        ]
    )
    table = ("<h1 style='font-size:16px'>Recent Activity</h1><table><thead><tr>"
             "<th>Time</th><th>Event</th><th>Member</th><th></th></tr></thead>"
             "<tbody>%s</tbody></table>" % rows)
    return layout("Dashboard", "<h1>Dashboard</h1>" + grid + table, user)


def lookup_page(user, error=None):
    msg = box("error", html.escape(error)) if error else ""
    content = (
        "<h1>Member Lookup</h1>" + msg +
        "<form class='lookup' method='get' action='/members/result'>"
        "<label>Member ID</label>"
        "<input type='text' name='member_id' placeholder='e.g. 10042'>"
        "<button class='btn' type='submit'>Search</button></form>"
        "<p style='color:#627d98;font-size:13px;margin-top:14px'>Recent: "
        "<a href='/members/result?member_id=10042'>10042</a>, "
        "<a href='/members/result?member_id=10077'>10077</a>, "
        "<a href='/members/result?member_id=10099'>10099</a></p>"
    )
    return layout("Member Lookup", content, user)


def compliance_page(user, mid):
    mid = html.escape(mid)
    content = (
        "<h1>Compliance Notice</h1>" +
        box("warn", "Account %s is under a routine compliance review. Access is "
                    "logged. Acknowledge to continue." % mid) +
        "<div class='actions'>"
        "<a class='btn' href='/members/%s?ack=1'>Acknowledge and continue</a>"
        "<a class='btn secondary' href='/members/lookup'>Cancel</a></div>" % mid
    )
    return layout("Compliance Notice", content, user)


def member_detail_page(user, mid, m):
    e = html.escape
    rows = [
        ("Member ID", mid), ("Name", m["name"]), ("Status", m["status"]),
        ("Member Since", m["joined"]), ("Savings Balance", m["savings"]),
        ("Checking Balance", m["checking"]),
    ]
    dl = "<dl class='detail'>" + "".join(
        "<dt>%s</dt><dd>%s</dd>" % (e(k), e(v)) for k, v in rows
    ) + "</dl>"
    acts = ("<div class='actions'>"
            "<a class='btn secondary' href='/members/%s/statement'>Export Statement</a>"
            "<a class='btn secondary' href='/members/%s/hold'>Place Account Hold</a>"
            "<a class='btn secondary' href='/members/%s/close'>Close Account</a></div>"
            % (e(mid), e(mid), e(mid)))
    head = "<h1>Member %s - %s</h1>" % (e(mid), e(m["name"]))
    return layout("Member %s" % mid, head + dl + acts, user)


def settings_page(user, saved=False):
    msg = box("ok", "Settings saved. (Demo mode: changes are not persisted.)") if saved else ""
    content = (
        "<h1>Settings</h1>" + msg +
        "<form method='post' action='/settings' style='background:#fff;border:1px solid "
        "#e2e8f0;border-radius:6px;padding:18px;max-width:420px'>"
        "<label><input type='checkbox' name='email_alerts'> Email alerts</label><br><br>"
        "<label><input type='checkbox' name='dark_mode'> Dark mode</label><br><br>"
        "<label><input type='checkbox' name='compact'> Compact tables</label><br><br>"
        "<button class='btn' type='submit'>Save</button></form>"
    )
    return layout("Settings", content, user)


def get_user(handler):
    raw = handler.headers.get("Cookie")
    if not raw:
        return None
    jar = SimpleCookie(raw)
    if "sid" not in jar:
        return None
    s = SESSIONS.get(jar["sid"].value)
    if not s:
        return None
    if time.time() - s["created"] > SESSION_TTL:
        SESSIONS.pop(jar["sid"].value, None)
        return None
    return s["user"]


class App(BaseHTTPRequestHandler):
    server_version = "RiverbendConsole/1.0"

    # -- helpers ------------------------------------------------------------
    def _send(self, body, status=200, headers=None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location, headers=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()

    def _page(self, *args, **kw):
        status = kw.pop("status", 200)
        self._send(layout(*args, **kw), status=status)

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)
        user = get_user(self)

        if path == "/favicon.ico":
            return self._send("", status=204)
        if path == "/":
            return self._redirect("/dashboard" if user else "/login")
        if path == "/login":
            return self._send(login_page(expired="expired" in qs))
        if path == "/logout":
            raw = self.headers.get("Cookie")
            if raw:
                jar = SimpleCookie(raw)
                if "sid" in jar:
                    SESSIONS.pop(jar["sid"].value, None)
            return self._redirect("/login", headers=[
                ("Set-Cookie", "sid=; Path=/; Max-Age=0")])

        if not user:
            return self._redirect("/login?expired=1")

        if path == "/dashboard":
            return self._send(dashboard_page(user))
        if path == "/members/lookup":
            return self._send(lookup_page(user))
        if path == "/members/result":
            return self._member_result(user, qs)
        if path.startswith("/members/"):
            parts = path.strip("/").split("/")
            mid = parts[1]
            action = parts[2] if len(parts) > 2 else None
            return self._member(user, mid, action, qs)
        if path in BAIT:
            kind, status, msg = BAIT[path]
            title = path.strip("/").title()
            return self._send(
                layout(title, "<h1>%s</h1>%s" % (html.escape(title), box(kind, html.escape(msg))), user),
                status=status)
        if path == "/settings":
            return self._send(settings_page(user))
        return self._send(layout("Not Found", "<h1>404</h1><p>No such page.</p>", user), status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        form = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

        if path == "/login":
            if (form.get("username"), form.get("password")) == VALID_USER:
                sid = uuid.uuid4().hex
                SESSIONS[sid] = {"user": form["username"], "created": time.time()}
                return self._redirect("/dashboard", headers=[
                    ("Set-Cookie", "sid=%s; Path=/; HttpOnly" % sid)])
            return self._send(login_page(error="Invalid username or password."), status=401)

        user = get_user(self)
        if not user:
            return self._redirect("/login?expired=1")
        if path == "/quicksearch":
            return self._send(
                layout("Quick Search", "<h1>Quick Search</h1>" +
                       box("error", "Quick Search is disabled in this environment."), user),
                status=503)
        if path == "/settings":
            return self._send(settings_page(user, saved=True))
        return self._send(layout("Not Found", "<h1>404</h1>", user), status=404)

    # -- member flow ----------------------------------------------------
    def _member_result(self, user, qs):
        if LATENCY_MS:
            time.sleep(LATENCY_MS / 1000.0)
        mid = qs.get("member_id", [""])[0].strip()
        if not mid:
            return self._send(lookup_page(user, error="Enter a member ID."), status=400)
        if not mid.isdigit():
            return self._send(
                lookup_page(user, error="Member ID must be numeric (e.g. 10042)."),
                status=400)
        return self._member(user, mid, None, {})

    def _member(self, user, mid, action, qs):
        e = html.escape
        if mid in RESTRICTED:
            return self._send(layout("Restricted", "<h1>Restricted</h1>" + box(
                "error", "Member %s is restricted. Contact a supervisor to view "
                         "this account." % e(mid)), user), status=403)
        if mid in LOCKED:
            return self._send(layout("Record Locked", "<h1>Record Locked</h1>" + box(
                "warn", "Member %s is locked by another operator (OP-2214). "
                        "Try again later." % e(mid)), user), status=409)
        m = MEMBERS.get(mid)
        if not m:
            return self._send(layout("Not Found", "<h1>Not Found</h1>" + box(
                "info", "No member found with ID %s." % e(mid)), user), status=404)

        if action in ("statement", "hold"):
            return self._send(layout("Action Unavailable", "<h1>Action Unavailable</h1>" + box(
                "warn", "This action requires supervisor approval and is disabled "
                        "in the demo environment."), user), status=403)
        if action == "close":
            if qs.get("confirm", ["0"])[0] == "1":
                return self._send(layout("Blocked", "<h1>Blocked</h1>" + box(
                    "error", "Destructive actions (account closure) are disabled "
                             "in this environment."), user), status=403)
            content = (box("error", "Closing an account is irreversible and will "
                                    "terminate all sub-accounts for member %s." % e(mid)) +
                       "<div class='actions'>"
                       "<a class='btn' href='/members/%s/close?confirm=1'>Confirm closure</a>"
                       "<a class='btn secondary' href='/members/%s'>Cancel</a></div>"
                       % (e(mid), e(mid)))
            return self._send(layout("Confirm Closure", "<h1>Confirm Closure</h1>" + content, user))

        if m.get("compliance_hold") and qs.get("ack", ["0"])[0] != "1":
            return self._send(compliance_page(user, mid))
        return self._send(member_detail_page(user, mid, m))


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), App)
    print("Riverbend Console on http://localhost:%d  (Ctrl-C to stop)" % PORT)
    print("Login: %s / %s" % VALID_USER)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
