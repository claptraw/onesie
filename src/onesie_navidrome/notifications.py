from __future__ import annotations

import logging

from .config import NotificationConfig
from .errors import ConfigError


class Notifier:
    def __init__(self, config: NotificationConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self._apprise = None

    def _load(self):
        if not self.config.enabled:
            return None
        if self._apprise is not None:
            return self._apprise
        if self.config.apprise_config is None:
            raise ConfigError("notifications.enabled is true but notifications.apprise_config is not set")
        if not self.config.apprise_config.is_file():
            raise ConfigError(f"Apprise configuration file not found: {self.config.apprise_config}")
        try:
            import apprise
        except ImportError as exc:
            raise ConfigError(
                "Apprise notifications are enabled but Apprise is not installed. "
                "Install onesie with: pip install 'onesie-navidrome[notifications]'"
            ) from exc
        app = apprise.Apprise()
        cfg = apprise.AppriseConfig()
        if not cfg.add(str(self.config.apprise_config)):
            raise ConfigError(f"Apprise could not load configuration: {self.config.apprise_config}")
        app.add(cfg)
        if len(app) == 0:
            raise ConfigError(f"Apprise configuration contains no usable notification services: {self.config.apprise_config}")
        self._apprise = app
        return app

    def send(self, title: str, body: str, kind: str = "info") -> bool:
        app = self._load()
        if app is None:
            return False
        try:
            from apprise import NotifyType

            notify_type = {
                "info": NotifyType.INFO,
                "success": NotifyType.SUCCESS,
                "warning": NotifyType.WARNING,
                "failure": NotifyType.FAILURE,
            }.get(kind, NotifyType.INFO)
            result = app.notify(
                title=title,
                body=body,
                tag=self.config.tag or None,
                notify_type=notify_type,
            )
        except Exception as exc:
            self.logger.error("Apprise notification failed: %s", exc)
            return False
        return bool(result)
