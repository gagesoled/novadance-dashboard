# NOVAdance fundraising dashboard

Every morning, a GitHub Action logs in to the B+ BEPOS admin and pulls the Team Report (CSV) as a running total for every day of the season. It saves team-level totals and publishes the dashboard in `site/` to GitHub Pages.

| Path | What it is |
|---|---|
| `bepos_sync.py` | The sync script: login, Team Report pulls, reconciliation, output files |
| `config/settings.json` | Event tag, season dates, headline goal |
| `config/teams.csv` | BEPOS team name → display name, group, 2027 goal |
| `config/monthly_goals.csv` | Event-wide goal per month |
| `data/` | Generated daily: `daily_team_totals.csv`, `team_snapshot.csv`, `snapshot_history.csv` |
| `site/index.html` | The dashboard page; reads `site/data.json` |
| `.github/workflows/daily-sync.yml` | Daily schedule plus the manual "Run workflow" button |
| `tests/` | Offline end-to-end test against a mock BEPOS (`python tests/test_sync.py`) |

Secrets required (repo Settings → Secrets and variables → Actions): `BEPOS_EMAIL` and `BEPOS_PASSWORD`.

Only team-level totals are stored. Donor, participant and captain details (including the captain passwords that BEPOS puts in its CSVs) are dropped when the CSV is read and never written.

See [SETUP.md](SETUP.md) for full instructions: creating the repo, enabling Pages, adding the BEPOS secrets, and running the first live sync.

Seeded data: `data/` and `site/data.json` in this copy are the real Nov 3, 2026 pull (62 teams, $128,253.08 received, daily rows reconcile exactly to the BEPOS all-time total), so the dashboard shows correct numbers before the first live run. The first scheduled run re-pulls the whole season and restates everything.
