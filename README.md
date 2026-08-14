# Onesie

[GitHub: claptraw/onesie](https://github.com/claptraw/onesie)

**Onesie** safely turns a Navidrome rating into a delayed deletion request.

The default policy is intentionally simple: rate a track **1 star**, keep it at 1 star for a configurable grace period, and Onesie can remove it after a final live re-check.

Onesie is designed for Navidrome libraries where low ratings can have operational meaning. It does **not** require Beets. Beets support is an optional deletion backend.

> **Alpha / destructive software:** `dry_run: true` is the default. Keep backups and test your configuration before enabling real deletion.

## Why Onesie is standalone

Onesie Core is independent from Beets, Docker, SmartImport, and any particular Navidrome client.

- **Navidrome-only:** use the `filesystem` backend.
- **Navidrome + Beets:** use the optional `beets` backend so Beets removes both its library entry and the audio file.
- **Docker:** optional packaging, not a requirement.
- **Apprise:** optional notifications with the provider(s) chosen by the user.

## Safety model

Before real deletion Onesie requires all of the following:

1. The configured Navidrome user currently reports the configured delete rating.
2. The rating has remained in Onesie's persistent queue for the whole grace period.
3. Navidrome supplies an **absolute real path**, not its synthetic Subsonic path.
4. The path maps under the configured music root and contains no traversal or symlink component.
5. The extension is explicitly allowed.
6. The selected deletion backend validates the track.
7. Immediately before deletion, Onesie calls `getSong` and re-checks both rating and path.
8. A per-run batch limit aborts the entire destructive phase if too many tracks become eligible at once.

Queue identity uses Navidrome's real server-side path, not a Navidrome song ID. This remains path-based even when song IDs change.

## Navidrome requirement: Report Real Path

Onesie deliberately refuses Navidrome's synthetic relative Subsonic paths. Enable **Report Real Path** for the player named `Onesie`, or use Navidrome's `Subsonic.DefaultReportRealPath` setting.

The server path and the path visible to Onesie can differ. Map them with:

```yaml
navidrome:
  server_music_root: /music
filesystem:
  music_root: /mnt/music
```

A Navidrome path `/music/Artist/Album/Song.flac` then maps to `/mnt/music/Artist/Album/Song.flac`.

### Multiple Navidrome music folders

For servers with more than one music folder, replace the single `music_root` mapping with explicit mappings:

```yaml
filesystem:
  path_mappings:
    - server_root: /music-main
      local_root: /mnt/music-main
    - server_root: /music-archive
      local_root: /mnt/music-archive
  sidecars: [.lrc]
```

Onesie chooses the longest matching server root and still enforces that the resolved file remains inside the corresponding local root.

## Install

The PyPI distribution name is `onesie-navidrome` (the name `onesie` is already occupied on PyPI), while the command remains `onesie`.

```bash
pip install onesie-navidrome
```

With Apprise notifications:

```bash
pip install "onesie-navidrome[notifications]"
```

From a Git checkout:

```bash
pip install -e ".[notifications]"
```

## Configure

Generate a starting config:

```bash
onesie init
```

Prefer an environment variable for the Navidrome password:

```bash
export ONESIE_NAVIDROME_PASSWORD='...'
```

Minimal Navidrome-only configuration:

```yaml
navidrome:
  url: http://localhost:4533
  username: myuser
  password_env: ONESIE_NAVIDROME_PASSWORD
  server_music_root: /music

policy:
  delete_rating: 1
  grace_period: 7d
  max_deletions_per_run: 20
  dry_run: true

delete:
  backend: filesystem

filesystem:
  music_root: /music
  sidecars: [.lrc]

notifications:
  enabled: false

runtime:
  state_file: ./state/onesie-state.json
  audit_log: ./state/onesie-audit.jsonl
```

Run the safety checks first:

```bash
onesie -c onesie.yaml doctor
```

On a fresh Navidrome setup, the first request also makes the `Onesie` client/player visible. If `doctor` reports a synthetic path, enable **Report Real Path** for that player (or the server default) and run `doctor` again.

Then process the queue:

```bash
onesie -c onesie.yaml run
```

Force a dry run regardless of config:

```bash
onesie -c onesie.yaml run --dry-run
```

Inspect the persistent queue:

```bash
onesie -c onesie.yaml status
```

## Filesystem backend

```yaml
delete:
  backend: filesystem
```

This is the universal Navidrome-only mode. Onesie removes the validated audio file itself. Exact-name sidecars such as `.lrc` can be removed after the audio deletion.

Onesie never deletes cover art or arbitrary files from the album directory.

## Beets backend

```yaml
delete:
  backend: beets

beets:
  executable: beet
  # config_file: /config/config.yaml
```

This backend intentionally uses **your own Beets CLI** rather than opening the SQLite library directly. Onesie enumerates Beets paths, requires an exact path match, and calls:

```text
beet remove -d -f id:<exact-id>
```

This avoids coupling Onesie to a particular Beets database schema/version and prevents a separately installed Beets library from migrating a user's database.

The `beet` executable must therefore be available in the environment where Onesie runs when this backend is selected. No Beets dependency exists in filesystem mode.

## Apprise

Notifications are opt-in:

```yaml
notifications:
  enabled: true
  apprise_config: /etc/onesie/apprise.conf
  tag: ""
```

Install the notifications extra and configure any services supported by Apprise. Onesie does not hard-code Pushover, ntfy, Discord, email, or another provider.

Test it with:

```bash
onesie -c onesie.yaml apprise-test
```

## Scheduling

Onesie is a run-once CLI, not a required daemon. Schedule it however you prefer:

- cron / systemd timer
- TrueNAS Cron Job
- Windows Task Scheduler
- launchd
- a one-shot Docker container

Running it daily with a 7-day grace period gives every track its own full grace period.

For Docker, run the container with a non-root UID/GID that has exactly the required write permission on the music files and state directory; the Compose example exposes `PUID`/`PGID` for this.

## Exit codes

- `0`: successful run
- `2`: configuration, safety, backend, or partial-deletion error

## Project status

GitHub release/tag: `v0.1alpha1`. Python packaging uses the PEP 440 equivalent `0.1a1`.

v0.1alpha1 is the first public alpha of the universal foundation. Planned follow-ups include a thin Beets plugin wrapper (`beet onesie`), improved packaging/release automation, and evaluation of a native Navidrome WASM edition for Navidrome-only users.
