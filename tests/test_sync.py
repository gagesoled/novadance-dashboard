"""
End-to-end test: run bepos_sync.py (unchanged, as a subprocess) against the
mock BEPOS server loaded with the real NOVAdance27 team-level data pulled on
2026-11-03, and check the outputs.

Run:  python tests/test_sync.py
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import mock_bepos as mb  # noqa: E402


def load_fixture():
    teams, names = [], {}
    for line in (HERE / "real_teams_2026-11-03.txt").read_text().splitlines():
        if line.startswith("#"):
            continue
        i, n, a, p, s, d, r = line.split("|")
        names[int(i)] = n
        teams.append({"team": n, "affiliation": a, "participants": int(p),
                      "supporters": int(s), "due": float(d), "received": float(r)})
    gifts = []
    for line in (HERE / "real_daily_2026-11-03.txt").read_text().splitlines():
        if line.startswith("#"):
            continue
        ds, i, r, d = line.split("|")
        m, dd, y = map(int, ds.split("/"))
        day = date(y, m, dd)
        team = names[int(i)]
        if float(r):
            if day == date(2026, 9, 2) and team == "Engagement Coordinators Team":
                # the real case: nine $5 cash gifts entered 8/25, received 9/2
                for _ in range(9):
                    gifts.append({"team": team, "entered": date(2026, 8, 25),
                                  "received_on": day, "amount": 5.0, "due": False})
            else:
                gifts.append({"team": team, "entered": day, "received_on": day,
                              "amount": float(r), "due": False})
        if float(d):
            gifts.append({"team": team, "entered": day, "received_on": day,
                          "amount": float(d), "due": True})
    return teams, gifts


def run_sync(base: str, workdir: Path, email: str, password: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "BEPOS_BASE_URL": base, "BEPOS_EMAIL": email,
           "BEPOS_PASSWORD": password, "BEPOS_TODAY": "2026-11-03"}
    return subprocess.run([sys.executable, str(workdir / "bepos_sync.py")], env=env,
                          capture_output=True, text=True, timeout=600)


def fresh_copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="nd-sync-"))
    shutil.copy(REPO / "bepos_sync.py", tmp)
    shutil.copytree(REPO / "config", tmp / "config")
    s = json.loads((tmp / "config/settings.json").read_text())
    s["request_delay_seconds"] = 0
    (tmp / "config/settings.json").write_text(json.dumps(s))
    return tmp


def main() -> int:
    teams, gifts = load_fixture()
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # sanity: the mock reproduces the BEPOS quirk
    st = mb.State(teams, gifts)
    def total(csvtext):
        rows = list(csv.reader(csvtext.splitlines()))
        return float(rows[-1][12].replace("$", "").replace(",", ""))
    month_sum = sum(total(st.team_csv(a, b)) for a, b in [
        (date(2026, 6, 1), date(2026, 6, 30)), (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)), (date(2026, 11, 1), date(2026, 11, 3))])
    check("mock: month-by-month windows drop the $45 (like live BEPOS)",
          round(total(st.team_csv(None, None)) - month_sum, 2) == 45.0,
          f"all-time {total(st.team_csv(None, None)):,.2f} vs months {month_sum:,.2f}")

    # 1) happy path, with the session expiring every 40 report calls
    st = mb.State(teams, gifts, expire_after=40)
    srv, base = mb.serve(st)
    work = fresh_copy()
    p = run_sync(base, work, mb.GOOD_EMAIL, mb.GOOD_PASSWORD)
    check("sync exits 0", p.returncode == 0, p.stderr[-500:])
    daily = list(csv.DictReader(open(work / "data/daily_team_totals.csv")))
    snap = list(csv.DictReader(open(work / "data/team_snapshot.csv")))
    payload = json.loads((work / "site/data.json").read_text())
    rec = round(sum(float(r["received"]) for r in daily), 2)
    due = round(sum(float(r["due"]) for r in daily), 2)
    check("days add up to all-time received ($128,253.08)", rec == 128253.08, f"{rec:,.2f}")
    check("days add up to all-time due ($625.00)", due == 625.00, f"{due:,.2f}")
    check("reconciliation flag ok", payload["reconciliation"]["ok"], payload["reconciliation"])
    by_team = defaultdict(float)
    for r in daily:
        by_team[r["team"]] += float(r["received"])
    bad = [t["team"] for t in teams if abs(by_team[t["team"]] - t["received"]) > 0.005]
    check("every team's days add up to its all-time total", not bad, bad)
    by_month = defaultdict(float)
    for r in daily:
        by_month[r["date"][:7]] += float(r["received"])
    want = {"2026-06": 1.00, "2026-07": 11957.40, "2026-08": 3295.00,
            "2026-09": 7947.00, "2026-10": 103107.68, "2026-11": 1945.00}
    got = {k: round(v, 2) for k, v in sorted(by_month.items())}
    check("monthly totals match live BEPOS", got == want, got)
    check("the $45 lands on 9/2 (received date)",
          any(r["date"] == "2026-09-02" and r["team"] == "Engagement Coordinators Team"
              and float(r["received"]) == 45.0 for r in daily))
    check("62 teams in snapshot", len(snap) == 62, len(snap))
    check("re-logged in after session expiry", st.logins > 1, f"logins={st.logins}")
    check("one request per day + 1 all-time", st.report_calls == (date(2026, 11, 3) - date(2026, 6, 1)).days + 2,
          st.report_calls)
    check("no unmapped teams vs config/teams.csv", payload["unmapped_teams"] == [],
          payload["unmapped_teams"])
    leaked = []
    for f in [*(work / "data").glob("*"), work / "site/data.json"]:
        txt = f.read_text()
        for bad_str in (mb.FAKE_SECRET, "captain@example.com", "555-0100", mb.GOOD_PASSWORD):
            if bad_str in txt:
                leaked.append((f.name, bad_str))
    check("no captain or login details in any output", not leaked, leaked)

    # 2) second run same day: history replaces, not duplicates
    p2 = run_sync(base, work, mb.GOOD_EMAIL, mb.GOOD_PASSWORD)
    hist = list(csv.DictReader(open(work / "data/snapshot_history.csv")))
    check("re-run same day keeps one history row per team", p2.returncode == 0 and len(hist) == 62, len(hist))
    srv.shutdown()

    # 3) wrong password fails loudly
    st = mb.State(teams, gifts)
    srv, base = mb.serve(st)
    p = run_sync(base, fresh_copy(), mb.GOOD_EMAIL, "wrong")
    check("bad password -> exit 1 with clear message",
          p.returncode == 1 and "Login failed" in p.stderr, p.stderr.strip()[-200:])
    srv.shutdown()

    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {'' if ok else detail}")
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
