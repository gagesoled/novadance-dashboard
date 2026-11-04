"""
A stand-in for www.beposfdn.org used by the tests. It copies the BEPOS
behaviour observed on the live site on 2026-11-03:

* GET  /bepos_login.aspx           login form with hidden ASP.NET fields
* POST /bepos_login.aspx           sets a session cookie on good credentials
* GET  /bepos_admin.aspx           302 to bepositive.org when signed out
* POST /bepos_admin.aspx           action=reportteams -> Team Report CSV
                                   (same 15 columns, "$1,234.00" money,
                                   blank + dashes + "N Teams" total row)
* Date filter quirk: a gift counts when entry_date >= start AND
  received_date <= end, so a gift entered one day and received another
  is missing from narrow windows (the real $45 on 8/25 -> 9/2 case).
* Captain columns are filled with fake secrets so the tests can prove
  they never reach the output.
"""
from __future__ import annotations

import csv
import io
import json
import threading
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

GOOD_EMAIL = "admin@example.org"
GOOD_PASSWORD = "correct-horse"
FAKE_SECRET = "FAKE-CAPTAIN-PASSWORD-123"

HEADER = ["Team Name", "Affliation", "Captain First", "Captain Last",
          "Captain E-Mail", "Captain Password", "Captain Phone",
          "# Participants", "# Supporters", "Frat/Sor", "Team Goal",
          "Money Due", "Money Received", "Offline", "% of Goal"]


def fmt(v: float) -> str:
    return f"${v:,.2f}"


def parse_mdy(s: str) -> date | None:
    if not s:
        return None
    m, d, y = (int(x) for x in s.split("/"))
    return date(y, m, d)


class State:
    def __init__(self, teams: list[dict], gifts: list[dict], expire_after: int | None = None):
        self.teams = teams          # [{team, affiliation, participants, supporters}]
        self.gifts = gifts          # [{team, entered, received_on, amount, due}]
        self.sessions: set[str] = set()
        self.expire_after = expire_after
        self.report_calls = 0
        self.logins = 0

    def team_csv(self, start: date | None, end: date | None) -> str:
        sums: dict[str, list[float]] = {}
        for g in self.gifts:
            if start and g["entered"] < start:
                continue
            if end and g["received_on"] > end:
                continue
            s = sums.setdefault(g["team"], [0.0, 0.0])
            s[1 if g["due"] else 0] += g["amount"]
        buf = io.StringIO()
        w = csv.writer(buf, quoting=csv.QUOTE_ALL)
        w.writerow(HEADER)
        tot = [0, 0, 0.0, 0.0, 0.0]
        shown = 0
        for t in self.teams:
            if (start or end) and t["team"] not in sums:
                continue  # BEPOS omits teams with no money in a dated window
            rec, due = sums.get(t["team"], [0.0, 0.0])
            shown += 1
            tot[0] += t["participants"]; tot[1] += t["supporters"]
            tot[2] += 10000; tot[3] += due; tot[4] += rec
            w.writerow([t["team"], t["affiliation"], "Fake", "Captain",
                        "captain@example.com", FAKE_SECRET, "555-0100",
                        t["participants"], t["supporters"], "", fmt(10000),
                        fmt(due), fmt(rec), fmt(0), f"{rec / 100:.1f}"])
        w.writerow([""] * len(HEADER))
        w.writerow(["----------------"] + [""] * (len(HEADER) - 1))
        label = f"{shown} Team" if shown == 1 else f"{shown} Teams"
        w.writerow([label, "", "", "", "", "", "", tot[0], tot[1], "",
                    fmt(tot[2]), fmt(tot[3]), fmt(tot[4]), fmt(0), "0"])
        return buf.getvalue()


def make_handler(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep test output quiet
            pass

        def _session(self) -> str | None:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "ASP.NET_SessionId" and v in state.sessions:
                    return v
            return None

        def _send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _form(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return {k: v[-1] for k, v in parse_qs(self.rfile.read(n).decode(), keep_blank_values=True).items()}

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/bepos_login.aspx":
                html = ('<form method="post" action="bepos_login.aspx" id="ctl00">'
                        '<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="dGVzdA==" />'
                        '<input type="hidden" name="__VIEWSTATEGENERATOR" value="C2EE9ABB" />'
                        '<input type="hidden" name="action" id="action" value="" />'
                        '<input type="hidden" name="eventtag" value="NOVAdance27" />'
                        '<input type="text" name="email"/><input type="password" name="password"/>'
                        '<input type="submit" name="submit" value=""/></form>')
                return self._send(200, html.encode())
            if path == "/bepos_admin.aspx":
                if not self._session():
                    return self._send(302, headers={"Location": "https://www.bepositive.org/"})
                return self._send(200, b"<h2>Administrative Interface</h2>")
            return self._send(404)

        def do_POST(self):
            path = urlparse(self.path).path
            form = self._form()
            if path == "/bepos_login.aspx":
                ok = (form.get("action") == "submit" and form.get("email") == GOOD_EMAIL
                      and form.get("password") == GOOD_PASSWORD and "__VIEWSTATE" in form)
                if not ok:
                    return self._send(200, b"<p>Invalid login</p>")
                sid = uuid.uuid4().hex
                state.sessions.add(sid)
                state.logins += 1
                return self._send(302, headers={"Location": "/bepos_index.aspx?eventtag=NOVAdance27",
                                               "Set-Cookie": f"ASP.NET_SessionId={sid}; path=/; HttpOnly"})
            if path == "/bepos_admin.aspx":
                sid = self._session()
                if not sid or form.get("eventtag") != "NOVAdance27":
                    return self._send(302, headers={"Location": "https://www.bepositive.org/"})
                if form.get("action") != "reportteams":
                    return self._send(200, b"<h2>Administrative Interface</h2>")
                state.report_calls += 1
                if state.expire_after and state.report_calls % state.expire_after == 0:
                    state.sessions.discard(sid)  # simulate session timeout
                body = state.team_csv(parse_mdy(form.get("teamstart", "")),
                                      parse_mdy(form.get("teamend", ""))).encode()
                return self._send(200, body, "text/csv",
                                  {"Content-Disposition": "attachment; filename=TeamReport.csv"})
            return self._send(404)
    return H


def serve(state: State) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def state_from_fixture(path: str, **kw) -> State:
    fx = json.load(open(path))
    return State(
        teams=fx["teams"],
        gifts=[{"team": g[0], "entered": date.fromisoformat(g[1]),
                "received_on": date.fromisoformat(g[2]), "amount": g[3], "due": bool(g[4])}
               for g in fx["gifts"]],
        **kw)
