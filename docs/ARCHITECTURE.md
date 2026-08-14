# onesie architecture

onesie separates **policy** from **deletion**.

```text
Navidrome Subsonic API
        |
        v
 rating / path discovery
        |
        v
  onesie core policy
  - delete rating
  - grace period
  - persistent queue
  - pre-deletion warning state
  - live re-check
  - batch guard
  - audit log
        |
        +------------------+
        |                  |
        v                  v
 filesystem backend     Beets CLI backend
        |                  |
        +--------+---------+
                 v
          exact sidecars
                 |
                 v
   optional orphan cleanup
  (allowed covers + empty dirs)
                 |
                 v
          Navidrome scan
                 |
                 v
             Apprise
```

## Design boundaries

- Navidrome is the source of truth for user ratings.
- onesie never edits audio tags, album metadata, or Navidrome's database.
- The filesystem backend is dependency-free apart from onesie itself and supports one or more explicit server-to-local music-root mappings.
- The Beets backend is optional and invokes the user's own `beet` executable/config. onesie does not open or migrate the Beets SQLite database.
- Pre-deletion notification state is persistent per queued path. When enabled, deletion is gated on a successfully delivered warning plus the configured final warning window.
- Optional cover/directory cleanup only operates after onesie's own successful track deletions. An unrelated file, symlink, or remaining subdirectory blocks cleanup of that directory.
- A future Navidrome WASM plugin can reuse the same policy model conceptually, but is not required by the CLI edition.
- SmartImport is not part of onesie and no SmartImport hooks are required.
