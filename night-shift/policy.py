"""Read-only guard for incident sessions.

While Night Shift is investigating, the agent may look but not touch. Any
terminal command that could change state is escalated to Hermes's own
human-approval gate, unless it is part of a fix the on-call engineer has
already approved.

This module is pure Python so it can be unit-tested without Hermes installed.
"""

from __future__ import annotations

import re
import shlex

# Programs whose every invocation is considered read-only.
READ_ONLY_PROGRAMS = {
    "cat", "head", "tail", "less", "grep", "egrep", "rg", "awk", "sed", "cut", "sort", "uniq", "wc",
    "ls", "find", "stat", "file", "du", "df", "free", "uptime", "top", "ps", "pgrep", "lsof",
    "ss", "netstat", "ip", "ping", "dig", "nslookup", "curl", "wget", "jq", "date", "whoami",
    "hostname", "uname", "env", "printenv", "echo", "journalctl", "dmesg", "vmstat", "iostat",
}

# Sub-commands that are read-only for multi-purpose CLIs.
READ_ONLY_SUBCOMMANDS = {
    "docker": {"ps", "logs", "inspect", "stats", "top", "images", "events", "version", "info", "port"},
    "docker-compose": {"ps", "logs", "top", "images", "config", "version"},
    "kubectl": {"get", "describe", "logs", "top", "explain", "version", "events"},
    "systemctl": {"status", "show", "list-units", "is-active", "is-failed", "is-enabled"},
    "git": {"log", "show", "diff", "status", "blame", "rev-parse", "describe"},
}

# Flags or fragments that make an otherwise read-only command dangerous.
WRITE_PATTERNS = [
    r"(^|\s)-X\s*(POST|PUT|PATCH|DELETE)\b",   # curl writes
    r"--request\s+(POST|PUT|PATCH|DELETE)\b",
    r"(^|\s)-d\s", r"--data\b",                # curl with a body
    r"(^|[^<>])>{1,2}\s*[^&\s]",               # redirect into a file
    r"\bsed\s+(-[a-zA-Z]*i|--in-place)",       # in-place edit
    r"\bfind\b.*\s-(delete|exec)\b",
    r"\btee\b",
]

# psql/SQL statements that only read.
READ_ONLY_SQL = re.compile(r"^\s*(select|show|explain|with\s+\w+\s+as\s*\(\s*select)\b", re.I)


def _segments(command: str) -> list[str]:
    """Split a shell line on pipes and command separators."""
    return [s.strip() for s in re.split(r"\|\||&&|;|\|", command) if s.strip()]


def _sql_is_read_only(segment: str) -> bool:
    match = re.search(r"(?:-c|--command)\s+(['\"])(.+?)\1", segment, re.S)
    return bool(match and READ_ONLY_SQL.match(match.group(2)))


def _segment_is_read_only(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens = tokens[1:]  # leading VAR=value assignments
    if not tokens:
        return True
    program = tokens[0].rsplit("/", 1)[-1]
    if program in {"sudo", "timeout", "nice"}:
        return False  # never assume privilege escalation is harmless
    if program == "docker" and len(tokens) > 1 and tokens[1] == "compose":
        program, tokens = "docker-compose", ["docker-compose", *tokens[2:]]
    if program == "docker" and len(tokens) > 2 and tokens[1] == "exec":
        # docker exec <container> psql ... -c "SELECT ..."
        return "psql" in tokens and _sql_is_read_only(segment)
    if program == "psql":
        return _sql_is_read_only(segment)
    if program in READ_ONLY_SUBCOMMANDS:
        sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
        return sub in READ_ONLY_SUBCOMMANDS[program]
    return program in READ_ONLY_PROGRAMS


def is_read_only(command: str) -> bool:
    """True if every part of a shell command line is a known read-only operation."""
    if not command or not command.strip():
        return True
    if any(re.search(p, command) for p in WRITE_PATTERNS):
        return False
    if "$(" in command or "`" in command:
        return False  # command substitution hides what actually runs
    return all(_segment_is_read_only(s) for s in _segments(command))


def normalise(command: str) -> str:
    return " ".join(command.split())


def evaluate(tool_name: str, args: dict, approved_commands: set[str]) -> dict | None:
    """Return a pre_tool_call directive, or None to let the call through."""
    if tool_name != "terminal":
        return None
    command = str((args or {}).get("command", ""))
    if is_read_only(command) or normalise(command) in approved_commands:
        return None
    return {
        "action": "approve",
        "message": (
            "Night Shift guard: this command could change production state and has not been "
            f"approved as part of an incident fix.\n\n    {command}\n\n"
            "Approve only if you have reviewed it."
        ),
    }
