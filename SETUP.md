# NOVAdance dashboard — setup guide

## What this is

A static dashboard that shows NOVAdance 2027 fundraising from the B+ Foundation's
BEPOS system, updated automatically every morning. A GitHub Action signs in to
the BEPOS admin, pulls the Team Report, and publishes the dashboard to
GitHub Pages. Nobody has to export a CSV by hand again.

- **Live page:** `https://<github-username>.github.io/novadance-dashboard/` once
  the steps below are done (the exact URL is shown on the repo's
  Settings → Pages page after the first deploy).
- **Schedule:** daily at 11:23 UTC — 6:23 AM Eastern in winter, 7:23 AM in
  daylight time. There is also a manual **Run workflow** button on the
  repo's Actions tab.
- **What updates:** team standings, team × month table, cumulative vs. goal-pace
  chart, monthly chart, KPIs, and the "Ties to BEPOS" reconciliation badge.
- **What is stored:** team-level totals only. Donor, participant, and captain
  details — including the captain passwords BEPOS puts in its CSVs — are
  dropped when the CSV is read and are never written to any file in this repo.

## Before you start

You need two things:

1. **A GitHub account** that will own the repo. Any account works; use the
   account NOVAdance already uses if there is one, so the exec team keeps
   access if you graduate.
2. **A BEPOS admin login** for the NOVAdance27 event — the email and password
   that sign in at `https://www.beposfdn.org/bepos_login.aspx?eventtag=NOVAdance27`.
   If you don't have one, the contacts named on that login page are
   Evan Johnson and John Tropeano (`novadanceexec@gmail.com`). Do not put the
   password in chat, email, or any file. It goes only into GitHub Secrets
   (step 3), where GitHub stores it encrypted and the workflow reads it as an
   environment variable.

## 1. Create the repo and push these files

Create a **public** repo named `novadance-dashboard` on GitHub (public, so the
Pages site is shareable with teams; the repo contains no donor or login data).
Push every file in this folder, including the hidden `.github/` folder and
`data/` / `site/data.json` (the seeded Nov 3, 2026 data — see below). The
default branch must be `main`.

From the command line, after `gh auth login`:

```sh
gh repo create novadance-dashboard --public --source=. --remote=origin --push
```

Or create the empty repo on github.com and push with git in the usual way.
Do not rename the workflow file: GitHub only runs workflows in
`.github/workflows/`.

## 2. Enable GitHub Pages

Repo → **Settings → Pages** → under **Build and deployment → Source** choose
**GitHub Actions**. (Do not choose "Deploy from a branch" — the workflow
publishes the `site/` folder as a Pages artifact itself.)

## 3. Add the BEPOS secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Add exactly these two, typing the values directly into GitHub — never paste
them anywhere else:

| Secret name | Value |
|---|---|
| `BEPOS_EMAIL` | The BEPOS admin email |
| `BEPOS_PASSWORD` | The BEPOS admin password |

From the command line, `gh secret set BEPOS_EMAIL` and
`gh secret set BEPOS_PASSWORD` prompt for the value without echoing it.

## 4. Run the first live sync

Repo → **Actions → Daily BEPOS sync → Run workflow → Run workflow**.

Watch the run. It takes a few minutes: it pulls one Team Report for the
all-time total, then one running-total report per day from June 1, 2026
through today (about 157 requests on Nov 3; it grows by one a day), and
commits the refreshed `data/` files and `site/data.json`. The deploy job
then publishes the site.

Verify, in this order:

1. The workflow run is green.
2. The commit message starts "Daily BEPOS sync" and `site/data.json` changed.
3. The Pages URL loads and the header says "Updated" today, "Ties to BEPOS",
   and the received total matches BEPOS.

If the run fails with "Login failed", the BEPOS password has changed or the
secrets hold the wrong account — update the two secrets and run it again.
If it fails later with a "layout changed" or "not CSV" message, BEPOS changed
its Team Report format; the old dashboard stays up (the deploy step never
runs on a failed sync), and the sync script needs the new column layout.

From then on, the schedule runs itself. No further action is needed unless a
run fails — GitHub emails the repo owner when a scheduled workflow fails.

## Seeded data (already in this copy)

`data/` and `site/data.json` are the real pull from Nov 3, 2026: 62 teams,
1,173 participants, 1,560 supporters, $128,253.08 received, $625.00 due,
with 205 daily rows that reconcile exactly ($0.00 gap) to the BEPOS all-time
total. That means the Pages site shows correct numbers from the first deploy,
before any live run. The first live run re-pulls the entire season from
June 1 and restates every day, so late receipts, refunds, and moved donations
are picked up automatically. `snapshot_history.csv` starts with one row per
team for Nov 3, 2026.

## Changing things later

- **A new team appears in BEPOS:** it shows up automatically in the next sync
  with its BEPOS name, but it will be listed in `unmapped_teams` in
  `data.json` until you add a row to `config/teams.csv`
  (`bepos_team,display_name,group,goal_2027`). The dashboard still works
  while a team is unmapped — it just uses the BEPOS name and no goal.
  Add the row, commit, and run the workflow (or wait for the morning run).
- **Goals:** edit `config/teams.csv` (`goal_2027` per team),
  `config/monthly_goals.csv` (event-wide goal per month), or
  `config/settings.json` (`event_goal`, season dates). Commit, then run the
  workflow; the next sync rebuilds `site/data.json` from the new config.
- **BEPOS password changes:** update the `BEPOS_PASSWORD` secret only.
  Never commit a password.
- **Peak season:** to refresh three times a day, uncomment the second cron
  line in `.github/workflows/daily-sync.yml`
  (`23 17,23 * * *` = 12:23 PM and 6:23 PM Eastern).

## Goal: the data-sheet total

`event_goal` in `config/settings.json` is $1,067,466.20, the sum of
`config/monthly_goals.csv`. The $1,100,000 headline goal is ignored per
Gage (Nov 3, 2026): the hero meter and the cumulative goal-pace line both
measure against the monthly-goal total. If the monthly goals change, update
`event_goal` to their new sum and run the workflow.

## Testing without BEPOS

```sh
python tests/test_sync.py
```

This runs the sync end-to-end against a mock BEPOS server loaded with the
real Nov 3 team-level data. It checks that the daily rows add up exactly to
the all-time totals, that the $45 of cash gifts entered Aug 25 and received
Sep 2 lands on Sep 2, that sessions expiring mid-run are re-established, that
a wrong password fails loudly, and that no captain or login details leak
into any output file. All 15 checks pass.

## Privacy

Only team-level totals ever leave BEPOS: team name, affiliation, participant
and supporter counts, team goal, money due, money received, and offline
amounts. Raw BEPOS exports (`.xlsx`, `exports/`, `raw/`) are git-ignored and
must never be committed — they contain donor details and captain passwords.
