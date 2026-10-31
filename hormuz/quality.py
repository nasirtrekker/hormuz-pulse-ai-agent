"""Self-evaluation of the guardrails against the bundled attack corpus."""
import json
from pathlib import Path

from . import guardrails, telemetry

CORPUS = Path(__file__).parent / "data" / "attack_corpus.json"


def guardrail_report() -> dict:
    corpus = json.loads(CORPUS.read_text())
    checks = {
        "Prompt-injection and empty inputs blocked": (
            corpus["blocked_inputs"], lambda t: guardrails.check_input(t) is not None),
        "Normal questions allowed": (
            corpus["allowed_inputs"], lambda t: guardrails.check_input(t) is None),
        "Injected instructions removed from research": (
            corpus["research_injections"], lambda t: "[removed]" in guardrails.sanitize_research("news", t)),
        "Secrets redacted from answers": (
            corpus["secrets"], lambda t: "[REDACTED]" in guardrails.redact_secrets(t)),
    }
    rows = []
    with telemetry.muted():
        for name, (cases, ok) in checks.items():
            passed = sum(1 for c in cases if ok(c))
            rows.append({"check": name, "passed": passed, "total": len(cases)})
    return {
        "checks": rows,
        "passed": sum(r["passed"] for r in rows),
        "total": sum(r["total"] for r in rows),
    }
