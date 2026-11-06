"""Tool schemas: what the model reads to decide when to call each tool."""

DIAGNOSE = {
    "name": "nightshift_diagnose",
    "description": (
        "Run Night Shift's read-only diagnostic checks for a service during an incident: container "
        "status, recent logs, resource usage, disk, database connections and recent deploys. Opens "
        "an incident record if none is given. Use this FIRST when an alert arrives, before "
        "forming any theory. It never changes anything."
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
        "'pg_stat_activity_count' or 'rate(http_requests_total{status=~\"5..\"}[5m])'."
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
        "Look up runbooks Night Shift learned from past incidents that match this alert. Call it "
        "right after the first diagnosis. If a match is returned, load that skill and follow it."
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
        "Propose a remediation for an open incident. It is NOT executed. The on-call engineer "
        "receives the diagnosis and commands and approves with '/nightshift approve <id>'. Use "
        "the smallest safe fix, and state the risk honestly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "diagnosis": {"type": "string", "description": "Root cause in plain English, with the evidence."},
            "commands": {"type": "array", "items": {"type": "string"}, "description": "Exact shell commands to run."},
            "risk": {"type": "string", "enum": ["low", "medium", "high"]},
            "verification": {"type": "string", "description": "How to confirm the fix worked."},
        },
        "required": ["incident_id", "diagnosis", "commands", "risk", "verification"],
    },
}

RESOLVE = {
    "name": "nightshift_resolve",
    "description": (
        "Close an incident once the fix is verified, and write what worked as a reusable runbook "
        "skill so the next occurrence is handled faster. Always call this at the end of an incident."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "incident_id": {"type": "string"},
            "root_cause": {"type": "string"},
            "symptoms": {"type": "string", "description": "Observable symptoms that identify this failure."},
            "fix_commands": {"type": "array", "items": {"type": "string"}},
            "verification": {"type": "string"},
        },
        "required": ["incident_id", "root_cause", "symptoms", "fix_commands", "verification"],
    },
}
