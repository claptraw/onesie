class OnesieError(RuntimeError):
    """Base operational error."""


class ConfigError(OnesieError):
    """Invalid configuration."""


class SafetyError(OnesieError):
    """A safety invariant failed; destructive work must stop."""


class BackendError(OnesieError):
    """Deletion backend failed."""
