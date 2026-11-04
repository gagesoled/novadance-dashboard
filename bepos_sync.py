#!/usr/bin/env python3
"""
NOVAdance daily BEPOS sync.

Logs in to the B+ BEPOS admin, pulls the Team Report (CSV) for the whole
event-to-date and for every single day of the season, and writes
team-level totals only (no donor, participant or captain details) to:

  data/daily_team_totals.csv   one row per (date, team) with money that day
  data/team_snapshot.csv       current all-time totals per team
  data/snapshot_history.csv    one all-time row per team per run date (appended)
  site/data.json               everything the dashboard needs, incl. goals

Credentials come from environment variables BEPOS_EMAIL and BEPOS_PASSWORD.
Settings live in config/settings.json. Goals live in config/*.csv.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
SITE = ROOT / "site"
ET = ZoneInfo("America/New_York")

# Only these Team Report columns are ever kept. Captain name, e-mail,
# password and phone are dropped on read and never written anywhere.
KEEP = {
    "Team Name": "team",
    "Affliation": "affiliation",   # (sic) BEPOS spells it this way
    "# Participants": "participants",
    "# Supporters": "supporters",
    "Team Goal": "bepos_goal",
    "Money Due": "due",
    "Money Received": "received",
    "Offline": "offline",
}
MONEY = {"bepos_goal", "due", "received", "offline"}
INTS = {"participants", "supporters"}
TOTAL_ROW = re.compile(r"^\d+ Teams?$")


class SyncError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# BEPOS client
# --------------------------------------------------------------------------- #
class Bepos:
    def __init__(self, base_url: str, eventtag: str, email: str, password: str,
                 delay: float = 0.3, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.eventtag = eventtag
        self.email = email
        self.password = password
        self.delay = delay
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "NOVAdance-dashboard-sync/1.0 (approved by B+ Foundation)"

    # -- auth ---------------------------------------------------------------
    def login(self) -> None:
        url = f"{self.base}/bepos_login.aspx?eventtag={self.eventtag}"
        r = self.s.get(url, timeout=self.timeout)
        r.raise_for_status()
        form = hidden_inputs(r.text)          # __VIEWSTATE etc.
        form.update({
            "action": "submit",
            "eventtag": self.eventtag,
            "email": self.email,
            "password": self.password,
            "submit": "",
        })
        self.s.post(f"{self.base}/bepos_login.aspx", data=form,
                    timeout=self.timeout, allow_redirects=True)
        if not self.logged_in():
            raise SyncError(
                "Login failed: BEPOS did not grant admin access. Check the "
                "BEPOS_EMAIL / BEPOS_PASSWORD secrets (has the password changed?).")

    def logged_in(self) -> bool:
        r = self.s.get(f"{self.base}/bepos_admin.aspx?eventtag={self.eventtag}",
                       timeout=self.timeout, allow_redirects=False)
        # Signed-out visitors are redirected to bepositive.org.
        return r.status_code == 200 and "Administrative Interface" in r.text

    # -- reports ------------------------------------------------------------
    def team_report(self, start: str = "", end: str = "") -> list[dict]:
        """Team Report CSV for an inclusive M/D/YYYY date range ('' = all time)."""
        payload = {"action": "reportteams", "eventtag": self.eventtag,
                   "teamstart": start, "teamend": end}
        for attempt in range(1, 4):
            try:
                r = self.s.post(f"{self.base}/bepos_admin.aspx", data=payload,
                                timeout=self.timeout, allow_redirects=False)
                ctype = r.headers.get("content-type", "")
                if r.status_code in (301, 302, 303, 307, 308):
                    # Session expired mid-run: sign in again once, then retry.
                    self.login()
                    continue
                r.raise_for_status()
                if "csv" not in ctype.lower():
                    raise SyncError(f"Team Report {start or 'all'}-{end or 'all'} "
                                    f"returned {ctype or 'no content-type'}, not CSV")
                time.sleep(self.delay)
                return parse_team_csv(r.content.decode("utf-8-sig", errors="replace"))
            except (requests.RequestException, SyncError) as exc:
                if attempt == 3:
                    raise SyncError(f"Team Report failed after 3 tries: {exc}") from exc
                time.sleep(5 * attempt)
        raise SyncError("unreachable")


class _Hidden(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and (a.get("type") or "").lower() == "hidden" and a.get("name"):
            self.fields[a["name"]] = a.get("value") or ""


def hidden_inputs(html: str) -> dict[str, str]:
    p = _Hidden()
    p.feed(html)
    return p.fields


def money(v: str) -> float:
    v = (v or "").replace("$", "").replace(",", "").strip()
    return round(float(v), 2) if v not in ("", "-", ".") else 0.0


def parse_team_csv(text: str) -> list[dict]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    missing = [k for k in ("Team Name", "Money Received") if k not in header]
    if missing:
        raise SyncError(f"Team Report layout changed; missing columns {missing}. "
                        f"Got: {header}")
    idx = {KEEP[h]: i for i, h in enumerate(header) if h in KEEP}
    out = []
    for r in rows[1:]:
        name = (r[idx["team"]] if len(r) > idx["team"] else "").strip()
        if not name or set(name) == {"-"}:
            continue
        rec = {"team": name}
        for key, i in idx.items():
            if key == "team":
                continue
            val = r[i].strip() if i < len(r) else ""
            if key in MONEY:
                rec[key] = money(val)
            elif key in INTS:
                rec[key] = int(val) if val.isdigit() else 0
            else:
                rec[key] = val
        rec["is_total"] = bool(TOTAL_ROW.match(name))
        out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def mdy(d: date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def load_goals() -> tuple[dict, list[dict], dict]:
    settings = json.loads((CONFIG / "settings.json").read_text())
    monthly = [{"month": r["month"], "goal": float(r["goal"] or 0)}
               for r in read_csv(CONFIG / "monthly_goals.csv")]
    teams = {}
    for r in read_csv(CONFIG / "teams.csv"):
        teams[r["bepos_team"].strip()] = {
            "display": (r.get("display_name") or r["bepos_team"]).strip(),
            "group": (r.get("group") or "").strip(),
            "goal": float(r["goal_2027"]) if r.get("goal_2027") else None,
        }
    return settings, monthly, teams


def run(today: date | None = None) -> dict:
    settings, monthly_goals, team_cfg = load_goals()
    email, password = os.environ.get("BEPOS_EMAIL"), os.environ.get("BEPOS_PASSWORD")
    if not email or not password:
        raise SyncError("BEPOS_EMAIL and BEPOS_PASSWORD must be set.")

    if today is None and os.environ.get("BEPOS_TODAY"):      # for tests / re-runs
        today = date.fromisoformat(os.environ["BEPOS_TODAY"])
    today = today or datetime.now(ET).date()
    start = date.fromisoformat(settings["season_start"])
    end = min(today, date.fromisoformat(settings.get("season_end", "2100-01-01")))

    client = Bepos(os.environ.get("BEPOS_BASE_URL", settings["base_url"]),
                   settings["eventtag"], email, password,
                   delay=float(settings.get("request_delay_seconds", 0.3)))
    client.login()
    print(f"Logged in. Pulling {settings['eventtag']} {start} -> {end}")

    # 1) all-time snapshot (authoritative totals)
    snap_rows = client.team_report()
    snap_total = next((r for r in snap_rows if r["is_total"]), None)
    snapshot = [r for r in snap_rows if not r["is_total"]]
    if snap_total is None:
        raise SyncError("All-time Team Report had no total row")

    # 2) Running total from season start through each day, then take the
    #    day-over-day difference. BEPOS date filters can drop a gift whose
    #    entry date and received date fall on different days when the window
    #    is a single day or month; a window that always starts at season
    #    start never drops it, so the days add up exactly to the all-time
    #    total. Every day is re-pulled on every run (full restatement), so
    #    late check receipts, refunds and moved donations are always current.
    daily = []
    prev: dict[str, tuple[float, float, float]] = {}
    affiliation = {r["team"]: r.get("affiliation", "") for r in snapshot}
    for d in daterange(start, end):
        cur = {}
        for r in client.team_report(mdy(start), mdy(d)):
            if not r["is_total"]:
                cur[r["team"]] = (r["received"], r["due"], r["offline"])
                affiliation.setdefault(r["team"], r.get("affiliation", ""))
        for team in sorted(set(cur) | set(prev)):
            c, p = cur.get(team, (0.0, 0.0, 0.0)), prev.get(team, (0.0, 0.0, 0.0))
            delta = [round(c[i] - p[i], 2) for i in range(3)]
            if any(delta):
                daily.append({"date": d.isoformat(), "team": team,
                              "affiliation": affiliation.get(team, ""),
                              "received": delta[0], "due": delta[1],
                              "offline": delta[2]})
        prev = cur

    # 3) reconcile daily detail against the all-time total
    day_received = round(sum(r["received"] for r in daily), 2)
    day_due = round(sum(r["due"] for r in daily), 2)
    gap = round(snap_total["received"] - day_received, 2)
    tolerance = float(settings.get("reconcile_tolerance", 100))
    recon = {
        "all_time_received": snap_total["received"],
        "all_time_due": snap_total["due"],
        "sum_of_days_received": day_received,
        "sum_of_days_due": day_due,
        "gap_received": gap,
        "ok": abs(gap) <= tolerance,
    }

    # 4) write data files (team-level only)
    write_csv(DATA / "daily_team_totals.csv", daily,
              ["date", "team", "affiliation", "received", "due", "offline"])
    snap_fields = ["team", "affiliation", "participants", "supporters",
                   "bepos_goal", "received", "due", "offline"]
    write_csv(DATA / "team_snapshot.csv", snapshot, snap_fields)
    hist_path = DATA / "snapshot_history.csv"
    history = [h for h in read_csv(hist_path) if h["run_date"] != today.isoformat()]
    history += [{"run_date": today.isoformat(), **{k: r.get(k, "") for k in snap_fields}}
                for r in snapshot]
    write_csv(hist_path, history, ["run_date"] + snap_fields)

    # 5) dashboard payload
    unknown = sorted({r["team"] for r in snapshot} - set(team_cfg))
    payload = {
        "event": settings["event_name"],
        "eventtag": settings["eventtag"],
        "updated": datetime.now(ET).isoformat(timespec="minutes"),
        "season_start": start.isoformat(),
        "through": end.isoformat(),
        "event_goal": settings["event_goal"],
        "totals": {
            "teams": len(snapshot),
            "participants": snap_total["participants"],
            "supporters": snap_total["supporters"],
            "received": snap_total["received"],
            "due": snap_total["due"],
            "offline": snap_total["offline"],
        },
        "reconciliation": recon,
        "monthly_goals": monthly_goals,
        "teams": [{
            "team": r["team"],
            "display": team_cfg.get(r["team"], {}).get("display", r["team"]),
            "affiliation": r.get("affiliation") or team_cfg.get(r["team"], {}).get("group", ""),
            "participants": r["participants"],
            "supporters": r["supporters"],
            "received": r["received"],
            "due": r["due"],
            "goal": team_cfg.get(r["team"], {}).get("goal"),
        } for r in snapshot],
        "daily": [[r["date"], r["team"], r["received"], r["due"]] for r in daily],
        "unmapped_teams": unknown,
    }
    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(payload, separators=(",", ":")))

    print(json.dumps({"teams": len(snapshot), "days": (end - start).days + 1,
                      "rows": len(daily), **recon, "unmapped_teams": unknown}, indent=2))
    if not recon["ok"]:
        print(f"WARNING: daily detail is ${gap:,.2f} off the all-time total "
              f"(tolerance ${tolerance:,.0f}).", file=sys.stderr)
    return payload


if __name__ == "__main__":
    try:
        run()
    except SyncError as e:
        print(f"SYNC FAILED: {e}", file=sys.stderr)
        sys.exit(1)
