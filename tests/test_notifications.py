import logging
from pathlib import Path

import pytest

from onesie_navidrome.config import NotificationConfig
from onesie_navidrome.errors import ConfigError
from onesie_navidrome.notifications import Notifier


def test_notifications_disabled_need_no_apprise(tmp_path: Path):
    notifier = Notifier(
        NotificationConfig(
            enabled=False,
            apprise_config=None,
            tag="",
            notify_on_noop=False,
            notify_on_dry_run=False,
            notify_before_deletion=True,
            warning_before_deletion_seconds=2 * 86400,
            warning_retry_interval_seconds=12 * 3600,
            final_warning_window_seconds=12 * 3600,
            warning_failure_postpone_seconds=86400,
            notify_after_deletion=True,
        ),
        logging.getLogger("test"),
    )
    assert notifier.send("title", "body") is False


def test_enabled_notifications_require_existing_config(tmp_path: Path):
    notifier = Notifier(
        NotificationConfig(
            enabled=True,
            apprise_config=tmp_path / "missing.conf",
            tag="",
            notify_on_noop=False,
            notify_on_dry_run=False,
            notify_before_deletion=True,
            warning_before_deletion_seconds=2 * 86400,
            warning_retry_interval_seconds=12 * 3600,
            final_warning_window_seconds=12 * 3600,
            warning_failure_postpone_seconds=86400,
            notify_after_deletion=True,
        ),
        logging.getLogger("test"),
    )
    with pytest.raises(ConfigError, match="not found"):
        notifier.send("title", "body")
