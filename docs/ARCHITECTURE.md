# Onesie architecture

Onesie separates **policy** from **deletion**.

```text
Navidrome Subsonic API
        |
        v
 rating / path discovery
        |
        v
  Onesie core policy
  - delete rating
  - grace period
  - persistent queue
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
          sidecar cleanup
                 |
                 v
          Navidrome scan
                 |
                 v
             Apprise
```

## Design boundaries

- Navidrome is the source of truth for user ratings.
- Onesie never edits audio tags, album metadata, or Navidrome's database.
- The filesystem backend is dependency-free apart from Onesie itself and supports one or more explicit server-to-local music-root mappings.
- The Beets backend is optional and invokes the user's own `beet` executable/config. Onesie does not open or migrate the Beets SQLite database.
- A future Navidrome WASM plugin can reuse the same policy model conceptually, but is not required by the CLI edition.
- SmartImport is not part of Onesie and no SmartImport hooks are required.
