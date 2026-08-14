from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .engine import OnesieEngine
from .errors import OnesieError

EXAMPLE_CONFIG = """# Onesie configuration\nnavidrome:\n  url: http://localhost:4533\n  username: YOUR_USER\n  password_env: ONESIE_NAVIDROME_PASSWORD\n  client: Onesie\n  server_music_root: /music\n  verify_tls: true\n  trigger_scan: true\n\npolicy:\n  delete_rating: 1\n  grace_period: 7d\n  max_deletions_per_run: 20\n  dry_run: true\n  strict_validation: true\n\ndelete:\n  backend: filesystem  # filesystem | beets\n\nfilesystem:\n  music_root: /music\n  sidecars: [.lrc]\n  prune_empty_dirs: false\n\nbeets:\n  executable: beet\n  # config_file: /path/to/config.yaml\n\nnotifications:\n  enabled: false\n  # apprise_config: /path/to/apprise.yaml\n  tag: \"\"\n  notify_on_noop: false\n  notify_on_dry_run: false\n\nruntime:\n  state_file: ./state/onesie-state.json\n  audit_log: ./state/onesie-audit.jsonl\n"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onesie", description="Safely delete Navidrome tracks marked with a rating.")
    parser.add_argument("--version", action="version", version=f"Onesie {__version__}")
    parser.add_argument("-c", "--config", type=Path, default=Path("onesie.yaml"), help="Path to Onesie YAML config")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Process the delete queue once")
    run.add_argument("--dry-run", action="store_true", help="Force dry-run even if config enables deletion")
    sub.add_parser("status", help="Show persistent queue state")
    sub.add_parser("doctor", help="Validate Navidrome, paths, backend, and notifications")
    sub.add_parser("apprise-test", help="Send a test notification")
    init = sub.add_parser("init", help="Write an example configuration")
    init.add_argument("--output", type=Path, default=Path("onesie.yaml"))
    init.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.command == "init":
        if args.output.exists() and not args.force:
            print(f"Refusing to overwrite existing file: {args.output}", file=sys.stderr)
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        print(args.output)
        return 0
    engine = None
    try:
        config = Config.load(args.config)
        engine = OnesieEngine(config)
        if args.command == "run":
            return engine.run_once(force_dry_run=args.dry_run)
        if args.command == "status":
            print(json.dumps(engine.status(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "doctor":
            for finding in engine.doctor():
                print(finding)
            return 0
        if args.command == "apprise-test":
            if not config.notifications.enabled:
                print("Apprise notifications are disabled in the config", file=sys.stderr)
                return 2
            return 0 if engine.notifier.send("Onesie test", "Your Onesie notifications are working.", "success") else 2
    except OnesieError as exc:
        logging.getLogger("onesie").error("%s", exc)
        if engine is not None:
            try:
                engine.notifier.send("Onesie: run blocked", str(exc), "failure")
            except OnesieError:
                pass
        return 2
    return 0
