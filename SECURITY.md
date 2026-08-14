# Security and destructive-operation policy

Onesie deletes files by design. Treat configuration changes as privileged operations.

## Defaults

- Real deletion is disabled by default (`policy.dry_run: true`).
- Synthetic/relative Navidrome paths are rejected.
- Paths containing traversal or symlink components are rejected.
- Files outside configured local music roots are rejected.
- A final `getSong` call re-checks rating and path immediately before deletion.
- A per-run batch limit aborts the destructive phase before any file is removed.

## Credentials

Do not commit Navidrome passwords or Apprise URLs containing credentials. Prefer `ONESIE_NAVIDROME_PASSWORD` or a dedicated environment variable referenced by `navidrome.password_env`.

## Reporting security issues

For security-sensitive reports, use GitHub Private Vulnerability Reporting for `claptraw/onesie` if it is enabled. If it is not enabled, open a minimal issue asking for a private reporting channel without including exploit details.
