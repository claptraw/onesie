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

EXAMPLE_CONFIG = """# onesie configuration
navidrome:
  url: http://localhost:4533
  username: YOUR_USER
  password_env: ONESIE_NAVIDROME_PASSWORD
  client: onesie
  server_music_root: /music
  verify_tls: true
  trigger_scan: true

policy:
  delete_rating: 1
  grace_period: 7d
  max_deletions_per_run: 20
  dry_run: true
  strict_validation: true

delete:
  backend: filesystem  # filesystem | beets

filesystem:
  music_root: /music
  sidecars: [.lrc]
  prune_empty_dirs: false
  cleanup_files:
    - cover.jpg
    - cover.webp
    - cover.mp4

beets:
  executable: beet
  # config_file: /path/to/config.yaml

notifications:
  enabled: false
  # apprise_config: /path/to/apprise.conf
  tag: ""
  notify_before_deletion: true
  warning_before_deletion: 2d
  warning_retry_interval: 12h
  final_warning_window: 12h
  warning_failure_postpone: 1d
  notify_after_deletion: true
  notify_on_noop: false
  notify_on_dry_run: false

runtime:
  state_file: ./state/onesie-state.json
  audit_log: ./state/onesie-audit.jsonl
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onesie", description="Safely delete Navidrome tracks marked with a rating.")
    parser.add_argument("--version", action="version", version=f"onesie {__version__}")
    parser.add_argument("-c", "--config", type=Path, default=Path("onesie.yaml"), help="Path to onesie YAML config")
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
            return 0 if engine.notifier.send("onesie test", "Your onesie notifications are working.", "success") else 2
    except OnesieError as exc:
        logging.getLogger("onesie").error("%s", exc)
        if engine is not None:
            try:
                engine.notifier.send("onesie: run blocked", str(exc), "failure")
            except OnesieError:
                pass
        return 2
    return 0
