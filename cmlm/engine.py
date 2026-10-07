"""Reconcile one client's ControlMap Action Items with its Lifecycle Manager Initiatives."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from . import mapping
from .platforms import ControlMap, LifecycleManager

log = logging.getLogger(__name__)

DECLINED = "Declined"


@dataclass
class ClientPair:
    controlmap_name: str
    lm_client_id: str
    lm_client_label: str = ""


class Syncer:
    def __init__(self, cm: ControlMap, lm: LifecycleManager, state: dict):
        self.cm = cm
        self.lm = lm
        # state["items"]: "<cm client id>:<action item id>" -> {initiative_id, fingerprint, code, ...}
        self.items: dict = state.setdefault("items", {})
        self.stats: Counter = Counter()
        self.errors: list[str] = []

    def sync_client(self, pair: ClientPair) -> None:
        cm_client = self.cm.find_client(pair.controlmap_name)
        if not cm_client:
            self._error(f"ControlMap client {pair.controlmap_name!r} not found")
            return
        cm_id = cm_client["id"]
        log.info("Syncing ControlMap %r (%s) -> LM %s", cm_client["name"], cm_id, pair.lm_client_label or pair.lm_client_id)

        action_items = self.cm.action_items(cm_id)
        initiatives = {i["id"]: i for i in self.lm.initiatives(pair.lm_client_id)}
        by_code = {}
        for init in initiatives.values():
            code = mapping.code_from_initiative_name(init.get("name", ""))
            if code:
                by_code.setdefault(code, init)

        seen_keys = set()
        for item in action_items:
            key = f"{cm_id}:{item['id']}"
            seen_keys.add(key)
            try:
                self._sync_item(key, item, pair, initiatives, by_code)
            except Exception as exc:  # keep going; one bad item shouldn't stop the run
                self._error(f"{item.get('code', item['id'])}: {exc}")

        # Action Items that disappeared from ControlMap -> Declined.
        for key, entry in list(self.items.items()):
            if not key.startswith(f"{cm_id}:") or key in seen_keys or entry.get("removed"):
                continue
            try:
                self._decline(entry, initiatives, reason="deleted in ControlMap")
                entry["removed"] = True
            except Exception as exc:
                self._error(f"{entry.get('code')}: {exc}")

    def _sync_item(self, key, item, pair, initiatives, by_code) -> None:
        entry = self.items.get(key)

        if mapping.is_skipped(item):
            if entry and initiatives.get(entry["initiative_id"]) and entry.get("status") != DECLINED:
                self._decline(entry, initiatives, reason="marked Not Applicable")
            else:
                self.stats["skipped"] += 1
            return

        spec = mapping.build_spec(item)

        if entry is None:
            existing = by_code.get(item["code"])
            if existing:  # state was lost but the Initiative exists: adopt it rather than duplicate
                log.info("%s: adopting existing Initiative %s", item["code"], existing["id"])
                entry = self.items[key] = {"initiative_id": existing["id"], "code": item["code"], "fingerprint": None}
            else:
                initiative_id = self.lm.create(pair.lm_client_id, spec.name, spec.summary_json)
                log.info("%s: created Initiative %s", item["code"], initiative_id)
                self._apply(initiative_id, spec, existing_budget=[], created=True)
                self._record(key, initiative_id, spec, item)
                self.stats["created"] += 1
                return

        initiative = initiatives.get(entry["initiative_id"])
        if initiative is None:
            # Someone deleted it in LM on purpose; don't fight them by recreating it.
            log.warning("%s: Initiative %s no longer exists in LM, not recreating", item["code"], entry["initiative_id"])
            self.stats["missing_in_lm"] += 1
            return

        if entry.get("fingerprint") == spec.fingerprint():
            self.stats["unchanged"] += 1
            return

        currency = ((initiative.get("budget") or {}).get("currency") or {}).get("code_alpha")
        self._apply(entry["initiative_id"], spec, existing_budget=_budget_lines(initiative), currency=currency)
        self._record(key, entry["initiative_id"], spec, item)
        log.info("%s: updated Initiative %s", item["code"], entry["initiative_id"])
        self.stats["updated"] += 1

    def _apply(self, initiative_id: str, spec: mapping.InitiativeSpec, existing_budget: list[dict], created=False, currency: str | None = None) -> None:
        fields = {} if created else {"name": spec.name, "executive_summary_json": spec.summary_json}
        if spec.estimated_hours:
            fields["estimated_hours"] = {"minimum": spec.estimated_hours}
        if fields:
            self.lm.patch(initiative_id, **fields)
        self.lm.set_status(initiative_id, spec.status)
        self.lm.set_priority(initiative_id, spec.priority)
        if spec.fiscal_quarter:
            self.lm.set_quarter(initiative_id, spec.fiscal_quarter)

        # Budget PUT replaces every one-time line, so keep lines people added by hand
        # and swap only the one this sync owns.
        ours = mapping.budget_label(spec.code)
        lines = [li for li in existing_budget if li.get("label") != ours]
        if spec.budget_line:
            if currency and spec.budget_currency and currency.upper() != spec.budget_currency.upper():
                log.warning("%s: cost is in %s but the Initiative budget is in %s, skipping budget line",
                            spec.code, spec.budget_currency, currency)
            else:
                lines.append(spec.budget_line)
        if lines != existing_budget:
            self.lm.set_budget(initiative_id, lines)

    def _decline(self, entry: dict, initiatives: dict, reason: str) -> None:
        if entry["initiative_id"] not in initiatives or entry.get("status") == DECLINED:
            return
        log.info("%s: %s, setting Initiative %s to Declined", entry.get("code"), reason, entry["initiative_id"])
        self.lm.set_status(entry["initiative_id"], DECLINED)
        entry["status"] = DECLINED
        entry["fingerprint"] = None  # if the item comes back, re-push everything
        self.stats["declined"] += 1

    def _record(self, key: str, initiative_id: str, spec: mapping.InitiativeSpec, item: dict) -> None:
        self.items[key] = {
            "initiative_id": initiative_id,
            "code": spec.code,
            "status": spec.status,
            "fingerprint": spec.fingerprint(),
            "cm_updated_at": item.get("updated_at"),
        }

    def _error(self, message: str) -> None:
        log.error(message)
        self.errors.append(message)
        self.stats["errors"] += 1


def _budget_lines(initiative: dict) -> list[dict]:
    """Existing one-time lines, trimmed to the fields the budget PUT accepts."""
    keep = ("label", "cost_subunits", "unit_count", "display_order", "cost_type")
    lines = (initiative.get("budget") or {}).get("line_items") or []
    return [{k: li[k] for k in keep if li.get(k) is not None} for li in lines]
