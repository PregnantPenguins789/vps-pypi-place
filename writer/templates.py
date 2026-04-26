"""
templates.py — Broadcast script sentence templates for The PyPI Place.

Each function returns a string. Caller (script_builder) supplies the data.
Templates use no LLM — all deterministic from structured inputs.

Voice register: flat, bureaucratic, mildly exhausted. The humor is in
the format, not the words. A machine reporting the news. Every night.
"""

import random
from datetime import datetime


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _date_spoken(date_str: str) -> str:
    """Convert YYYY-MM-DD to 'April 25, 2026'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%B %-d, %Y")
    except ValueError:
        return date_str


def _env_short(env: str) -> str:
    """'python:3.11-slim' → '3.11-slim'"""
    return env.replace("python:", "").strip()


def _env_list(envs: list) -> str:
    """['python:3.9-slim', 'python:3.11-slim'] → '3.9-slim and 3.11-slim'"""
    shortened = [_env_short(e) for e in envs]
    if not shortened:
        return "no environments"
    if len(shortened) == 1:
        return shortened[0]
    return ", ".join(shortened[:-1]) + " and " + shortened[-1]


def _summary_sentence(summary: str | None, package: str) -> str:
    """
    Turn a PyPI summary string into a spoken sentence fragment.
    Handles None, empty, and overly long summaries.
    """
    if not summary or not summary.strip():
        return f"{package} did not describe itself."
    s = summary.strip().rstrip(".")
    # Truncate to ~80 chars to keep spoken sentence manageable
    if len(s) > 90:
        s = s[:87].rsplit(" ", 1)[0] + "..."
    return f"It describes itself as: {s}."


def _failure_sentence(failure_type: str | None, pkg: dict) -> str:
    ft = (failure_type or "unknown").lower()
    if ft == "build_failure":
        return "It needed to compile. The compiler disagreed."
    if ft == "missing_dep":
        return "A required dependency could not be located."
    if ft == "version_incompatible":
        return "It has opinions about which Python versions are acceptable. This was not one of them."
    if ft == "bad_metadata":
        return "The package metadata was malformed. pip could not proceed."
    if ft == "timeout":
        return f"Installation exceeded the time limit. The machine moved on."
    if ft == "import_error":
        return "It installed. It could not be imported. These are not the same thing."
    if ft == "network_failure":
        return "A network error occurred during installation."
    return "The failure type was not recognized."


# ─────────────────────────────────────────────
# SEGMENTS
# ─────────────────────────────────────────────

def opener(date: str, ov: dict) -> str:
    spoken_date = _date_spoken(date)
    pt = ov["packages_tested"]
    bt = ov["total_tests"]
    pc = ov["pass_count"]
    fc = ov["fail_count"]
    ph = ov["phantom_count"]
    pr = ov["pass_rate"]

    phantom_note = ""
    if ph > 0:
        phantom_note = (
            f" {ph} installed but could not be imported. "
            "We will discuss those separately."
        )

    return f"""\
Good evening. The date is {spoken_date}. I am your host. This is The PyPI Place.

In the past twenty-four hours, {pt} Python packages were submitted to the index. \
We tested {bt} of them across multiple environments. \
{pc} passed. {fc} failed.{phantom_note}

The overall pass rate: {pr} percent.

Let us begin.\
"""


def numbers(ov: dict) -> str:
    lines = ["The numbers."]
    lines.append(
        f"{ov['pass_count']} passes. "
        f"{ov['fail_count']} failures. "
        f"{ov['timeout_count']} timeouts. "
        f"{ov['phantom_count']} phantoms."
    )

    if ov.get("heaviest_package"):
        lines.append(
            f"{ov['heaviest_package']} was the heaviest package today. "
            f"It brought {ov['heaviest_dep_count']} dependencies with it."
        )

    if ov.get("compile_count", 0) > 0:
        lines.append(
            f"{ov['compile_count']} packages attempted to compile native code."
        )

    if ov.get("arch_split_count", 0) > 0:
        n = ov["arch_split_count"]
        lines.append(
            f"{n} package{'s' if n != 1 else ''} showed different results "
            "across environments. Those are covered in the asymmetries report."
        )

    lines.append("The machines are running. The numbers are what they are.")
    return "\n\n".join(lines)


def passes_intro(count: int) -> str:
    noun = "package" if count == 1 else "packages"
    return (
        f"Now the ones that worked. "
        f"We are featuring {count} {noun} today. "
        "They installed. They imported. They are in the record."
    )


def package_pass(pkg: dict) -> str:
    name     = pkg["package"]
    version  = pkg["version"]
    dep_cnt  = pkg.get("dep_count") or 0
    ms       = pkg.get("install_ms") or 0
    env_cnt  = pkg.get("env_count") or 1

    summary  = _summary_sentence(pkg.get("summary"), name)

    dep_note = ""
    if dep_cnt == 0:
        dep_note = "No additional dependencies."
    elif dep_cnt == 1:
        dep_note = "One dependency."
    else:
        dep_note = f"{dep_cnt} dependencies."

    ms_note = ""
    if ms > 0:
        if ms < 1000:
            ms_note = f"Installed in under one second."
        elif ms < 10000:
            ms_note = f"Installed in {ms // 1000} seconds."
        else:
            ms_note = f"Installed in {ms // 1000} seconds."

    env_note = ""
    if env_cnt > 1:
        env_note = f"Passed across {env_cnt} environments."

    parts = [f"{name}. Version {version}.", summary, dep_note]
    if ms_note:
        parts.append(ms_note)
    if env_note:
        parts.append(env_note)
    parts.append("It imports.")

    return " ".join(p for p in parts if p)


def failures_intro(count: int) -> str:
    noun = "package" if count == 1 else "packages"
    return (
        f"Now the failures. "
        f"{count} {noun} did not make it through."
    )


def package_fail(pkg: dict) -> str:
    name    = pkg["package"]
    version = pkg["version"]
    summary = _summary_sentence(pkg.get("summary"), name)
    failure = _failure_sentence(pkg.get("failure_type"), pkg)

    envs = pkg.get("environments") or ""
    env_list = [e.strip() for e in str(envs).split(",") if e.strip()]
    env_note = ""
    if len(env_list) > 1:
        env_note = f"Tested across {len(env_list)} environments. Same result in all of them."

    parts = [f"{name}. Version {version}.", summary, failure]
    if env_note:
        parts.append(env_note)

    return " ".join(p for p in parts if p)


def phantoms_intro() -> str:
    return """\
We now come to the phantoms.

These are packages that installed without complaint. \
pip reported success. The files are on disk. \
We then asked Python if it knew them. Python did not answer.

A phantom package is here and it is not here. \
We note them. We record them. We report them.\
"""


def package_phantom(pkg: dict) -> str:
    name    = pkg["package"]
    version = pkg["version"]
    summary = _summary_sentence(pkg.get("summary"), name)

    return (
        f"{name}. Version {version}. "
        f"{summary} "
        f"Installed. Present on disk. "
        f"Absent from the import system. "
        f"A phantom."
    )


def asymmetries_intro() -> str:
    return (
        "Now the asymmetries. "
        "These are packages that produced different results "
        "in different environments. Same package. Different answer."
    )


def _classify_asymmetry(passing_envs: list, failing_envs: list) -> str:
    """
    Derive a spoken asymmetry label from the actual environment names,
    ignoring the stored asymmetry_type which conflates arch/version splits
    when all environments share the same arch.
    """
    all_envs = passing_envs + failing_envs
    archs = set()
    for e in all_envs:
        if "alpine" in e:
            archs.add("alpine")
        elif "arm64" in e or "aarch64" in e:
            archs.add("arm64")
        elif "amd64" in e or "x86" in e:
            archs.add("x86")
    # If environments differ only by Python version (e.g. 3.9 vs 3.12), say so
    # Detect by checking if all env names share the same distro suffix
    suffixes = set()
    for e in all_envs:
        short = _env_short(e)
        # "3.11-slim" → "slim", "3.12-alpine" → "alpine"
        parts = short.split("-", 1)
        suffixes.add(parts[1] if len(parts) > 1 else "")
    if len(suffixes) == 1:
        return "Python version split"
    if len(archs) > 1:
        return "architecture split"
    return "environment split"


def asymmetry(a: dict) -> str:
    name    = a["package"]
    version = a["version"]
    passing_envs = a.get("passing_envs") or []
    failing_envs = a.get("failing_envs") or []
    passing = _env_list(passing_envs)
    failing = _env_list(failing_envs)
    atype   = _classify_asymmetry(passing_envs, failing_envs)

    summary = ""
    if a.get("summary"):
        s = a["summary"].strip().rstrip(".")
        if len(s) > 70:
            s = s[:67].rsplit(" ", 1)[0] + "..."
        summary = f"{s}. "

    return (
        f"{name}. Version {version}. {summary}"
        f"On {passing}: passes. On {failing}: fails. "
        f"Classification: {atype}. "
        "The machine noticed. The difference has been recorded."
    )


_COMMERCIALS = [
    """\
We will be right back.

[STATIC]

This portion of The PyPI Place is brought to you by dependencies. \
Dependencies: they were there when you installed it. Were they there when you needed them? \
Find out tonight.

[STATIC]\
""",
    """\
We will be right back.

[STATIC]

The PyPI Place is not responsible for packages that claim to do things \
they cannot do. Neither is PyPI. Neither are you, probably. \
We have all made choices.

[STATIC]\
""",
    """\
We will be right back.

[STATIC]

A brief word from our sponsor, pip. pip: it either works or it tells you why. \
Sometimes it tells you why in a way that makes sense. \
pip has been doing this since 2008. pip is tired.

[STATIC]\
""",
    """\
We will be right back.

[STATIC]

If you are watching this broadcast, you understand why it exists. \
We test the packages. We report the results. We do this every day. \
For you. You are welcome.

[STATIC]\
""",
    """\
We will be right back.

[STATIC]

This portion of The PyPI Place is brought to you by Sovereign Mail. \
Sovereign Mail: at least we fucking try. \
What have you done. \
Available now. Probably won't work. \
T-shirts also available.

[STATIC]\
""",
]


def commercial() -> str:
    return random.choice(_COMMERCIALS)


def signoff(date: str, ov: dict) -> str:
    spoken_date = _date_spoken(date)
    pt  = ov["packages_tested"]
    pr  = ov["pass_rate"]
    fc  = ov["fail_count"]
    ph  = ov["phantom_count"]

    survivor_note = ""
    if fc > 0 or ph > 0:
        total_not_pass = fc + ph
        survivor_note = (
            f" {total_not_pass} did not. "
            "They are in the record."
        )
    else:
        survivor_note = " All of them passed. This is unusual."

    return f"""\
That is the report for {spoken_date}.

{pt} packages tested. {pr} percent passed.{survivor_note}

I am your host. The machine will run again tomorrow.

This has been The PyPI Place.

Goodnight.\
"""
