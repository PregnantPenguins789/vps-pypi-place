"""
Batch test runner.

For each environment:
  - Start one persistent container (warm mode) or fresh per package (cold mode)
  - Run four phases per package: download, nodeps, full, import
  - Write fingerprinted results to test_results table
  - Classify failures and detect asymmetries after each release is complete
"""

import subprocess
import logging
import random
import time
from watchdog import db, classifier
from watchdog.config import get

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# DOCKER PRIMITIVES
# ─────────────────────────────────────────────

def _run(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run a command. Returns (returncode, stdout, stderr)."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def _docker_start(image: str) -> str | None:
    """Start a detached container, return container ID or None on failure."""
    code, out, err = _run(
        ["docker", "run", "-d", "--rm", image, "sleep", "3600"],
        timeout=60,
    )
    if code != 0:
        log.error("Failed to start container for %s: %s", image, err)
        return None
    return out.strip()


def _docker_stop(container_id: str):
    _run(["docker", "stop", container_id], timeout=30)


def _docker_exec(container_id: str, cmd: list[str], timeout: int) -> tuple[int, str, str]:
    return _run(["docker", "exec", container_id] + cmd, timeout=timeout)


def _docker_run_once(image: str, cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run a command in a fresh ephemeral container."""
    return _run(["docker", "run", "--rm", image] + cmd, timeout=timeout)


def _get_import_name(container_id: str, package: str, timeout: int) -> str:
    """
    Best-effort: find the importable name for a package.
    Tries: normalized package name, then top_level.txt from dist-info.
    """
    normalized = package.lower().replace("-", "_").replace(".", "_")

    # Try to read top_level.txt from installed dist-info
    code, out, _ = _docker_exec(container_id, [
        "python", "-c",
        f"""
import pathlib, sys
for p in sys.path:
    for d in pathlib.Path(p).glob('{package.replace("-","_").replace(".","_")}*.dist-info'):
        tl = d / 'top_level.txt'
        if tl.exists():
            print(tl.read_text().strip().splitlines()[0])
            raise SystemExit(0)
print('{normalized}')
"""
    ], timeout=timeout)

    name = out.strip().splitlines()[0] if out.strip() else normalized
    return name or normalized


# ─────────────────────────────────────────────
# FOUR-PHASE TEST
# ─────────────────────────────────────────────

def _phase_download(container_id: str, package: str, cfg: dict) -> tuple[str, str]:
    """Phase 1: pip download --no-deps (metadata + resolution only)."""
    code, out, err = _docker_exec(container_id, [
        "pip", "download", "--no-deps", "--no-cache-dir", "-d", "/tmp/pkgdl", package
    ], timeout=cfg["install_timeout_sec"])

    if code == -1 and err == "TIMEOUT":
        return "TIMEOUT", err
    return ("PASS" if code == 0 else "FAIL"), (err or out)[-1000:]


def _phase_nodeps(container_id: str, package: str, cfg: dict) -> tuple[str, str]:
    """Phase 2: pip install --no-deps (package integrity, no dependency graph)."""
    code, out, err = _docker_exec(container_id, [
        "pip", "install", "--no-deps", "--no-cache-dir", package
    ], timeout=cfg["install_timeout_sec"])

    if code == -1 and err == "TIMEOUT":
        return "TIMEOUT", err
    return ("PASS" if code == 0 else "FAIL"), (err or out)[-1000:]


def _phase_full(container_id: str, package: str, cfg: dict) -> tuple[str, str, str]:
    """Phase 3: pip install (full dependency resolution). Returns status, log, raw stdout."""
    code, out, err = _docker_exec(container_id, [
        "pip", "install", "--no-cache-dir", package
    ], timeout=cfg["install_timeout_sec"])

    if code == -1 and err == "TIMEOUT":
        return "TIMEOUT", err, ""
    status = "PASS" if code == 0 else "FAIL"
    return status, (err or out)[-1000:], out


def _phase_import(container_id: str, package: str, cfg: dict) -> tuple[str, str]:
    """Phase 4: python -c 'import X' (runtime viability)."""
    import_name = _get_import_name(container_id, package, timeout=10)
    if not import_name:
        return "SKIP", "Could not determine import name"

    code, out, err = _docker_exec(container_id, [
        "python", "-c", f"import {import_name}; print('OK')"
    ], timeout=30)

    return ("PASS" if code == 0 else "FAIL"), (err or out)[-500:]


# ─────────────────────────────────────────────
# ALREADY TESTED CHECK
# ─────────────────────────────────────────────

def _already_tested(release_id: str, env_name: str, arch: str) -> bool:
    with db.connect() as conn:
        row = conn.execute("""
            SELECT 1 FROM test_results
            WHERE release_id = ? AND environment = ? AND arch = ?
        """, (release_id, env_name, arch)).fetchone()
    return row is not None


# ─────────────────────────────────────────────
# SINGLE PACKAGE TEST
# ─────────────────────────────────────────────

def _test_package(container_id: str, release: dict, env: dict,
                  isolation: str, phase_cfg: dict, watchdog_cfg: dict) -> dict:
    """
    Run all four phases for one package in one environment.
    Returns a result dict ready for insertion into test_results.
    """
    package = release["package"]
    t_start = time.monotonic()

    log.info("  Testing %s on %s [%s]", package, env["name"], isolation)

    # Phase 1 — download
    p_download, dl_log = ("SKIP", None)
    if phase_cfg.get("run_download", True):
        p_download, dl_log = _phase_download(container_id, package, watchdog_cfg)
        log.debug("    download: %s", p_download)

    # Phase 2 — nodeps
    p_nodeps, nd_log = ("SKIP", None)
    if phase_cfg.get("run_nodeps", True):
        if phase_cfg.get("abort_on_download_fail") and p_download == "FAIL":
            p_nodeps, nd_log = "SKIP", "Skipped: download failed"
        else:
            p_nodeps, nd_log = _phase_nodeps(container_id, package, watchdog_cfg)
            log.debug("    nodeps:   %s", p_nodeps)

    # Phase 3 — full install
    p_full, full_log, full_stdout = ("SKIP", None, "")
    if phase_cfg.get("run_full", True):
        if phase_cfg.get("abort_on_nodeps_fail") and p_nodeps == "FAIL":
            p_full, full_log = "SKIP", "Skipped: nodeps failed"
        else:
            p_full, full_log, full_stdout = _phase_full(container_id, package, watchdog_cfg)
            log.debug("    full:     %s", p_full)

    # Phase 4 — import
    p_import, import_log = ("SKIP", None)
    if phase_cfg.get("run_import", True):
        if phase_cfg.get("abort_on_full_fail") and p_full in ("FAIL", "TIMEOUT", "SKIP"):
            p_import, import_log = "SKIP", "Skipped: full install failed"
        else:
            p_import, import_log = _phase_import(container_id, package, watchdog_cfg)
            log.debug("    import:   %s", p_import)

    install_ms = int((time.monotonic() - t_start) * 1000)

    phases = {
        "phase_download": p_download,
        "phase_nodeps":   p_nodeps,
        "phase_full":     p_full,
        "phase_import":   p_import,
        "download_log":   dl_log,
        "nodeps_log":     nd_log,
        "full_log":       full_log,
        "import_log":     import_log,
    }

    status       = classifier.derive_status(phases)
    failure_type = classifier.classify_failure(phases) if status != "PASS" else None
    dep_surface  = classifier.parse_dep_surface(full_stdout)

    log.info("  → %s %s [%s] %s", package, env["name"], isolation, status)

    return {
        "release_id":         release["id"],
        "environment":        env["name"],
        "arch":               env["arch"],
        "isolation":          isolation,
        "phase_download":     p_download,
        "phase_nodeps":       p_nodeps,
        "phase_full":         p_full,
        "phase_import":       p_import,
        "status":             status,
        "failure_type":       failure_type,
        "install_ms":         install_ms,
        "download_log":       dl_log,
        "nodeps_log":         nd_log,
        "full_log":           full_log,
        "import_log":         import_log,
        **dep_surface,
    }


def _write_result(result: dict):
    with db.transaction() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO test_results (
                release_id, environment, arch,
                phase_download, phase_nodeps, phase_full, phase_import,
                status, failure_type, install_ms,
                dep_count, wheel_count, source_count, compile_triggered,
                download_log, nodeps_log, full_log, import_log
            ) VALUES (
                :release_id, :environment, :arch,
                :phase_download, :phase_nodeps, :phase_full, :phase_import,
                :status, :failure_type, :install_ms,
                :dep_count, :wheel_count, :source_count, :compile_triggered,
                :download_log, :nodeps_log, :full_log, :import_log
            )
        """, result)


# ─────────────────────────────────────────────
# MAIN BATCH RUN
# ─────────────────────────────────────────────

def run_batch(releases: list[dict]):
    """
    Test all releases across all enabled environments.
    Uses warm containers (one per environment) with cold sampling.
    """
    if not releases:
        log.info("No releases to test.")
        return

    cfg          = get("watchdog")
    phase_cfg    = get("phases")
    envs         = [e for e in get("environments") if e.get("enabled", True)]
    cold_rate    = cfg.get("cold_sample_rate", 0.05)

    log.info("Starting batch: %d releases × %d environments", len(releases), len(envs))

    for env in envs:
        image = env["image"]
        log.info("Environment: %s (%s)", env["name"], image)

        # Start warm container for this environment
        warm_container = _docker_start(image)
        if warm_container is None:
            log.error("Could not start container for %s — skipping environment", image)
            continue

        try:
            for release in releases:
                arch = env["arch"]

                if _already_tested(release["id"], env["name"], arch):
                    log.debug("Already tested: %s on %s", release["package"], env["name"])
                    continue

                # Determine isolation mode for this package
                use_cold = (cfg.get("default_isolation") == "cold") or \
                           (random.random() < cold_rate)
                isolation = "cold" if use_cold else "warm"

                if use_cold:
                    # Fresh container per package
                    cold_container = _docker_start(image)
                    if cold_container is None:
                        log.warning("Could not start cold container — falling back to warm")
                        use_cold   = False
                        isolation  = "warm"
                        container  = warm_container
                    else:
                        container = cold_container
                else:
                    container = warm_container

                try:
                    result = _test_package(
                        container, release, env, isolation, phase_cfg, cfg
                    )
                    _write_result(result)
                finally:
                    if use_cold and cold_container:
                        _docker_stop(cold_container)

        finally:
            _docker_stop(warm_container)
            log.info("Stopped warm container for %s", env["name"])

    # Post-batch: asymmetries + findings
    log.info("Classifying findings and asymmetries...")
    for release in releases:
        with db.connect() as conn:
            results = [dict(r) for r in conn.execute("""
                SELECT * FROM test_results WHERE release_id = ?
            """, (release["id"],)).fetchall()]

        if results:
            classifier.detect_asymmetries(release["id"])
            classifier.write_findings(release["id"], results)

    log.info("Batch complete.")
