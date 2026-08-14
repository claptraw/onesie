# Changelog

## v0.1alpha1 - 2026-08-14

First public alpha of the universal onesie architecture.

- Standalone run-once CLI; no daemon required.
- Navidrome-only filesystem deletion backend.
- Optional Beets CLI backend with exact path matching and no direct SQLite access.
- Configurable delete rating and grace period.
- Persistent path-based queue, final live rating/path re-check, symlink/traversal guards, extension allow-list, and batch guard.
- Optional exact-name sidecar cleanup and empty-directory pruning.
- Optional provider-neutral Apprise notifications.
- Multiple Navidrome music-folder path mappings.
- Docker example, GitHub Actions CI, architecture notes, and tests.

> GitHub release/tag: `v0.1alpha1`. Python package metadata uses the PEP 440 equivalent `0.1a1`.
