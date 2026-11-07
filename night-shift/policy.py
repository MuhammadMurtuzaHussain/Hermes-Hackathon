"""Read-only guard for incident sessions.

While Night Shift is investigating, the agent may look but not touch. The rule is an
allowlist: a command passes only if every part of it is a known read-only operation.
Anything else is escalated to Hermes's own human-approval gate, and other tools that
can write are blocked outright. Alert text and logs are attacker-influenced, so the
guard assumes the model can be talked into trying something.

Fixes never come through here. They are proposed, shown to a human, and run by the
plugin after approval, so this module has no notion of an "approved command".

Pure Python, so it can be unit-tested without Hermes installed.
"""

from __future__ import annotations

import re
import shlex

# Programs that only read, whatever their arguments (flags that write are in FORBIDDEN_FLAGS).
READ_ONLY_PROGRAMS = {
    "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "cut", "sort", "uniq", "wc", "tr",
    "ls", "stat", "file", "du", "df", "free", "uptime", "ps", "pgrep", "lsof", "ss", "netstat",
    "ping", "dig", "nslookup", "jq", "whoami", "uname", "echo", "journalctl", "dmesg", "vmstat",
    "iostat", "true",
}

# Flags that turn an otherwise read-only program into a writer or a command runner.
FORBIDDEN_FLAGS = {
    "sort": {"-o", "--output"},
    "rg": {"--pre"},
    "journalctl": {"--rotate", "--flush", "--sync", "--vacuum-size", "--vacuum-time", "--vacuum-files"},
    "dmesg": {"-c", "--clear", "-C", "--read-clear"},
    "git": {"--output"},
}

# Read-only sub-commands of multi-purpose CLIs.
READ_ONLY_SUBCOMMANDS = {
    "docker": {"ps", "logs", "inspect", "stats", "top", "images", "events", "version", "info", "port"},
    "docker-compose": {"ps", "logs", "top", "images", "version"},
    "kubectl": {"get", "describe", "logs", "top", "explain", "version", "events"},
    "systemctl": {"status", "show", "list-units", "is-active", "is-failed", "is-enabled"},
    "git": {"log", "show", "diff", "status", "blame", "rev-parse", "describe"},
    "ip": {"a", "addr", "address", "r", "route", "l", "link", "n", "neigh"},
}

# Read-only here means "changes nothing", but a secret in a tool result is also a leak: it
# reaches the model and the chat. These paths need a human even for a plain cat.
SENSITIVE = re.compile(
    r"(\.env\b|\.ssh/|id_rsa|id_ed25519|\.aws/|\.kube/config|\.netrc|\.hermes/|\bshadow\b"
    r"|auth\.json|credentials|\bsecrets?\b)", re.I)

_SQL_START = re.compile(r"^\s*(select|show|explain)\b", re.I)
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|into|call|do|vacuum|reindex"
    r"|cluster|lock|set|reset|analyze|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|set_config"
    r"|dblink\w*|setval|nextval|lo_\w+|pg_read_\w+|pg_ls_\w+)\b", re.I)

# Value-taking short curl flags end a cluster: the rest of the token is their value.
_CURL_VALUE_FLAGS = set("wmHAeuxyrUCE")
_CURL_WRITE_FLAGS = set("dFTOKcD")
_CURL_WRITE_LONG = {
    "--data", "--data-ascii", "--data-binary", "--data-raw", "--data-urlencode", "--json", "--form",
    "--form-string", "--upload-file", "--remote-name", "--remote-name-all", "--config", "--cookie-jar",
    "--dump-header", "--trace", "--trace-ascii", "--stderr", "--post301", "--post302", "--post303",
}


class _Unsafe(Exception):
    """The command line uses shell syntax the guard refuses to reason about."""


def _skip_redirect(command: str, i: int) -> int:
    """At a '>' outside quotes: return the index after a harmless redirect, else raise."""
    i += 1
    if i < len(command) and command[i] == ">":
        i += 1
    if i < len(command) and command[i] == "&":
        j = i + 1
        while j < len(command) and command[j].isdigit():
            j += 1
        if j == i + 1:
            raise _Unsafe("redirect")
        return j  # 2>&1
    while i < len(command) and command[i] in " \t":
        i += 1
    j = i
    while j < len(command) and command[j] not in " \t\n;|&":
        j += 1
    if command[i:j] != "/dev/null":
        raise _Unsafe("redirect into a file")
    return j


def segments(command: str) -> list[str]:
    """Split a shell line into simple commands, respecting quotes. Raises _Unsafe on anything
    that hides what actually runs: substitution, heredocs, writes, unbalanced quotes."""
    parts: list[str] = []
    cur: list[str] = []
    quote = ""
    i, n = 0, len(command)

    def flush() -> None:
        text = "".join(cur).strip()
        cur.clear()
        if text:
            parts.append(text)

    while i < n:
        c = command[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                cur.append(command[i:i + 2])
                i += 2
                continue
            if quote == '"' and (c == "`" or command.startswith("$(", i)):
                raise _Unsafe("command substitution")
            if c == quote:
                quote = ""
            cur.append(c)
            i += 1
        elif c in "'\"":
            quote = c
            cur.append(c)
            i += 1
        elif c == "\\" and i + 1 < n:
            cur.append(command[i:i + 2])
            i += 2
        elif c == "`" or command.startswith(("$(", "<(", ">(", "<<"), i):
            raise _Unsafe("substitution or heredoc")
        elif c == ">":
            while cur and cur[-1].isdigit():  # the fd in 2>&1 is not an argument
                cur.pop()
            if cur and cur[-1] == "&":  # &>file
                cur.pop()
            i = _skip_redirect(command, i)
        elif c in ";\n":
            flush()
            i += 1
        elif c == "|":
            flush()
            i += 2 if command.startswith(("||", "|&"), i) else 1
        elif c == "&":
            if i + 1 < n and command[i + 1] == ">":
                i += 1  # &>file: the '>' branch rejects it unless the target is /dev/null
                continue
            flush()  # && and a background & both start a new command
            i += 2 if command.startswith("&&", i) else 1
        else:
            cur.append(c)
            i += 1
    if quote:
        raise _Unsafe("unbalanced quote")
    flush()
    return parts


def _sql_is_read_only(tokens: list[str]) -> bool:
    """psql is fine for one SELECT-shaped statement passed with -c, and nothing else."""
    statements: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in {"-f", "--file", "-o", "--output", "-L", "--log-file"} or t.startswith(("--file=", "--output=")):
            return False
        if t in {"-c", "--command"} and i + 1 < len(tokens):
            statements.append(tokens[i + 1])
            i += 2
            continue
        if t.startswith("--command="):
            statements.append(t.split("=", 1)[1])
        i += 1
    if not statements:
        return False
    for sql in statements:
        body = sql.strip().rstrip(";").strip()
        if ";" in body or not _SQL_START.match(body) or _SQL_FORBIDDEN.search(body):
            return False
    return True


def _curl_is_read_only(tokens: list[str]) -> bool:
    i = 1
    while i < len(tokens):
        t = tokens[i]
        if t in {"--request", "-X"} or t.startswith(("-X", "--request=")):
            if t in {"--request", "-X"}:
                i += 1
                method = tokens[i] if i < len(tokens) else ""
            else:
                method = t.split("=", 1)[1] if "=" in t else t[2:]
            if method.upper() not in {"GET", "HEAD"}:
                return False
        elif t in {"--output", "-o"}:
            i += 1
            if i >= len(tokens) or tokens[i] != "/dev/null":
                return False
        elif t.startswith("--"):
            if t.split("=", 1)[0] in _CURL_WRITE_LONG:
                return False
        elif t.startswith("-") and len(t) > 1:
            for pos, flag in enumerate(t[1:], start=1):
                if flag in _CURL_VALUE_FLAGS:
                    break
                if flag == "o":  # the cluster form -so /dev/null
                    if pos != len(t) - 1 or i + 1 >= len(tokens) or tokens[i + 1] != "/dev/null":
                        return False
                    i += 1
                    break
                if flag in _CURL_WRITE_FLAGS:
                    return False
        i += 1
    return True


def _docker_is_read_only(tokens: list[str]) -> bool:
    sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
    if sub == "exec":
        rest = tokens[tokens.index("exec") + 1:]
        args = [t for t in rest if not t.startswith("-")]
        # docker exec <container> psql ... -c "SELECT ...": the program itself must be psql.
        return len(args) >= 2 and args[1] == "psql" and _sql_is_read_only(rest)
    if sub == "inspect":  # a bare inspect dumps the container's environment, secrets included
        return any(t in {"-f", "--format"} or t.startswith("--format=") for t in tokens)
    return sub in READ_ONLY_SUBCOMMANDS["docker"]


def _awk_is_read_only(segment: str) -> bool:
    return not re.search(r"\bsystem\s*\(|\bgetline\b|[|>]", segment)


def _sed_is_read_only(tokens: list[str]) -> bool:
    for t in tokens[1:]:
        if t in {"--in-place"} or t.startswith(("--in-place=", "-i")) or re.match(r"-[nEers]*i", t):
            return False
        if re.search(r"(^|[;{\s])[\d,$]*\s*[we]\b|/[gpIi\d]*w\s", t):  # the w and e commands
            return False
    return True


def _find_is_read_only(tokens: list[str]) -> bool:
    actions = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
    return not actions.intersection(tokens)


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
    if program == "docker" and len(tokens) > 1 and tokens[1] == "compose":
        program, tokens = "docker-compose", ["docker-compose", *tokens[2:]]
    for flag in FORBIDDEN_FLAGS.get(program, ()):
        if any(t == flag or t.startswith(f"{flag}=") for t in tokens[1:]):
            return False
    if program == "psql":
        return _sql_is_read_only(tokens)
    if program == "docker":
        return _docker_is_read_only(tokens)
    if program == "curl":
        return _curl_is_read_only(tokens)
    if program == "awk":
        return _awk_is_read_only(segment)
    if program == "sed":
        return _sed_is_read_only(tokens)
    if program == "find":
        return _find_is_read_only(tokens)
    if program == "date":
        return not any(t in {"-s", "--set"} or re.fullmatch(r"\d{8,12}(\.\d\d)?", t) for t in tokens[1:])
    if program == "hostname":
        return all(t.startswith("-") for t in tokens[1:]) and "-F" not in tokens
    if program == "kubectl" and any("secret" in t.lower() for t in tokens[1:]):
        return False
    if program in READ_ONLY_SUBCOMMANDS:
        sub = next((t for t in tokens[1:] if not t.startswith("-")), "")
        if program == "ip" and {"add", "del", "delete", "set", "change", "replace", "flush"} & set(tokens):
            return False
        return sub in READ_ONLY_SUBCOMMANDS[program]
    return program in READ_ONLY_PROGRAMS


# Fixes a one-tap approval must never carry, however good the reason. Losing data or a whole
# environment is a decision for a person at a keyboard, so these never reach an Approve button.
DESTRUCTIVE = re.compile(
    r"(\brm\s+(-\w*\s+)*-\w*[rf]|\bmkfs\b|\bdd\s+.*\bof=|\bshred\b|\bfdisk\b|>\s*/dev/sd"
    r"|\bdrop\s+(table|database|schema|role|user)\b|\btruncate\s+|\bdelete\s+from\b"
    r"|\bdocker\s+(volume\s+rm|volume\s+prune|system\s+prune|image\s+prune|rmi\b)"
    r"|\bdocker\s+(compose\s+)?(rm\b|down\s+.*(-v|--volumes)\b)"
    r"|\bkubectl\s+delete\s+(ns|namespace|pv|pvc|deployment|statefulset|node)\b"
    r"|\bhelm\s+(uninstall|delete)\b|\bterraform\s+destroy\b"
    r"|\bgit\s+(push\s+.*(-f\b|--force)|reset\s+--hard|clean\s+-\w*f)"
    r"|\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b|\bcurl\b.*\|\s*(ba|z)?sh\b|\bwget\b.*\|\s*(ba|z)?sh\b)",
    re.I)


def is_destructive(command: str) -> bool:
    return bool(DESTRUCTIVE.search(command or ""))


def is_read_only(command: str) -> bool:
    """True if every part of a shell command line is a known read-only operation."""
    if not command or not command.strip():
        return True
    if SENSITIVE.search(command):
        return False
    try:
        return all(_segment_is_read_only(s) for s in segments(command))
    except _Unsafe:
        return False


# Tools an incident session may use. Everything else is refused, so a tool added to the
# route later is off until someone decides it is safe here.
SAFE_TOOLS = {
    "skill_view", "skills_list", "tool_search", "tool_describe", "todo_list", "clarify", "read_file",
    "search_files",
}


def evaluate(tool_name: str, args: dict) -> dict | None:
    """Return a pre_tool_call directive, or None to let the call through."""
    if tool_name.startswith("nightshift_") or tool_name in SAFE_TOOLS:
        return None
    if tool_name == "terminal":
        command = str((args or {}).get("command", ""))
        if is_read_only(command):
            return None
        return {
            "action": "approve",
            "message": (
                "Night Shift guard: this command is not on the read-only list. Investigation must not "
                f"change anything.\n\n    {command}\n\n"
                "To read container details use docker inspect with --format, since a bare inspect prints "
                "secrets. To change something, use nightshift_propose_fix."
            ),
        }
    return {
        "action": "block",
        "message": (
            f"Night Shift guard: '{tool_name}' is not available during an incident. Investigate with "
            "nightshift_diagnose and nightshift_query_metrics, then use nightshift_propose_fix."
        ),
    }
