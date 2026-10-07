# ControlMap → Lifecycle Manager sync

Nightly job that turns ControlMap **Action Items** into Lifecycle Manager **Initiatives** on the matching client's roadmap, and keeps them updated.

## What it does

For each client pair in [`config.yaml`](config.yaml):

1. Reads all Action Items for the ControlMap client.
2. Lists the Lifecycle Manager client's Initiatives.
3. For each Action Item:
   - **New** → creates an Initiative named `AI-12 · <weakness name>` and sets its status, priority, quarter, hours and budget.
   - **Changed in ControlMap** → updates the Initiative.
   - **Unchanged** → leaves the Initiative alone, so edits made in Lifecycle Manager survive until the Action Item changes again.
   - **Not Applicable** → skipped. If it had already been synced, its Initiative is set to Declined.
4. Action Items deleted in ControlMap → their Initiative is set to **Declined** (never deleted).

If an Initiative is deleted by hand in Lifecycle Manager, the sync logs a warning and does **not** recreate it.

## Field mapping

| ControlMap Action Item | Lifecycle Manager Initiative |
|---|---|
| `code` + `weakness_name` | Name: `AI-12 · MFA not enforced` |
| `weakness_description`, `corrective_action`, `implementation_notes` | Executive summary (with a "Synced from ControlMap" footer) |
| Status: Not Started → Proposed, In Progress / Review → In Progress, Completed → Completed | Status |
| Priority: Critical / High → High, Medium → Medium, Low → Low, blank → None | Priority |
| `planned_end_date`, else `due_date`, else `planned_start_date`, else `roadmap` (3/6/12 months from creation) | Fiscal quarter (calendar quarters) |
| `effort_in_hours` | Estimated hours (minimum) |
| `cost` | One-time investment line `ControlMap AI-12 remediation`. Other budget lines are kept, and the cost is skipped if its currency doesn't match the Initiative's |

## How duplicates are prevented

[`state/state.json`](state/state.json) maps every Action Item to its Initiative ID plus a fingerprint of the synced fields. Live runs in GitHub Actions commit it back to the repo. If the state is ever lost, the sync re-adopts Initiatives by the `AI-12 ·` prefix in their name instead of creating new ones.

## Setup

### GitHub (nightly run)

1. Add the API key as a repo secret named `SCALEPAD_API_KEY` (**Settings → Secrets and variables → Actions → Secrets**). The key needs ControlMap and Lifecycle Manager access.
2. Run **Actions → Nightly sync → Run workflow** with mode `dry-run` and check the log and run summary.
3. When the dry run looks right, run it once with mode `live`.
4. To make the nightly schedule write for real, add a repo **variable** `SYNC_LIVE` = `true`. Until then, scheduled runs are dry runs.

The schedule is 07:00 UTC daily (in [`.github/workflows/sync.yml`](.github/workflows/sync.yml)).

### Local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
cp .env.example .env   # then paste the key into .env
.venv/bin/python -m cmlm                 # dry run, no writes
.venv/bin/python -m cmlm --live          # write to Lifecycle Manager
.venv/bin/python -m cmlm --client "Shamrock Woodworking"
.venv/bin/pytest
```

Local live runs update `state/state.json`. Commit it, or the next GitHub run will re-adopt those Initiatives by name, which works but is noisier.

## Adding a client

Add an entry to `config.yaml`. Names often differ between ControlMap and Lifecycle Manager, so the Lifecycle Manager side is pinned by client ID:

```yaml
  - controlmap: Shamrock Woodworking
    lifecycle_manager_id: e399db8f-634c-47be-8e8f-0f324f956a57
    lifecycle_manager_label: Simplewood
    enabled: true
```

## Layout

| Path | Purpose |
|---|---|
| `cmlm/api.py` | HTTP client: auth, pagination, retries (POSTs aren't retried on 5xx, to avoid duplicates) |
| `cmlm/platforms.py` | ControlMap and Lifecycle Manager endpoint calls; dry-run turns writes into log lines |
| `cmlm/mapping.py` | Action Item → Initiative field translation |
| `cmlm/engine.py` | Create / update / decline logic and state tracking |
| `cmlm/__main__.py` | CLI entry point and GitHub run summary |
