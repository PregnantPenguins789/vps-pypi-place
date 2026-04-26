# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# vps-pypi-place

## What This Is

A daily automated broadcast — "The PyPI Place" — that monitors the Python package ecosystem,
generates honest prose analysis from test results, and renders it as a Max Headroom-style
video production using period-accurate circa-2000 Linux tools. Runs entirely on free-tier
cloud infrastructure (Oracle Always Free ARM VPS).

One human. One Dell Inspiron. Zero dollar data center. Daily 11 o'clock news from inside the machine.

## Mission

Test every new PyPI release for actual install success across multiple Python versions and
Linux distributions. Generate machine-written analysis grounded in that data. Broadcast it.
Publish everything. Forever.

## Architecture

### Components

```
watchdog/       RSS poller + batch test runner
                Fetches PyPI release feed, installs packages in batches
                inside persistent Docker containers (one per environment),
                writes results to SQLite

writer/         writing_machine adapter
                Reads structured test results from SQLite, shapes them
                into writing_machine source documents, triggers queue_runner,
                outputs dated report prose to reports/

broadcaster/
  pov/          POV-Ray scene files for the Max Headroom head geometry
  audio/        Tracker music (.mod/.xm files) + TTS assembly scripts
  compose/      ffmpeg composition scripts — frames + audio → final video

site/           Static site generator
                Builds HTML from SQLite + report files, publishes episode archive + RSS feed

deploy/
  systemd/      Unit files for all services + timers
  oracle_setup.sh   VPS provisioning script (idempotent, run on fresh Oracle ARM instance)

db/
  schema.sql    Single source of truth for database schema

config/
  settings.toml All configuration — environments, paths, timing, thresholds

tools/          One-off utilities and maintenance scripts
```

### Data Flow

```
PyPI RSS feed
      ↓
watchdog/rss_poller.py    — fetch new releases
      ↓
watchdog/batch_runner.py  — batch install test per environment → SQLite
      ↓
writer/adapter.py         — shape results into writing_machine sources
      ↓
writing_machine           — generate report prose → reports/YYYY-MM-DD.md
      ↓
broadcaster/audio/        — Piper TTS reads report, sox processes, tracker music mixed
      ↓
broadcaster/pov/          — POV-Ray renders head frames
      ↓
broadcaster/compose/      — ffmpeg assembles video + audio → episode
      ↓
site/                     — publish to static site + RSS
```

### Database

Single SQLite file. All components read/write through it.
See db/schema.sql for full schema.

### Scheduling

All automation via systemd timers (not cron).
- watchdog: every 6 hours
- writer: after watchdog completes
- broadcaster: 22:30 daily (renders for 11pm)
- site: after broadcaster completes

Oracle idle reclaim policy: CPU must exceed 20th percentile over any 7-day window.
Scheduled workloads satisfy this naturally.

## Infrastructure

- Oracle Cloud Always Free ARM (VM.Standard.A1.Flex) — 4 OCPU / 24GB RAM
  Primary compute. Runs all services.
- Oracle Cloud Always Free AMD (VM.Standard.E2.1.Micro) — 1 OCPU / 1GB RAM
  Optional: dashboard server or second test node
- Cloudflare Workers (free tier) — aggregator if multi-node
- Codeberg / self-hosted Gitea — source hosting

## Toolchain Constraints

Visual and audio production uses period-accurate circa-2000 Linux tools only:
- POV-Ray — head geometry rendering
- aalib — ASCII/terminal rendering mode
- ffmpeg — video assembly (circa 2000-2001)
- sox — audio processing
- timidity or mikmod — tracker music playback
- Piper TTS — voice synthesis (modern but local/offline)
- ImageMagick — compositing and effects

This constraint is intentional. The aesthetic is the argument.

## Design Principles

- Everything runs on free infrastructure
- All outputs are traceable to source data
- No speculation beyond what the data shows
- The pipeline is a sequence of shell scripts and systemd timers
- SQLite is the only database
- No external APIs, no cloud services beyond free-tier compute

## HTML / Frontend Rules

**No external resource loading.** All HTML pages must be fully self-contained.

Prohibited without exception:
- Google Fonts (`fonts.googleapis.com`, `fonts.gstatic.com`)
- Any CDN-hosted fonts, icon sets, or stylesheets (Font Awesome, Bootstrap, Tailwind CDN, etc.)
- Any CDN-hosted JavaScript (jQuery CDN, analytics snippets, tracking pixels, etc.)
- `<link rel="preconnect">` or `<link rel="dns-prefetch">` to third-party domains

Allowed:
- System font stacks — IBM Plex Mono, Cascadia Code, Fira Mono, Courier New, etc.
- Inline `<style>` blocks
- Inline or file-local `<script>` blocks
- Assets served from the same origin

If a font is wanted, use a system fallback stack. The page must render correctly with
zero network requests beyond the HTML file itself.

## Dev Commands

All paths below assume local dev at `/home/anonymous/vps-pypi-place/`. On the server the prefix is `/opt/vps-pypi-place/`.

```bash
# Run the watchdog (poll RSS + test a batch):
cd /home/anonymous/vps-pypi-place
PYTHONPATH=. python -m watchdog.run

# Rebuild the static dashboard from an existing DB:
python site/build.py --db /path/to/pypi_place.db --out /tmp/index.html

# Export test results to yggcrawl outbox (adapter, not report writer):
PYTHONPATH=. python -m writer.adapter --db /path/to/pypi_place.db --outbox /tmp/outbox

# Kick off a single watchdog run on the server and watch logs:
ssh ubuntu@129.153.15.163
sudo systemctl start pypi-watchdog.service
journalctl -u pypi-watchdog.service -f

# Pull latest code on the server and reload systemd unit:
git -C /opt/vps-pypi-place pull origin master
sudo tee /etc/systemd/system/pypi-watchdog.service > /dev/null < /opt/vps-pypi-place/deploy/systemd/pypi-watchdog.service
sudo systemctl daemon-reload
```

`config/settings.toml` is the single config source. `watchdog/config.py` loads it via `get("section")`.

`site/build.py` must be run as a script (`python site/build.py`), not as a module — `site` is a stdlib name.

## Current Status

**Live and running** — Oracle micro at `129.153.15.163`, proxied through `sovereignmail.org/pypiplace` via Cloudflare Worker.

**Built:**
- `watchdog/` — RSS poller, four-phase Docker install tester, classifier, SQLite writer. Runs every 6 hours via systemd timer. After each run, `ExecStartPost` fires `site/build.py` to refresh the dashboard.
- `site/build.py` — static dashboard generator (index.html + about.html). Self-contained HTML, no external resources.
- `writer/adapter.py` — exports test results as JSON records to a yggcrawl outbox directory. This is a data bridge, not a report writer.
- `deploy/` — provision and setup scripts, systemd units, Cloudflare Worker for routing.
- `db/schema.sql` — complete schema including `findings`, `asymmetries`, `package_archetypes`, `report_batches`, `reports`, `episodes`.

**Not yet built (entire broadcaster pipeline):**
- `broadcaster/pov/` — POV-Ray scene files for the Max Headroom head geometry. Empty.
- `broadcaster/audio/` — tracker theme (`theme.mod`), TTS assembly scripts. Empty.
- `broadcaster/compose/` — ffmpeg composition scripts. Empty.
- Report prose generation — `writer/adapter.py` exports raw data to yggcrawl but nothing reads that outbox and generates written prose yet. The `findings`, `report_batches`, and `reports` tables are populated by schema but no writer fills them.

The DB schema already has everything downstream needs (`findings`, `asymmetries`, `episodes`, `reports`) — the missing work is the software that reads from those tables and produces audio/video output.
