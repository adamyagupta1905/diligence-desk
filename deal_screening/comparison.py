"""Deterministic document/fact change review; no legal drafting or model calls."""
from difflib import SequenceMatcher, unified_diff

from .audit import AuditLog


class DocumentComparator:
    @staticmethod
    def lines(pages):
        return [{"page": p["page"], "text": line.strip()}
                for p in pages for line in p["text"].splitlines() if line.strip()]

    @classmethod
    def text_changes(cls, before_pages, after_pages):
        before, after = cls.lines(before_pages), cls.lines(after_pages)
        old = [line["text"] for line in before]
        new = [line["text"] for line in after]
        changes = []
        for operation, i, j, x, y in SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
            if operation != "equal":
                changes.append({"operation": operation, "before": before[i:j], "after": after[x:y]})
        return {"changes": changes,
                "diff": "\n".join(unified_diff(old, new, fromfile="Previous document",
                                                tofile="Current document", lineterm=""))}

    @staticmethod
    def fact_changes(before, after):
        changes = AuditLog.changes(before.get("facts", []), after.get("facts", []))
        for change in changes:
            change["category"] = ("value_or_status" if
                                  set(change["changed_properties"]) & {"value", "status", "currency", "period"}
                                  else "evidence_or_note")
        return changes

    @staticmethod
    def calculation_changes(before, after):
        if not before.get("calculations") or not after.get("calculations"):
            return {"available": False, "reason": "Both revisions need confirmed inputs and calculations."}
        old_method = before.get("assumptions", {}).get("valuation_method", "ev_ebitda")
        new_method = after.get("assumptions", {}).get("valuation_method", "ev_ebitda")
        if old_method != new_method:
            return {"available": False, "reason": "Valuation method changed; direct like-for-like deltas withheld."}
        for key in ("company_name", "currency", "period"):
            if before["company"].get(key) != after["company"].get(key):
                return {"available": False, "reason": f"Different {key}: direct valuation deltas withheld."}
        keys = ("enterprise_value", "equity_value", "margin", "stress_enterprise_value", "stress_equity_value")
        deltas = []
        for key in keys:
            old, new = before["calculations"].get(key), after["calculations"].get(key)
            deltas.append({"metric": key, "before": old, "after": new,
                           "change": new - old if old is not None and new is not None else None})
        return {"available": True, "currency": after["company"]["currency"], "deltas": deltas,
                "assumptions_before": before.get("assumptions"),
                "assumptions_after": after.get("assumptions"),
                "note": "Deltas reflect all confirmed input and assumption changes; they do not isolate causality."}
