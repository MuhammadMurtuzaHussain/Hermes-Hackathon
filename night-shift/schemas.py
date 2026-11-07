"""Tool schemas: what the model reads to decide when to call each tool."""

DIAGNOSE = {
    "name": "nightshift_diagnose",
    "description": (
        "Run Night Shift's read-only diagnostic checks for a service during an incident: container "
        "status, recent logs, resource usage, disk, database connections and deploy history. Opens "
        "an incident record, and returns any learned runbook that matches. Call this FIRST when an "
        "alert arrives, before forming any theory. It never changes anything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Service or container name, e.g. 'api'."},
            "alertname": {"type": "string", "description": "Alert name from the monitoring system."},
            "summary": {"type": "string", "description": "One-line alert summary, if known."},
            "incident_id": {"type": "string", "description": "Existing incident id to attach evidence to."},
            "checks": {
                "type": "array",
                "items": {"type": "string", "enum": ["status", "logs", "resources", "disk", "db", "deploys"]},
                "description": "Subset of checks to run. Defaults to all.",
            },
        },
        "required": ["service", "alertname"],
    },
}

QUERY_METRICS = {
    "name": "nightshift_query_metrics",
    "description": (
        "Run a PromQL instant query against Prometheus and return the result. Use it to check "
        "error rates, saturation or when a metric changed, e.g. "
        "'sum(pg_stat_activity_count)' or 'rate(http_requests_total{status=~\"5..\"}[5m])'."
    ),
    "parameters": {
        "type": "object",
        "properties": {"promql": {"type": "string", "description": "PromQL expression."}},
        "required": ["promql"],
    },
}

RECALL = {
    "name": "nightshift_recall",
    "description": (
        "Look up runbooks Night Shift learned from past incidents. nightshift_diagnose already does "
        "this for an exact alert match, so only call this to search with different symptoms."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "alertname": {"type": "string"},
            "service": {"type": "string"},
            "symptoms": {"type": "string", "description": "What the diagnosis showed, in a sentence or two."},
        },
        "required": ["alertname", "service"],
    },
}

PROPOSE_FIX = {
    "name": "nightshift_propose_fix",
    "description": (
        "Propose a remediation for an open incident. It is NOT executed by you. The on-call engineer "
        "sees the diagnosis and the exact commands and approves or denies. After approval Night "
        "Shift runs the commands, checks the verification and writes the runbook. Use the smallest "
        "fix that addresses the cause, and state the risk honestly. For a known failure, pass "
        "`runbook` and a one-line diagnosis: everything else comes from the runbook. If no fix is "
        "justified, call nightshift_escalate instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "diagnosis": {"type": "string", "description": "Root cause in plain English, with the evidence."},
            "symptoms": {"type": "string", "description": "Observable symptoms that identify this failure."},
            "commands": {
                "type": "array", "items": {"type": "string"},
                "description": ("Exact shell commands that fix the cause, run in order. Only commands that "
                                "change something: checks belong in verify_promql or verify_command."),
            },
            "risk": {"type": "string", "enum": ["low", "medium", "high"]},
            "verification": {"type": "string", "description": "In words, how recovery is confirmed."},
            "verify_promql": {
                "type": "string",
                "description": ("PromQL with a comparison that returns a result only once healthy, e.g. "
                                "'sum(pg_stat_activity_count) / max(pg_settings_max_connections) < 0.5'. "
                                "It must not already be true now."),
            },
            "verify_command": {
                "type": "string",
                "description": "Read-only shell command that exits 0 once healthy. Use with or instead of verify_promql.",
            },
            "runbook": {"type": "string", "description": "Name of a learned runbook to reuse, from known_runbook."},
        },
        "required": ["incident_id", "diagnosis"],
    },
}

ESCALATE = {
    "name": "nightshift_escalate",
    "description": (
        "Hand an incident to a human without proposing a fix: the alert looks false, the evidence is "
        "missing or contradictory, or no safe command would help. Say what you found and what a person "
        "should look at first. Never invent a fix to avoid calling this."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "reason": {"type": "string", "description": "What you found and why you are not proposing a fix."},
            "next_steps": {"type": "string", "description": "What a person should check first."},
            "false_alarm": {
                "type": "boolean",
                "description": "True only if the metrics show the service is healthy right now. Closes the incident, and the card still says why.",
            },
        },
        "required": ["incident_id", "reason"],
    },
}

RESOLVE = {
    "name": "nightshift_resolve",
    "description": (
        "Close an incident by hand, for example once a person has fixed it. Approved fixes close "
        "themselves and write their runbook, so you rarely need this. A runbook is only written if a "
        "fix was approved and verified."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "root_cause": {"type": "string"},
        },
        "required": ["incident_id", "root_cause"],
    },
}
