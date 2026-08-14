# ⭐ onesie

> [!CAUTION]
> **Alpha software:** onesie is currently in an early alpha stage. **Do not point it at a production music library yet.** Use a test library or a disposable copy of your music, keep `dry_run: true`, and verify that onesie sees the correct tracks and file paths before enabling real deletion.

onesie turns the Navidrome one-star-rating into a simple **"remove this song from my server"** action.

Don't like a song and want to quickly delete it from your server?

Just rate it with one star in your music player and onesie deletes it cleanly - works with any music client (as long as it's accessing your Navidrome instance and has access to its rating feature).

```text
Navidrome client
      ↓
Rate a track 1 star
      ↓
onesie marks it for deletion
      ↓
Wait for the grace period
      ↓
Still 1 star? → remove it
Rating changed/removed? → keep it
```

This lets you clean up a curated music library without SSHing into your server, browsing through folders, looking up filenames, or maintaining a separate "to delete" playlist.

By default, onesie uses a **7-day grace period**. If you change your mind and want to keep the song, just remove the one-star-rating and onesie won't delete it.

## Features

- Use the native Navidrome rating system for a (delayed) deletion request.
- Default workflow: **1 star = remove this track from my library**.
- Configurable grace period before anything is deleted.
- Change the rating during the grace period to automatically cancel the deletion.
- Final rating check immediately before a real deletion.
- Works with **Navidrome only**.
- Optional **beets integration** for libraries already managed by [beets](https://beets.io/).
- Optional cleanup of matching sidecar files such as `.lrc` lyrics and any leftover, now empty folders.
- Supports one or multiple Navidrome music folders.
- Dry-run mode for safely testing the complete workflow.
- Batch limit to stop unexpected mass-deletion runs.
- Persistent queue and audit log.
- Optional notifications through [Apprise](https://github.com/caronc/apprise), so you can get a notification, which songs will be/have been deleted.
- Normal command-line application; optional Docker version.
- Can be scheduled via cron jobs, systemd, Windows Task Scheduler, launchd, or another scheduler.

## Why onesie exists

Navidrome and its clients are great places to listen to, rate, and enjoy your music. Organizing your actual music files and quickly removing them from your server is a different matter.

If you listen to a song while on the run and decide "Nah, I don't like that one, I want to delete it from my server", things are quickly getting inconvenient. Your music player client doesn't have a delete button, so you have to remember which song it was you didn't like, later manually open your server, find the correct file and delete it.

onesie makes this process much more convenient.

Just rate a song one star in the Navidrome-connected player of your choice. onesie handles the server-side removal.


```text
☆☆☆☆☆  not rated
★☆☆☆☆  remove this track (onesie)
★★★☆☆  good
★★★★☆  very good
★★★★★  favourite
```

The delete rating is configurable if you prefer a different scheme.

## How the grace period works

The grace period prevents an accidental rating from immediately deleting a file.

The default are 7 days before a song is actually deleted:

1. You rate a track 1 star.
2. onesie sees it during the next run and adds it to its deletion queue.
3. The file stays untouched for seven days.
4. If you change the rating before the grace period ends, the deletion is cancelled.
5. If the track is still rated 1 star after seven days, onesie checks it again and removes it.

Running onesie once per day is a practical default for most setups.

## Prerequisites

You need:

- a working [Navidrome](https://www.navidrome.org/) server;
- a Navidrome user whose ratings onesie should follow;
- any Navidrome/Subsonic client that can set star ratings;
- Python 3.10 or newer for the normal CLI installation;
- read/write access to the music files when using the filesystem backend.

**Optional: beets integration**

Many of us use beets as their preferred way of tagging, organizing and importing new music in their existing library. 

If onesie deletes music files, the file paths still exist in beets' library database, which is not ideal.

To keep your actually existing music files and beets synchronous, onesie can use beets as a backend.

Whenever a music file is deleted from your server, it's also deleted from the beets database.

If you choose the beets backend in the config, the `beet` command must be available in the same environment where onesie runs.

### Navidrome: enable Report Real Path

onesie needs Navidrome to report the real path of each track so that the rating can be matched to the correct file on disk.

Use the client name `onesie` and enable **Report Real Path** for that player in Navidrome, or use Navidrome's server-wide equivalent setting.

If Navidrome returns a synthetic library path instead of a real file path, onesie refuses to perform real deletions.

## Installation

### 1. Install the alpha release

Download the wheel from the GitHub Release and install it with pip:

```bash
python -m pip install ./onesie_navidrome-0.1a1-py3-none-any.whl
```

The Python distribution is named `onesie-navidrome`, while the command you use is simply:

```bash
onesie
```

### 2. Optional: install Apprise notifications

Notifications are completely optional.

If you want them, install Apprise alongside onesie:

```bash
python -m pip install apprise
```

### 3. Create a starter configuration

```bash
onesie init
```

Adjust the generated configuration for your Navidrome server and music paths before running anything against real files.

## Configuration

A minimal Navidrome-only setup looks like this:

```yaml
navidrome:
  url: http://localhost:4533
  username: myuser
  password_env: ONESIE_NAVIDROME_PASSWORD
  client: onesie
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
  sidecars:
    - .lrc

notifications:
  enabled: false

runtime:
  state_file: ./state/onesie-state.json
  audit_log: ./state/onesie-audit.jsonl
```

Prefer an environment variable instead of storing your Navidrome password directly in the configuration:

```bash
export ONESIE_NAVIDROME_PASSWORD='your-password'
```

A more complete example is available in [`examples/onesie.yaml`](examples/onesie.yaml).

## First setup: recommended safe test

Because onesie is designed to delete files, the first setup should always use a test library or disposable copy of your music.

### 1. Keep dry-run enabled

```yaml
policy:
  dry_run: true
```

### 2. Check the setup

```bash
onesie -c onesie.yaml doctor
```

This is the first command to run after configuring onesie.

If it reports that Navidrome is returning a synthetic path, enable **Report Real Path** for the `onesie` player and run the check again.

### 3. Rate one test track 1 star

In your music player client, give a disposable test track a 1-star rating.

Then run:

```bash
onesie -c onesie.yaml run
```

The track should enter the queue, but no file is removed while dry-run mode is enabled.

### 4. Check the queue

```bash
onesie -c onesie.yaml status
```

Confirm that the correct song and correct real file path are shown.

Keep using dry-run mode until you have verified the complete workflow with non-production data.

## Navidrome-only mode

The normal standalone setup uses the filesystem backend:

```yaml
delete:
  backend: filesystem
```

This mode is intended for users who run Navidrome without a separate music-library manager like beets.

When a track has remained at the delete rating for the complete grace period, onesie removes the validated audio file itself.

Matching sidecars can also be removed. For example:

```text
01 - Song.flac
01 - Song.lrc
```

With `.lrc` configured as a sidecar, when the audio file is deleted by onesie, the lyric file is also removed.

onesie does not sweep the rest of the album folder and does not remove covers or unrelated files.

## Optional beets integration

If your music library is already managed by [beets](https://beets.io/), you can let beets handle the actual removal:

```yaml
delete:
  backend: beets

beets:
  executable: beet
  # config_file: /path/to/config.yaml
```

The practical difference is that the track is removed through your existing beets library instead of only deleting the audio file from disk.

This keeps the beets library in sync with the file removal.

The beets backend is completely optional. Users with only Navidrome do not need beets installed or configured.

## Multiple music folders

If your Navidrome setup uses more than one music folder, configure explicit mappings:

```yaml
filesystem:
  path_mappings:
    - server_root: /music-main
      local_root: /mnt/music-main
    - server_root: /music-archive
      local_root: /mnt/music-archive
  sidecars:
    - .lrc
```

This lets one onesie installation work across several explicitly configured libraries.

## Apprise notifications

onesie uses [Apprise](https://github.com/caronc/apprise) for optional notifications.

Enable it in the config:

```yaml
notifications:
  enabled: true
  apprise_config: /etc/onesie/apprise.conf
  tag: ""
```

Your Apprise configuration can then contain whichever supported notification service you prefer (e.g. Pushover, Pushbullet, Discord, Telegram, Slack etc.).

Test it with:

```bash
onesie -c onesie.yaml apprise-test
```

If notifications are disabled, onesie simply continues without Apprise.

## Scheduling

onesie is a run-once command. It does not need to stay running as a permanent service.

A typical setup simply runs:

```bash
onesie -c /path/to/onesie.yaml run
```

once per day.

You can use whatever scheduler already fits your setup, for example:

- cron;
- systemd timer;
- Cron Jobs;
- Windows Task Scheduler;
- launchd;
- a scheduled one-shot Docker container.

With a 7-day grace period, a daily run is usually enough.

## Docker

Docker is optional.

The repository includes example Docker files under [`docker/`](docker/) for users who prefer containers, but onesie does not require a permanently running container.

## Useful commands

### Create a starter config

```bash
onesie init
```

### Check the setup

```bash
onesie -c onesie.yaml doctor
```

### Process ratings and delete songs in the deletion queue

```bash
onesie -c onesie.yaml run
```

### Force a dry run

```bash
onesie -c onesie.yaml run --dry-run
```

### Show the current queue

```bash
onesie -c onesie.yaml status
```

### Test Apprise notifications

```bash
onesie -c onesie.yaml apprise-test
```

## Safety behaviour

onesie is intentionally conservative because its job can become destructive once real deletion is enabled.

Before a real deletion, it checks that:

- the track is still using the configured delete rating;
- the complete grace period has passed;
- Navidrome reports a real absolute path;
- the file is inside one of the configured music roots;
- the path does not escape through traversal or symlinks;
- the file type is allowed;
- the selected deletion backend accepts the track;
- the rating and path still match immediately before deletion;
- the number of eligible deletions stays below the configured batch limit.

If the batch limit is exceeded, the destructive part of the run is stopped instead of deleting only part of an unexpectedly large group.

These safeguards reduce risk, but they are not a substitute for testing and backups.

## Troubleshooting and common situations

| Situation | What it usually means / what to do |
|---|---|
| onesie reports a synthetic or relative Navidrome path | Enable **Report Real Path** for the `onesie` player or the Navidrome server default. |
| A track is queued but never becomes eligible | Check the grace period and make sure the track is still using the configured delete rating. |
| A track disappears from the queue | Its rating changed, so onesie cancelled the deletion as intended. |
| Path is outside the configured music root | Correct the configured music root or path mappings. onesie will not delete outside those paths. |
| Batch guard aborts the run | More tracks became eligible than `max_deletions_per_run` allows. Review the ratings before increasing the limit. |
| beets backend cannot find the track | Confirm that the same file exists in your beets library and that onesie is using the correct beets environment/config. |
| `beet` command not found | The beets backend was selected, but beets is not available in the environment where onesie runs. |
| Apprise test fails | Check that Apprise is installed and that the configured Apprise file is valid. |

## What onesie does not change

- It does not edit audio tags or metadata.
- It does not change artist, album, track, or disc information.
- It does not modify the Navidrome database directly.
- It does not require or modify beets unless you explicitly select the beets backend.
- It does not create a separate "to delete" playlist.
- It does not remove album covers or arbitrary unrelated files from album folders.
- It does not replace your backup or snapshot strategy.

## Current alpha status

`v0.1alpha1` is the first public alpha release.

The goal of the alpha phase is to validate the workflow across different real-world Navidrome setups before onesie is considered safe for production libraries.

For now, use onesie only with test data, disposable copies, or another environment where an incorrect deletion cannot damage your actual music collection.

## AI-assisted development

AI-assisted tools were used during development and documentation. AI-generated or AI-suggested changes included in releases were reviewed and tested by the maintainer.

## Development

For development and testing from a source checkout:

```bash
python -m pip install -e ".[test,notifications]"
pytest
```

Build release packages with:

```bash
python -m build
```

## License

The source code in this repository is released under the MIT License. See [`LICENSE`](LICENSE).

## Legal disclaimer

This is an independent, unofficial project and is not affiliated with or endorsed by Navidrome, beets, Apprise, or their maintainers.

onesie performs destructive filesystem operations when real deletion is enabled. Users are responsible for validating their configuration, permissions, backups, and retention strategy before using it on any library they care about.
