"""
Classify test results into failure types and behavioral archetypes.
The schema of what happened is derived here — not in the runner.
"""

import json
import logging
from watchdog import db

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# FAILURE CLASSIFICATION
# ─────────────────────────────────────────────

_BUILD_SIGNALS = [
    "building wheel", "error: command", "gcc", "clang", "rustc",
    "cargo build", "failed building", "compilation error",
    "cannot find", "No such file or directory", "linker",
]

_MISSING_DEP_SIGNALS = [
    "no matching distribution", "resolutionimpossible",
    "could not find a version", "no versions found",
    "package not found",
]

_BAD_METADATA_SIGNALS = [
    "invalid requirement", "metadata-version", "invalid metadata",
    "could not parse", "malformed", "bad metadata",
]

_VERSION_SIGNALS = [
    "requires-python", "python_requires", "requires python",
    "not supported on python", "python version",
]

_NETWORK_SIGNALS = [
    "connectionerror", "connection error", "timeout", "sslerror",
    "ssl error", "network", "unreachable", "refused",
]


def _match(text: str, signals: list[str]) -> bool:
    lowered = text.lower()
    return any(s in lowered for s in signals)


def classify_failure(phases: dict) -> str | None:
    """
    Given phase results dict with keys download_log, nodeps_log, full_log, import_log,
    and phase statuses, return a failure_type string or None if passed.
    """
    all_logs = " ".join(filter(None, [
        phases.get("download_log", ""),
        phases.get("nodeps_log", ""),
        phases.get("full_log", ""),
        phases.get("import_log", ""),
    ]))

    # Import error: full passed but import failed
    if phases.get("phase_full") == "PASS" and phases.get("phase_import") == "FAIL":
        return "import_error"

    # Check specific error signals in order of specificity
    if _match(all_logs, _VERSION_SIGNALS):
        return "version_incompatible"

    if _match(all_logs, _NETWORK_SIGNALS):
        return "network_failure"

    if _match(all_logs, _BUILD_SIGNALS):
        return "build_failure"

    if _match(all_logs, _MISSING_DEP_SIGNALS):
        return "missing_dep"

    if _match(all_logs, _BAD_METADATA_SIGNALS):
        return "bad_metadata"

    if phases.get("phase_full") == "TIMEOUT":
        return "timeout"

    # Generic failure — logs didn't match known patterns
    return "unknown"


def derive_status(phases: dict) -> str:
    """Derive overall status from phase results."""
    pf = phases.get("phase_full")
    pi = phases.get("phase_import")

    if pf == "TIMEOUT":
        return "TIMEOUT"

    if pf == "PASS" and pi == "PASS":
        return "PASS"

    if pf == "PASS" and pi == "FAIL":
        return "PHANTOM"

    if pf == "PASS" and pi == "SKIP":
        return "PASS"   # import name unknown, count as pass

    if phases.get("phase_download") == "FAIL":
        return "FAIL"

    if phases.get("phase_nodeps") == "FAIL":
        return "FAIL"

    if pf == "FAIL":
        return "FAIL"

    return "PARTIAL"


# ─────────────────────────────────────────────
# DEPENDENCY SURFACE
# ─────────────────────────────────────────────

def parse_dep_surface(pip_output: str) -> dict:
    """
    Parse 'pip install' stdout to extract dependency surface metrics.
    Returns dict with dep_count, wheel_count, source_count, compile_triggered.
    """
    lines = pip_output.lower()
    dep_count   = 0
    wheel_count = 0
    source_count = 0
    compile_triggered = 0

    # "Successfully installed X-1.0 Y-2.0 ..."
    for line in pip_output.splitlines():
        if line.strip().startswith("Successfully installed"):
            packages = line.replace("Successfully installed", "").strip().split()
            dep_count = len(packages)

    # Wheel vs source build counts
    wheel_count       = pip_output.lower().count(".whl")
    source_count      = pip_output.lower().count("building wheel")
    compile_triggered = 1 if _match(pip_output, _BUILD_SIGNALS) else 0

    return {
        "dep_count":          dep_count,
        "wheel_count":        wheel_count,
        "source_count":       source_count,
        "compile_triggered":  compile_triggered,
    }


# ─────────────────────────────────────────────
# ASYMMETRY DETECTION
# ─────────────────────────────────────────────

def detect_asymmetries(release_id: str):
    """
    After all environments have been tested for a release,
    detect cross-environment asymmetries and write to asymmetries table.
    """
    with db.transaction() as conn:
        rows = conn.execute("""
            SELECT environment, arch, status FROM test_results
            WHERE release_id = ?
        """, (release_id,)).fetchall()

    if len(rows) < 2:
        return

    passing = [r["environment"] for r in rows if r["status"] in ("PASS",)]
    failing = [r["environment"] for r in rows if r["status"] in ("FAIL", "TIMEOUT", "PHANTOM")]

    if not passing or not failing:
        return  # uniform result — no asymmetry

    # Detect arch split specifically
    pass_archs = set(r["arch"] for r in rows if r["status"] == "PASS")
    fail_archs = set(r["arch"] for r in rows if r["status"] in ("FAIL", "PHANTOM"))
    arch_split = bool(pass_archs & fail_archs) or (pass_archs != fail_archs and pass_archs and fail_archs)

    asymmetry_type = "arch_split" if arch_split else "env_split"

    with db.transaction() as conn:
        existing = conn.execute(
            "SELECT 1 FROM asymmetries WHERE release_id = ? AND asymmetry_type = ?",
            (release_id, asymmetry_type)
        ).fetchone()

        if not existing:
            conn.execute("""
                INSERT INTO asymmetries (release_id, asymmetry_type, passing_envs, failing_envs, detail)
                VALUES (?, ?, ?, ?, ?)
            """, (
                release_id,
                asymmetry_type,
                json.dumps(passing),
                json.dumps(failing),
                f"Passes in {len(passing)} env(s), fails in {len(failing)} env(s)",
            ))
            log.info("Asymmetry detected for %s: %s", release_id, asymmetry_type)


# ─────────────────────────────────────────────
# FINDINGS
# ─────────────────────────────────────────────

_ARCHETYPE_RULES = [
    ("phantom_success",  lambda r: r["status"] == "PHANTOM"),
    ("bitrot",           lambda r: r["failure_type"] == "version_incompatible"),
    ("native_gamble",    lambda r: r["failure_type"] == "build_failure"),
    ("dep_explosion",    lambda r: (r["dep_count"] or 0) > 20),
    ("clean_citizen",    lambda r: r["status"] == "PASS" and (r["dep_count"] or 0) <= 5),
]

def write_findings(release_id: str, results: list[dict]):
    """Derive findings from test results and write to findings table."""
    statuses  = [r["status"] for r in results]
    fail_envs = [r["environment"] for r in results if r["status"] not in ("PASS",)]

    with db.transaction() as conn:
        # Always-fails
        if all(s in ("FAIL", "TIMEOUT") for s in statuses):
            conn.execute("""
                INSERT OR IGNORE INTO findings (release_id, finding_type, detail, environments, severity)
                VALUES (?, 'always_fails', 'Fails in every tested environment', ?, 'significant')
            """, (release_id, json.dumps(fail_envs)))

        # Phantom success
        if any(r["status"] == "PHANTOM" for r in results):
            envs = [r["environment"] for r in results if r["status"] == "PHANTOM"]
            conn.execute("""
                INSERT OR IGNORE INTO findings (release_id, finding_type, detail, environments, severity)
                VALUES (?, 'phantom_success', 'Installs cleanly but cannot be imported', ?, 'critical')
            """, (release_id, json.dumps(envs)))

        # Dependency explosion
        heavy = [r for r in results if (r.get("dep_count") or 0) > 20]
        if heavy:
            max_deps = max(r["dep_count"] for r in heavy)
            conn.execute("""
                INSERT OR IGNORE INTO findings (release_id, finding_type, detail, environments, severity)
                VALUES (?, 'dep_explosion', ?, ?, 'notable')
            """, (release_id, f"Pulls {max_deps} dependencies", json.dumps([r["environment"] for r in heavy])))

        # Clean pass
        if all(s == "PASS" for s in statuses):
            conn.execute("""
                INSERT OR IGNORE INTO findings (release_id, finding_type, detail, environments, severity)
                VALUES (?, 'clean_pass', 'Passes all phases in all environments', ?, 'info')
            """, (release_id, json.dumps([r["environment"] for r in results])))
