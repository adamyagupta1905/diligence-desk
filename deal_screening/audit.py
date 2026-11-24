"""Audit records and meaningful field comparisons, independent of the UI."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .models import NUMERIC


class AuditLog:
    @staticmethod
    def append(history, action, **details):
        return history + [{"time_utc": datetime.now(timezone.utc).isoformat(),
                           "action": action, **deepcopy(details)}]

    @staticmethod
    def canonical_fact(fact):
        """For comparison only: keep original records intact in the audit trail.

        Empty and null optional text are equivalent; numeric display formatting
        and insignificant whitespace are not substantive analyst corrections.
        Nonempty notes, citations, periods and changed values ARE corrections.
        """
        normalized = {}
        for key, value in fact.items():
            if isinstance(value, str):
                value = value.strip()
                if key == "quote":
                    value = " ".join(value.split())
                if not value:
                    value = None
            if key == "value" and fact.get("field") in NUMERIC and value is not None:
                try:
                    number = Decimal(str(value))
                    if number.is_finite():
                        value = number.normalize()
                except InvalidOperation:
                    pass
            normalized[key] = value
        # UI and model may omit empty optional metadata entirely.
        for key in ("value", "chunk_id", "quote", "currency", "period", "note"):
            normalized.setdefault(key, None)
        return normalized

    @classmethod
    def changes(cls, before, after):
        originals = {f["field"]: f for f in before}
        differences = []
        for fact in after:
            old = originals.get(fact["field"], {})
            old_normal = cls.canonical_fact(old)
            new_normal = cls.canonical_fact(fact)
            keys = sorted(k for k in old_normal.keys() | new_normal.keys()
                          if old_normal.get(k) != new_normal.get(k))
            if keys:
                differences.append({"field": fact["field"], "changed_properties": keys,
                                    "before": deepcopy(old), "after": deepcopy(fact)})
        return differences
