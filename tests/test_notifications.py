import logging
from pathlib import Path

import pytest

from onesie_navidrome.config import NotificationConfig
from onesie_navidrome.errors import ConfigError
from onesie_navidrome.notifications import Notifier


def test_notifications_disabled_need_no_apprise(tmp_path: Path):
    notifier = Notifier(
        NotificationConfig(False, None, "", False, False),
        logging.getLogger("test"),
    )
    assert notifier.send("title", "body") is False


def test_enabled_notifications_require_existing_config(tmp_path: Path):
    notifier = Notifier(
        NotificationConfig(True, tmp_path / "missing.conf", "", False, False),
        logging.getLogger("test"),
    )
    with pytest.raises(ConfigError, match="not found"):
        notifier.send("title", "body")
