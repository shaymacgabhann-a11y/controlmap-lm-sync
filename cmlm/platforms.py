"""ControlMap reads and Lifecycle Manager reads/writes."""

from __future__ import annotations

import itertools
import logging

from .api import ScalePadClient

log = logging.getLogger(__name__)


class ControlMap:
    def __init__(self, client: ScalePadClient):
        self.client = client

    def find_client(self, name: str) -> dict | None:
        """Look up a ControlMap client by (case-insensitive) name."""
        rows = list(
            self.client.paginate_get(
                "/controlmap/v1/clients/action-items-summary",
                {"filter[client.name]": f"eq:{name}"},
            )
        )
        return rows[0]["client"] if rows else None

    def action_items(self, client_id: str) -> list[dict]:
        return list(self.client.paginate_post(f"/controlmap/v1/clients/{client_id}/action-items/search"))


class LifecycleManager:
    """Initiative operations. With dry_run=True, writes are logged and skipped."""

    def __init__(self, client: ScalePadClient, dry_run: bool):
        self.client = client
        self.dry_run = dry_run
        self._fake_ids = (f"dry-run-{n}" for n in itertools.count(1))

    def initiatives(self, client_id: str) -> list[dict]:
        return list(
            self.client.paginate_get(
                "/lifecycle-manager/v2/initiatives",
                {"filter[client.id]": f"eq:{client_id}", "include_unscheduled": "true"},
            )
        )

    def create(self, client_id: str, name: str, summary_json: str) -> str:
        body = {"client_key": {"id": client_id}, "name": name, "executive_summary_json": summary_json}
        if self._skip("create", name):
            return next(self._fake_ids)
        return self.client.post("/lifecycle-manager/v1/initiatives", body)["id"]

    def patch(self, initiative_id: str, **fields) -> None:
        if not self._skip("patch", initiative_id, sorted(fields)):
            self.client.patch(f"/lifecycle-manager/v1/initiatives/{initiative_id}", fields)

    def set_status(self, initiative_id: str, status: str) -> None:
        if not self._skip("status", initiative_id, status):
            self.client.put(f"/lifecycle-manager/v1/initiatives/{initiative_id}/status", {"status": status})

    def set_priority(self, initiative_id: str, priority: str) -> None:
        if not self._skip("priority", initiative_id, priority):
            self.client.put(f"/lifecycle-manager/v1/initiatives/{initiative_id}/priority", {"priority": priority})

    def set_quarter(self, initiative_id: str, fiscal_quarter: dict | None) -> None:
        body = {"fiscal_quarter": fiscal_quarter, "target_precision": "Quarter" if fiscal_quarter else None}
        if not self._skip("schedule", initiative_id, fiscal_quarter):
            self.client.put(f"/lifecycle-manager/v1/initiatives/{initiative_id}/schedule", body)

    def set_budget(self, initiative_id: str, line_items: list[dict]) -> None:
        if not self._skip("budget", initiative_id, [li["label"] for li in line_items]):
            self.client.put(
                f"/lifecycle-manager/v1/initiatives/{initiative_id}/budget", {"budget_line_items": line_items}
            )

    def _skip(self, action: str, *detail) -> bool:
        if self.dry_run:
            log.info("[dry-run] would %s %s", action, " ".join(map(str, detail)))
        return self.dry_run
