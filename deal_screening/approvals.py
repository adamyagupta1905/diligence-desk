"""Content-bound approval checks; no authentication or digital-signature claim."""
import hashlib
import json


class ApprovalPolicy:
    VERSION = "screening-controls-1"

    @classmethod
    def fingerprint(cls, state):
        # Bind approval to exact reviewed evidence, assumptions and outputs.
        keys = ("case_id", "revision_id", "document_name", "document_hash", "pages",
                "facts", "company", "assumptions", "calculations", "flags", "memo", "model_name")
        payload = {k: state.get(k) for k in keys}
        if state.get("ledger_enabled"):
            # Optional payload preserves legacy fingerprints while binding every
            # authoritative historical record for new ledger-enabled cases.
            payload["historical_ledger"] = {key: state.get(key) for key in (
                "ledger_context", "ledger_context_confirmed", "ledger_context_draft", "ledger_raw", "ledger_rows",
                "ledger_confirmed", "ledger_selected_year", "ledger_reviewed",
                "ledger_diagnostics", "ledger_derived", "ledger_errors")}
        payload["policy_version"] = cls.VERSION
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                         allow_nan=False).encode()).hexdigest()

    @classmethod
    def status(cls, state, is_current=True):
        if not is_current:
            return "superseded"
        decision = state.get("decision", "pending")
        if decision != "approved":
            return decision
        if state.get("memo_error") or not state.get("memo"):
            return "stale_approval"
        if state.get("approval_policy_version") != cls.VERSION:
            return "stale_approval"
        if state.get("approved_fingerprint") != cls.fingerprint(state):
            return "stale_approval"
        return "approved"
