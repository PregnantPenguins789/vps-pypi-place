#!/usr/bin/env python3
"""
build.py — static dashboard generator for The PyPI Place

Usage:  python site/build.py [--db PATH] [--out FILE]

Run as a script, not a module — 'site' is a stdlib name.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_DB  = "/opt/vps-pypi-place/data/pypi_place.db"
_DEFAULT_OUT = "/opt/vps-pypi-place/site/index.html"

# ─────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────

_EMPTY = {
    "packages_tested": 0,
    "total_tests":     0,
    "pass":            0,
    "fail":            0,
    "timeout":         0,
    "phantom":         0,
    "partial":         0,
    "pass_rate":       0.0,
    "last_tested":     None,
    "envs":            [],
    "recent_tests":    [],
    "findings":        [],
    "archetypes":      [],
    "asymmetries":     [],
    "episodes":        [],
    "today_batch":     None,
    "generated_at":    "",
}


def _collect(conn) -> dict:
    d = dict(_EMPTY)
    d["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    c = conn.cursor()

    c.execute("""
        SELECT
            COUNT(DISTINCT t.release_id),
            COUNT(*),
            SUM(t.status = 'PASS'),
            SUM(t.status = 'FAIL'),
            SUM(t.status = 'TIMEOUT'),
            SUM(t.status = 'PHANTOM'),
            SUM(t.status = 'PARTIAL'),
            MAX(t.tested_at)
        FROM test_results t
        WHERE t.status != 'pending'
    """)
    row = c.fetchone()
    if row and row[1]:
        d.update({
            "packages_tested": row[0] or 0,
            "total_tests":     row[1] or 0,
            "pass":            row[2] or 0,
            "fail":            row[3] or 0,
            "timeout":         row[4] or 0,
            "phantom":         row[5] or 0,
            "partial":         row[6] or 0,
            "last_tested":     row[7],
        })
        if d["total_tests"]:
            d["pass_rate"] = round(100.0 * d["pass"] / d["total_tests"], 1)

    c.execute("""
        SELECT environment,
               SUM(status = 'PASS'),
               COUNT(*)
        FROM test_results
        WHERE status != 'pending'
        GROUP BY environment
        ORDER BY SUM(status = 'PASS') DESC
    """)
    d["envs"] = [
        {
            "name":  r[0],
            "pass":  r[1] or 0,
            "total": r[2] or 0,
            "rate":  round(100.0 * (r[1] or 0) / r[2], 1) if r[2] else 0.0,
        }
        for r in c.fetchall()
    ]

    c.execute("""
        SELECT
            t.tested_at, r.package, r.version,
            t.environment, t.status, t.failure_type,
            t.phase_download, t.phase_nodeps, t.phase_full, t.phase_import,
            t.dep_count
        FROM test_results t
        JOIN releases r ON r.id = t.release_id
        WHERE t.status != 'pending'
        ORDER BY t.tested_at DESC
        LIMIT 50
    """)
    _tc = ["tested_at","package","version","environment","status","failure_type",
           "phase_download","phase_nodeps","phase_full","phase_import","dep_count"]
    d["recent_tests"] = [dict(zip(_tc, r)) for r in c.fetchall()]

    c.execute("""
        SELECT r.package, r.version,
               f.finding_type, f.detail, f.severity, f.environments, f.created_at
        FROM findings f
        JOIN releases r ON r.id = f.release_id
        ORDER BY
            CASE f.severity
                WHEN 'critical'    THEN 0
                WHEN 'significant' THEN 1
                WHEN 'notable'     THEN 2
                ELSE 3
            END,
            f.created_at DESC
        LIMIT 15
    """)
    _fc = ["package","version","finding_type","detail","severity","environments","created_at"]
    d["findings"] = [dict(zip(_fc, r)) for r in c.fetchall()]

    c.execute("""
        SELECT archetype, COUNT(*) FROM package_archetypes
        GROUP BY archetype ORDER BY COUNT(*) DESC
    """)
    d["archetypes"] = [{"archetype": r[0], "count": r[1]} for r in c.fetchall()]

    c.execute("""
        SELECT r.package, r.version,
               a.asymmetry_type, a.passing_envs, a.failing_envs,
               a.detail, a.detected_at
        FROM asymmetries a
        JOIN releases r ON r.id = a.release_id
        ORDER BY a.detected_at DESC
        LIMIT 10
    """)
    _ac = ["package","version","asymmetry_type","passing_envs","failing_envs","detail","detected_at"]
    d["asymmetries"] = [dict(zip(_ac, r)) for r in c.fetchall()]

    c.execute("""
        SELECT date, status, duration_sec, video_path, published_at
        FROM episodes
        ORDER BY date DESC
        LIMIT 10
    """)
    d["episodes"] = [
        {"date": r[0], "status": r[1], "duration_sec": r[2],
         "video_path": r[3], "published_at": r[4]}
        for r in c.fetchall()
    ]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c.execute("SELECT * FROM report_batches WHERE date = ?", (today,))
    row = c.fetchone()
    if row:
        d["today_batch"] = dict(zip([x[0] for x in c.description], row))

    return d


# ─────────────────────────────────────────────
# RENDER HELPERS
# ─────────────────────────────────────────────

_SC = {
    "PASS":    "var(--green)",
    "FAIL":    "var(--red)",
    "TIMEOUT": "var(--amber)",
    "PHANTOM": "var(--amber)",
    "PARTIAL": "var(--grey-light)",
    "PENDING": "var(--grey)",
}

_ARCHETYPE_COLOR = {
    "clean_citizen":   "var(--green)",
    "fragile_tower":   "var(--amber)",
    "dep_explosion":   "var(--blue)",
    "phantom_success": "var(--amber)",
    "bitrot":          "var(--red)",
    "native_gamble":   "var(--amber)",
    "arch_specific":   "var(--blue)",
    "unknown":         "var(--grey)",
}

_SEV_COLOR = {
    "critical":    "var(--red)",
    "significant": "var(--amber)",
    "notable":     "var(--green)",
    "info":        "var(--grey-light)",
}

_ASYM_COLOR = {
    "arch_split":    "var(--amber)",
    "version_split": "var(--blue)",
    "distro_split":  "var(--green)",
}


def _sc(status):
    return _SC.get((status or "").upper(), "var(--grey)")


def _phase(val):
    v = (val or "").upper()
    if v == "PASS":    return '<span style="color:var(--green)">✓</span>'
    if v == "FAIL":    return '<span style="color:var(--red)">✗</span>'
    if v == "TIMEOUT": return '<span style="color:var(--amber)">⏱</span>'
    if v == "SKIP":    return '<span style="color:var(--grey)">—</span>'
    return '<span style="color:var(--grey)">·</span>'


def _env(name):
    return (name or "").replace("python:", "").replace(":", "-")


def _jlist(raw):
    try:
        return json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []


# ─────────────────────────────────────────────
# CSS  (verbatim design language from index.html)
# ─────────────────────────────────────────────

_CSS = """
    :root {
        --bg: #0a0a0a;
        --bg2: #111111;
        --bg3: #181818;
        --green: #00ff41;
        --green-dim: #00aa2a;
        --green-dark: #003d0f;
        --amber: #ffb300;
        --amber-dim: #cc8800;
        --red: #ff3131;
        --blue: #00cfff;
        --blue-dim: #007799;
        --white: #e8e8e8;
        --grey: #555;
        --grey-light: #888;
        --scan: rgba(0,255,65,0.03);
        --font-mono: 'IBM Plex Mono', 'Courier New', Courier, monospace;
        --font-display: 'Bebas Neue', Impact, 'Arial Narrow', sans-serif;
        --font-term: 'Share Tech Mono', 'Lucida Console', monospace;
    }
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
        background: var(--bg);
        color: var(--white);
        font-family: var(--font-mono);
        font-size: 16px;
        line-height: 1.6;
        overflow-x: hidden;
    }
    body::before {
        content: '';
        position: fixed;
        top:0; left:0; right:0; bottom:0;
        background: repeating-linear-gradient(0deg, transparent, transparent 2px, var(--scan) 2px, var(--scan) 4px);
        pointer-events: none;
        z-index: 1000;
    }
    @keyframes flicker { 0%,97%,100% { opacity:1; } 98% { opacity:0.92; } 99% { opacity:0.98; } }
    body { animation: flicker 8s infinite; }

    header {
        border-bottom: 1px solid var(--green-dim);
        padding: 2rem 2rem 1.5rem;
        background: linear-gradient(180deg, #001a05 0%, transparent 100%);
    }
    .header-top {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 1rem;
    }
    .logo {
        font-family: var(--font-display);
        font-size: clamp(3rem, 8vw, 6rem);
        color: var(--green);
        letter-spacing: 0.05em;
        text-shadow: 0 0 20px var(--green), 0 0 60px rgba(0,255,65,0.3);
        line-height: 0.9;
    }
    .logo span {
        display: block;
        font-size: 0.35em;
        color: var(--amber);
        letter-spacing: 0.3em;
        text-shadow: 0 0 10px var(--amber);
        margin-top: 0.3em;
    }
    .tagline {
        font-family: var(--font-term);
        color: var(--amber);
        font-size: 0.9rem;
        border: 1px solid var(--amber-dim);
        padding: 0.4rem 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        animation: pulse-border 3s ease-in-out infinite;
        align-self: flex-end;
    }
    @keyframes pulse-border {
        0%,100% { border-color: var(--amber-dim); }
        50% { border-color: var(--amber); box-shadow: 0 0 8px var(--amber); }
    }
    .header-meta {
        margin-top: 1rem;
        display: flex;
        gap: 2rem;
        font-size: 0.75rem;
        color: var(--white);
        flex-wrap: wrap;
    }
    .header-meta span { color: var(--green); }

    main {
        padding: 1.5rem 2rem;
        display: grid;
        grid-template-columns: repeat(12, 1fr);
        gap: 1rem;
        max-width: 1600px;
        margin: 0 auto;
    }
    .card {
        background: var(--bg2);
        border: 1px solid #222;
        padding: 1.2rem;
        position: relative;
        transition: border-color 0.3s;
    }
    .card:hover { border-color: var(--green-dim); }
    .card::before {
        content: '';
        position: absolute;
        top:0; left:0;
        width: 3px; height: 100%;
        background: var(--green);
        opacity: 0;
        transition: opacity 0.3s;
    }
    .card:hover::before { opacity: 1; }
    .card-title {
        font-family: var(--font-term);
        font-size: 0.65rem;
        letter-spacing: 0.2em;
        text-transform: uppercase;
        color: var(--green);
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .card-title::before { content: '//'; color: var(--green); }

    .col-3  { grid-column: span 3; }
    .col-4  { grid-column: span 4; }
    .col-5  { grid-column: span 5; }
    .col-6  { grid-column: span 6; }
    .col-7  { grid-column: span 7; }
    .col-8  { grid-column: span 8; }
    .col-12 { grid-column: span 12; }
    @media (max-width: 900px) {
        .col-3,.col-4,.col-5,.col-6,.col-7,.col-8 { grid-column: span 12; }
    }

    .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.8rem; }
    .stat-block {
        background: var(--bg3);
        padding: 0.8rem;
        border-left: 2px solid var(--green-dim);
    }
    .stat-val {
        font-family: var(--font-display);
        font-size: 2rem;
        color: var(--green);
        line-height: 1;
        text-shadow: 0 0 10px rgba(0,255,65,0.4);
    }
    .stat-label {
        font-size: 0.65rem;
        color: var(--white);
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-top: 0.3rem;
    }

    .terminal {
        background: #050505;
        border: 1px solid #1e1e1e;
        padding: 1rem;
        font-family: var(--font-term);
        font-size: 0.75rem;
        line-height: 1.8;
        position: relative;
        max-height: 320px;
        overflow-y: auto;
    }
    .terminal::before {
        content: '● ● ●';
        display: block;
        color: var(--grey);
        font-size: 0.6rem;
        margin-bottom: 0.8rem;
        letter-spacing: 0.3em;
    }

    .t-green { color: var(--green); }
    .t-red   { color: var(--red); }
    .t-amber { color: var(--amber); }
    .t-blue  { color: var(--blue); }
    .t-grey  { color: var(--grey-light); }

    @keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0; } }
    .cursor {
        display: inline-block;
        width: 0.5em; height: 1em;
        background: var(--green);
        animation: blink 1s step-end infinite;
        vertical-align: text-bottom;
        margin-left: 2px;
    }
    .blink { animation: blink 1s step-end infinite; }

    table { width: 100%; border-collapse: collapse; font-size: 0.73rem; }
    th {
        text-align: left;
        border-bottom: 1px solid var(--grey);
        padding: 0.5rem;
        color: var(--grey);
        font-weight: normal;
        letter-spacing: 0.05em;
    }
    td { padding: 0.5rem; border-bottom: 1px solid #1a1a1a; }
    tr:hover td { background: rgba(0,255,65,0.02); }

    .pill {
        font-size: 0.6rem;
        padding: 0.15rem 0.5rem;
        border: 1px solid #333;
        color: var(--white);
        letter-spacing: 0.05em;
        display: inline-block;
        margin-right: 0.3rem;
        margin-bottom: 0.3rem;
    }
    .pill:hover { border-color: var(--green-dim); color: var(--green); cursor: default; }

    .progress-row { margin-bottom: 0.7rem; }
    .progress-label {
        display: flex;
        justify-content: space-between;
        font-size: 0.68rem;
        color: var(--white);
        margin-bottom: 0.2rem;
    }
    .progress-label span { color: var(--green); }
    .progress-bar { height: 3px; background: #1a1a1a; }
    .progress-fill { height: 100%; background: var(--green); box-shadow: 0 0 4px var(--green); }
    .progress-fill.amber { background: var(--amber); box-shadow: 0 0 4px var(--amber); }
    .progress-fill.red   { background: var(--red);   box-shadow: 0 0 4px var(--red); }

    .section-label {
        grid-column: span 12;
        display: flex;
        align-items: center;
        gap: 1rem;
        margin-top: 0.5rem;
    }
    .section-label h2 {
        font-family: var(--font-display);
        font-size: 1rem;
        letter-spacing: 0.2em;
        color: var(--grey);
        white-space: nowrap;
    }
    .section-label::after { content:''; flex:1; height:1px; background:#1e1e1e; }

    footer {
        border-top: 1px solid #1e1e1e;
        padding: 1.5rem 2rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 1rem;
        font-size: 0.7rem;
        color: var(--grey);
        margin-top: 2rem;
    }
    footer a { color: var(--green-dim); text-decoration: none; }
    footer a:hover { color: var(--green); }

    ::-webkit-scrollbar { width: 4px; }
    ::-webkit-scrollbar-track { background: var(--bg); }
    ::-webkit-scrollbar-thumb { background: var(--green-dark); }
    ::-webkit-scrollbar-thumb:hover { background: var(--green-dim); }

    .empty-state {
        color: var(--grey);
        font-size: 0.72rem;
        padding: 1.5rem 0;
        text-align: center;
        font-family: var(--font-term);
    }
    .finding-item { padding: 0.5rem 0; border-bottom: 1px solid #1a1a1a; }
    .finding-type {
        font-family: var(--font-term);
        font-size: 0.6rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-bottom: 0.15rem;
    }
    .finding-pkg { color: var(--white); font-size: 0.72rem; }
    .finding-detail { color: var(--grey-light); font-size: 0.65rem; }

    .arch-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.45rem 0;
        border-bottom: 1px solid #1a1a1a;
        font-size: 0.72rem;
    }
    .arch-val {
        font-family: var(--font-display);
        font-size: 1.6rem;
        line-height: 1;
    }
    .asym-item { padding: 0.5rem 0; border-bottom: 1px solid #1a1a1a; font-size: 0.7rem; }
    .asym-type {
        font-size: 0.6rem;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        margin-bottom: 0.15rem;
    }
    .ep-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.45rem 0;
        border-bottom: 1px solid #1a1a1a;
        font-size: 0.72rem;
        gap: 1rem;
    }
    .ep-date { font-family: var(--font-term); color: var(--green); min-width: 7rem; }
    .ep-status { font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.1em; }
    .ep-dur { color: var(--grey-light); min-width: 4rem; text-align: right; }
"""


# ─────────────────────────────────────────────
# RENDER
# ─────────────────────────────────────────────

def render(d: dict) -> str:
    live        = bool(d.get("last_tested"))
    status_lbl  = "LIVE" if live else "OFFLINE"
    status_clr  = "var(--green)" if live else "var(--red)"
    last_t      = (d.get("last_tested") or "—")[:16]
    pr          = d["pass_rate"]
    pr_bar_cls  = "green" if pr >= 50 else "amber" if pr >= 20 else "red"

    # ── env health rows
    env_html = ""
    for env in d["envs"]:
        short = _env(env["name"])
        r     = env["rate"]
        cls   = "green" if r >= 50 else "amber" if r >= 20 else "red"
        env_html += f"""
        <div class="progress-row">
            <div class="progress-label">{short}<span>{r}% &nbsp;{env['pass']}/{env['total']}</span></div>
            <div class="progress-bar"><div class="progress-fill {cls}" style="width:{r}%"></div></div>
        </div>"""
    if not env_html:
        env_html = '<div class="empty-state">no data yet</div>'

    # ── pill summary of envs tested
    env_pills = "".join(
        f'<span class="pill">{_env(e["name"])}: {e["pass"]}/{e["total"]} ({e["rate"]}%)</span>'
        for e in d["envs"]
    )

    # ── recent tests table
    test_rows = ""
    for t in d["recent_tests"]:
        sc   = _sc(t["status"])
        env  = _env(t.get("environment", ""))
        ts   = (t.get("tested_at") or "")[:16]
        ft   = t.get("failure_type") or ""
        ft_h = f'<span class="t-grey" style="font-size:0.62rem">{ft}</span>' if ft else "—"
        phases = (
            _phase(t.get("phase_download")) +
            _phase(t.get("phase_nodeps")) +
            _phase(t.get("phase_full")) +
            _phase(t.get("phase_import"))
        )
        test_rows += f"""
                <tr>
                    <td class="t-grey">{ts}</td>
                    <td><strong>{t.get('package','')}</strong>&nbsp;<span class="t-grey">{t.get('version','')}</span></td>
                    <td style="color:var(--blue)">{env}</td>
                    <td style="color:{sc};font-weight:bold">{t.get('status','')}</td>
                    <td style="letter-spacing:0.15em">{phases}</td>
                    <td>{ft_h}</td>
                </tr>"""
    if not test_rows:
        test_rows = '<tr><td colspan="6" class="empty-state">no results yet</td></tr>'

    # ── findings
    finding_html = ""
    for f in d["findings"]:
        sc   = _SEV_COLOR.get(f.get("severity", "info"), "var(--grey-light)")
        envs = _jlist(f.get("environments"))
        env_s = ("&nbsp;·&nbsp;" + "&nbsp;".join(_env(e) for e in envs[:3])) if envs else ""
        finding_html += f"""
        <div class="finding-item">
            <div class="finding-type" style="color:{sc}">{(f.get('finding_type') or '').replace('_',' ')}</div>
            <div class="finding-pkg"><strong>{f.get('package','')}</strong>&nbsp;<span class="t-grey">{f.get('version','')}</span></div>
            <div class="finding-detail">{(f.get('detail') or '')[:90]}{env_s}</div>
        </div>"""
    if not finding_html:
        finding_html = '<div class="empty-state">no findings yet</div>'

    # ── archetypes
    arch_html  = ""
    total_arch = sum(a["count"] for a in d["archetypes"]) or 1
    for a in d["archetypes"]:
        clr = _ARCHETYPE_COLOR.get(a["archetype"], "var(--grey)")
        pct = round(100 * a["count"] / total_arch, 1)
        arch_html += f"""
        <div class="arch-item">
            <span class="arch-val" style="color:{clr}">{a['archetype'].replace('_',' ')}</span>
            <span style="display:flex;align-items:baseline;gap:0.6rem">
                <span class="arch-val" style="color:{clr}">{a['count']}</span>
                <span class="t-grey" style="font-size:0.62rem">{pct}%</span>
            </span>
        </div>"""
    if not arch_html:
        arch_html = '<div class="empty-state">no archetypes classified yet</div>'

    # ── asymmetries
    asym_html = ""
    for a in d["asymmetries"]:
        tc    = _ASYM_COLOR.get(a.get("asymmetry_type", ""), "var(--grey-light)")
        penvs = " ".join(_env(e) for e in _jlist(a.get("passing_envs"))[:3])
        fenvs = " ".join(_env(e) for e in _jlist(a.get("failing_envs"))[:3])
        sep   = "&nbsp;&nbsp;" if penvs and fenvs else ""
        asym_html += f"""
        <div class="asym-item">
            <div class="asym-type" style="color:{tc}">{(a.get('asymmetry_type') or '').replace('_',' ')}</div>
            <div><strong>{a.get('package','')}</strong>&nbsp;<span class="t-grey">{a.get('version','')}</span></div>
            <div style="font-size:0.65rem;margin-top:0.15rem">
                <span class="t-green">✓ {penvs}</span>{sep}<span class="t-red">✗ {fenvs}</span>
            </div>
        </div>"""
    if not asym_html:
        asym_html = '<div class="empty-state">no asymmetries detected yet</div>'

    # ── today's batch
    batch = d.get("today_batch")
    if batch:
        batch_html = f"""
        <div class="terminal">
            <div><span class="t-green">date</span>&nbsp;&nbsp;&nbsp;&nbsp;{batch.get('date','—')}</div>
            <div><span class="t-green">status</span>&nbsp;&nbsp;{batch.get('status','—')}</div>
            <div><span class="t-green">tested</span>&nbsp;&nbsp;{batch.get('releases_tested',0)}</div>
            <div><span class="t-green">pass</span>&nbsp;&nbsp;&nbsp;&nbsp;{batch.get('pass_count',0)}</div>
            <div><span class="t-red">fail</span>&nbsp;&nbsp;&nbsp;&nbsp;{batch.get('fail_count',0)}</div>
            <div><span class="t-amber">timeout</span>&nbsp;{batch.get('timeout_count',0)}</div>
            <div><span class="t-amber">phantom</span>&nbsp;{batch.get('phantom_count',0)}</div>
            <div><span class="cursor"></span></div>
        </div>"""
    else:
        batch_html = '<div class="empty-state">no batch today yet</div>'

    # ── episodes
    ep_html = ""
    for ep in d["episodes"]:
        s   = ep.get("status", "pending")
        sc  = {"complete": "var(--green)", "failed": "var(--red)"}.get(s, "var(--grey)")
        dur = ""
        if ep.get("duration_sec"):
            m, s2 = divmod(ep["duration_sec"], 60)
            dur = f"{m}m{s2:02d}s"
        link = ""
        if ep.get("video_path"):
            link = f'<a href="{ep["video_path"]}" style="color:var(--amber);font-size:0.65rem">[WATCH]</a>'
        ep_html += f"""
        <div class="ep-row">
            <div class="ep-date">{ep.get('date','')}</div>
            <div class="ep-status" style="color:{sc}">{s}</div>
            <div class="ep-dur">{dur}</div>
            <div>{link}</div>
        </div>"""
    if not ep_html:
        ep_html = '<div class="empty-state">no episodes yet</div>'

    # ── assemble
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="300">
<title>The PyPI Place — Live Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Mono:wght@400;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
{_CSS}
</style>
</head>
<body>

<header>
  <div class="header-top">
    <div class="logo">The PyPI Place<span>A Place for PyPI</span></div>
    <div class="tagline">⚠ Dangerously Educational</div>
  </div>
  <div class="header-meta">
    <div>STATUS: <span style="color:{status_clr}">{status_lbl}</span></div>
    <div>PACKAGES TESTED: <span>{d['packages_tested']:,}</span></div>
    <div>TOTAL TESTS: <span>{d['total_tests']:,}</span></div>
    <div>PASS RATE: <span>{pr}%</span></div>
    <div>LAST TESTED: <span>{last_t}</span></div>
    <div>GENERATED: <span>{d['generated_at']}</span></div>
  </div>
</header>

<main>

  <!-- ── stats + env health + today's batch ── -->

  <div class="card col-4">
    <div class="card-title">PyPI Watchdog Stats</div>
    <div class="stat-grid">
      <div class="stat-block">
        <div class="stat-val">{d['packages_tested']:,}</div>
        <div class="stat-label">Packages</div>
      </div>
      <div class="stat-block">
        <div class="stat-val">{d['total_tests']:,}</div>
        <div class="stat-label">Tests Run</div>
      </div>
      <div class="stat-block">
        <div class="stat-val" style="color:var(--green)">{d['pass']:,}</div>
        <div class="stat-label">Passed</div>
      </div>
      <div class="stat-block">
        <div class="stat-val" style="color:var(--red)">{d['fail']:,}</div>
        <div class="stat-label">Failed</div>
      </div>
      <div class="stat-block">
        <div class="stat-val" style="color:var(--amber)">{d['timeout']:,}</div>
        <div class="stat-label">Timeouts</div>
      </div>
      <div class="stat-block">
        <div class="stat-val" style="color:var(--amber)">{d['phantom']:,}</div>
        <div class="stat-label">Phantoms</div>
      </div>
    </div>
    <div style="margin-top:1rem">
      <div class="progress-row">
        <div class="progress-label">Pass rate <span>{pr}%</span></div>
        <div class="progress-bar">
          <div class="progress-fill {pr_bar_cls}" style="width:{pr}%"></div>
        </div>
      </div>
    </div>
  </div>

  <div class="card col-4">
    <div class="card-title">Environment Health</div>
    <div style="margin-bottom:0.8rem">{env_pills}</div>
    <div class="terminal">
      <div><span class="t-green">$</span> ./check --all</div>
      <div class="t-grey"># per-environment success rates</div>
      {"".join(f'<div><span class="t-green">✓</span> {_env(e["name"])}: {e["pass"]}/{e["total"]} ({e["rate"]}%)</div>' for e in d["envs"]) or '<div class="t-grey">no data yet</div>'}
      <div><span class="cursor"></span></div>
    </div>
  </div>

  <div class="card col-4">
    <div class="card-title">Today's Batch</div>
    {batch_html}
  </div>

  <!-- ── recent tests ── -->

  <div class="card col-12">
    <div class="card-title">Recent Tests</div>
    <div class="terminal" style="max-height:360px;overflow-y:auto">
      <table>
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Package</th>
            <th>Env</th>
            <th>Status</th>
            <th>D&nbsp;N&nbsp;F&nbsp;I</th>
            <th>Failure</th>
          </tr>
        </thead>
        <tbody>
          {test_rows}
        </tbody>
      </table>
    </div>
  </div>

  <!-- ── classification ── -->

  <div class="section-label"><h2>CLASSIFICATION</h2></div>

  <div class="card col-4">
    <div class="card-title">Notable Findings</div>
    {finding_html}
  </div>

  <div class="card col-4">
    <div class="card-title">Package Archetypes</div>
    {arch_html}
  </div>

  <div class="card col-4">
    <div class="card-title">Cross-Env Asymmetries</div>
    {asym_html}
  </div>

  <!-- ── episodes ── -->

  <div class="section-label"><h2>EPISODE ARCHIVE</h2></div>

  <div class="card col-12">
    <div class="card-title">Broadcast History</div>
    {ep_html}
  </div>

</main>

<footer>
  <div>THE PYPI PLACE &nbsp;·&nbsp; <span class="blink">▮</span> {status_lbl} &nbsp;·&nbsp; DANGEROUSLY EDUCATIONAL</div>
  <div>One person. One Inspiron. Zero dollar data center. &nbsp;·&nbsp;
    <a href="https://github.com/PregnantPenguins789/vps-pypi-place">GitHub</a>
  </div>
  <div>generated {d['generated_at']}</div>
</footer>

</body>
</html>
"""


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Build The PyPI Place static dashboard")
    p.add_argument("--db",  default=_DEFAULT_DB,  help="SQLite DB path")
    p.add_argument("--out", default=_DEFAULT_OUT, help="Output HTML path")
    args = p.parse_args()

    db_path = Path(args.db)
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            data = _collect(conn)
        finally:
            conn.close()
    else:
        print(f"build: DB not found at {db_path} — generating empty dashboard", file=sys.stderr)
        data = dict(_EMPTY)
        data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = render(data)
    out  = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"build: wrote {out}  ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
