"""CLI: python -m cmlm [--live] [--client NAME]"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import yaml

from .api import ScalePadClient
from .engine import ClientPair, Syncer
from .platforms import ControlMap, LifecycleManager

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("cmlm")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync ControlMap Action Items into Lifecycle Manager Initiatives.")
    parser.add_argument("--live", action="store_true", help="actually write to Lifecycle Manager (default: dry run)")
    parser.add_argument("--client", help="only sync this ControlMap client name")
    parser.add_argument("--config", default=ROOT / "config.yaml", type=Path)
    parser.add_argument("--state", default=ROOT / "state" / "state.json", type=Path)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    _load_dotenv(ROOT / ".env")

    api_key = os.environ.get("SCALEPAD_API_KEY")
    if not api_key:
        log.error("SCALEPAD_API_KEY is not set (put it in .env locally or a GitHub secret)")
        return 2

    config = yaml.safe_load(args.config.read_text())
    pairs = [
        ClientPair(c["controlmap"], c["lifecycle_manager_id"], c.get("lifecycle_manager_label", ""))
        for c in config.get("clients", [])
        if c.get("enabled", True) and (not args.client or c["controlmap"].lower() == args.client.lower())
    ]
    if not pairs:
        log.error("No enabled clients matched in %s", args.config)
        return 2

    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    client = ScalePadClient(api_key, region=config.get("region", "us"))
    syncer = Syncer(ControlMap(client), LifecycleManager(client, dry_run=not args.live), state)

    log.info("Mode: %s", "LIVE" if args.live else "DRY RUN (no writes)")
    try:
        for pair in pairs:
            syncer.sync_client(pair)
    finally:
        # Save even after a crash so Initiatives already created aren't created again.
        if args.live:
            args.state.parent.mkdir(parents=True, exist_ok=True)
            args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    _report(syncer, live=args.live)
    return 1 if syncer.errors else 0


def _report(syncer: Syncer, live: bool) -> None:
    order = ["created", "updated", "declined", "unchanged", "skipped", "missing_in_lm", "errors"]
    rows = [(k, syncer.stats.get(k, 0)) for k in order]
    log.info("Summary: %s", ", ".join(f"{k}={v}" for k, v in rows))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = [f"## ControlMap → Lifecycle Manager ({'live' if live else 'dry run'})", "", "| Result | Count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in rows]
        if syncer.errors:
            lines += ["", "### Errors", *[f"- {e}" for e in syncer.errors]]
        with open(summary_path, "a") as fh:
            fh.write("\n".join(lines) + "\n")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


if __name__ == "__main__":
    sys.exit(main())
