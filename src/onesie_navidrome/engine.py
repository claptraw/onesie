from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .audit import AuditLog, iso_z, parse_time
from .backends import BeetsCliBackend, DeletionBackend, FilesystemBackend
from .config import Config
from .errors import BackendError, OnesieError, SafetyError
from .models import BackendTarget, MappedSong
from .navidrome import NavidromeClient
from .notifications import Notifier
from .pathing import PathMapper
from .state import StateStore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def rating(song: dict[str, Any]) -> int:
    try:
        return int(song.get("userRating", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _song_label(song: MappedSong) -> str:
    artist = song.artist.strip()
    title = song.title.strip() or song.path.name
    album = song.album.strip()
    label = f"{artist} — {title}" if artist else title
    return f"{label} ({album})" if album else label


def _human_duration(seconds: int) -> str:
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"{days} day" if days == 1 else f"{days} days"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    minutes = max(1, seconds // 60)
    return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"


class OnesieEngine:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("onesie")
        self.audit = AuditLog(config.runtime.audit_log)
        self.state = StateStore(config.runtime.state_file)
        self.navidrome = NavidromeClient(config.navidrome)
        self.mapper = PathMapper(config.navidrome, config.filesystem)
        self.notifier = Notifier(config.notifications, self.logger)
        self.backend: DeletionBackend = (
            BeetsCliBackend(
                config.beets, tuple(mapping.local_root for mapping in config.filesystem.path_mappings)
            )
            if config.backend == "beets"
            else FilesystemBackend()
        )

    def _prepare_all(self, mapped: list[MappedSong]) -> dict[str, BackendTarget]:
        self.backend.preflight()
        targets: dict[str, BackendTarget] = {}
        errors: list[str] = []
        for song in mapped:
            try:
                targets[song.key] = self.backend.prepare(song)
            except SafetyError as exc:
                errors.append(str(exc))
        if errors and self.config.policy.strict_validation:
            raise SafetyError(
                f"{len(errors)} delete marker(s) failed {self.backend.name} validation. "
                f"No state or files changed. First error: {errors[0]}"
            )
        for error in errors:
            self.logger.warning("Skipping invalid delete marker: %s", error)
        return targets

    def _reconcile(
        self, state: dict[str, Any], mapped: list[MappedSong], now: datetime
    ) -> tuple[list[str], list[str]]:
        tracks = state["tracks"]
        current = {song.key: song for song in mapped}
        new: list[str] = []
        cancelled: list[str] = []
        for key, song in current.items():
            if key not in tracks:
                tracks[key] = {
                    "first_seen": iso_z(now),
                    "last_seen": iso_z(now),
                    "navidrome_id": song.id,
                    "artist": song.artist,
                    "album": song.album,
                    "title": song.title,
                }
                new.append(key)
                self.audit.write(
                    "queued",
                    path=key,
                    navidrome_id=song.id,
                    artist=song.artist,
                    album=song.album,
                    title=song.title,
                )
            else:
                tracks[key].update(
                    {
                        "last_seen": iso_z(now),
                        "navidrome_id": song.id,
                        "artist": song.artist,
                        "album": song.album,
                        "title": song.title,
                    }
                )
        for key in list(tracks):
            if key not in current:
                old = tracks.pop(key)
                cancelled.append(key)
                self.audit.write(
                    "cancelled",
                    path=key,
                    navidrome_id=old.get("navidrome_id", ""),
                    reason="rating_no_longer_delete_marker",
                )
        return new, cancelled

    def _base_delete_at(self, record: dict[str, Any]) -> datetime:
        return parse_time(record["first_seen"]) + timedelta(seconds=self.config.policy.grace_period_seconds)

    def _delete_at(self, record: dict[str, Any]) -> datetime:
        target = self._base_delete_at(record)
        deferred = record.get("deletion_deferred_until")
        if deferred:
            target = max(target, parse_time(str(deferred)))
        return target

    def _warnings_required(self) -> bool:
        cfg = self.config.notifications
        return cfg.enabled and cfg.notify_before_deletion

    def _warning_due(self, record: dict[str, Any], now: datetime) -> bool:
        if record.get("warning_sent_at"):
            return False
        due_at = self._delete_at(record) - timedelta(
            seconds=self.config.notifications.warning_before_deletion_seconds
        )
        if now < due_at:
            return False
        last_attempt = record.get("last_warning_attempt_at")
        if last_attempt:
            retry_at = parse_time(str(last_attempt)) + timedelta(
                seconds=self.config.notifications.warning_retry_interval_seconds
            )
            if now < retry_at:
                return False
        return True

    def _defer_after_warning_timing_problem(
        self, record: dict[str, Any], now: datetime, *, reason: str, path: str
    ) -> None:
        current_target = self._delete_at(record)
        new_target = max(current_target, now) + timedelta(
            seconds=self.config.notifications.warning_failure_postpone_seconds
        )
        record["deletion_deferred_until"] = iso_z(new_target)
        self.audit.write(
            "deletion_deferred",
            path=path,
            reason=reason,
            previous_delete_at=iso_z(current_target),
            deferred_until=iso_z(new_target),
        )
        self.logger.warning(
            "Deletion deferred for %s until %s (%s)", path, iso_z(new_target), reason
        )

    def _warning_body(self, songs: list[MappedSong], retry: bool) -> str:
        lead = _human_duration(self.config.notifications.warning_before_deletion_seconds)
        if retry:
            intro = "These songs are still scheduled for deletion soon:"
        else:
            intro = f"These songs are scheduled for deletion in about {lead}:"
        lines = [intro, "", *[f"- {_song_label(song)}" for song in songs]]
        lines.extend(
            [
                "",
                "To keep a song, change or remove its delete rating before the deletion run.",
            ]
        )
        return "\n".join(lines)

    def _process_warnings(
        self,
        state: dict[str, Any],
        current: dict[str, MappedSong],
        now: datetime,
        *,
        dry_run: bool,
    ) -> bool:
        if not self._warnings_required():
            return False

        due: list[MappedSong] = []
        had_previous_attempt = False
        for key, song in current.items():
            record = state["tracks"].get(key)
            if not record or not self._warning_due(record, now):
                continue
            due.append(song)
            if record.get("last_warning_attempt_at"):
                had_previous_attempt = True

        if not due:
            return False

        if dry_run:
            self.logger.info(
                "DRY RUN: would send pre-deletion warning for %d track(s)", len(due)
            )
            return True

        sent = self.notifier.send(
            "onesie: deletion warning",
            self._warning_body(due, retry=had_previous_attempt),
            "warning",
        )

        final_window = timedelta(seconds=self.config.notifications.final_warning_window_seconds)
        for song in due:
            record = state["tracks"][song.key]
            record["last_warning_attempt_at"] = iso_z(now)
            record["warning_attempts"] = int(record.get("warning_attempts", 0) or 0) + 1
            delete_at = self._delete_at(record)
            if sent:
                record["warning_sent_at"] = iso_z(now)
                self.audit.write(
                    "warning_sent",
                    path=song.key,
                    navidrome_id=song.id,
                    delete_at=iso_z(delete_at),
                    attempts=record["warning_attempts"],
                )
                # A late first successful warning must still leave a real final warning window.
                if delete_at - now < final_window:
                    self._defer_after_warning_timing_problem(
                        record,
                        now,
                        reason="successful_warning_inside_final_window",
                        path=song.key,
                    )
            else:
                self.audit.write(
                    "warning_failed",
                    path=song.key,
                    navidrome_id=song.id,
                    delete_at=iso_z(delete_at),
                    attempts=record["warning_attempts"],
                )
                # If the last 12-hour safety window is reached without a delivered warning,
                # postpone by 24 hours and keep retrying every configured retry interval.
                if delete_at - now <= final_window:
                    self._defer_after_warning_timing_problem(
                        record,
                        now,
                        reason="warning_failed_inside_final_window",
                        path=song.key,
                    )
        self.state.save(state)
        return True

    def _eligible(
        self,
        state: dict[str, Any],
        current: dict[str, MappedSong],
        now: datetime,
        *,
        require_warning: bool,
    ) -> list[MappedSong]:
        eligible: list[MappedSong] = []
        final_window = timedelta(seconds=self.config.notifications.final_warning_window_seconds)
        for key, song in current.items():
            record = state["tracks"].get(key)
            if not record:
                continue
            delete_at = self._delete_at(record)
            if now < delete_at:
                continue
            if require_warning:
                warning_sent_at = record.get("warning_sent_at")
                if not warning_sent_at:
                    continue
                if now - parse_time(str(warning_sent_at)) < final_window:
                    continue
            eligible.append(song)
        return eligible

    def _live_verify(self, mapped: MappedSong) -> MappedSong:
        if not mapped.id:
            raise SafetyError(f"Queued song has no Navidrome id: {mapped.key}")
        live = self.navidrome.get_song(mapped.id)
        if rating(live) != self.config.policy.delete_rating:
            raise SafetyError(f"Rating changed immediately before deletion: {mapped.key}")
        remapped = self.mapper.map_song(live)
        if remapped.key != mapped.key:
            raise SafetyError(f"Path changed immediately before deletion: {mapped.key} -> {remapped.key}")
        return remapped

    def _sidecar_paths(self, audio_path: Path) -> list[Path]:
        sidecars: list[Path] = []
        for suffix in self.config.filesystem.sidecars:
            sidecar = audio_path.with_suffix(suffix)
            if not sidecar.exists():
                continue
            if sidecar.is_symlink() or not sidecar.is_file():
                self.logger.warning("Leaving unexpected sidecar type untouched: %s", sidecar)
                continue
            sidecars.append(sidecar)
        return sidecars

    def _remove_sidecars(self, audio_path: Path) -> list[str]:
        removed: list[str] = []
        for sidecar in self._sidecar_paths(audio_path):
            try:
                sidecar.unlink()
                removed.append(str(sidecar))
            except OSError as exc:
                self.logger.warning("Could not remove sidecar %s: %s", sidecar, exc)
        return removed

    def _root_for_directory(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        candidates: list[Path] = []
        for mapping in self.config.filesystem.path_mappings:
            root = mapping.local_root.resolve(strict=True)
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            candidates.append(root)
        if not candidates:
            raise SafetyError(f"Refusing directory cleanup outside configured local roots: {path}")
        return max(candidates, key=lambda item: len(item.parts))

    def _cleanup_directories(self, starts: Iterable[Path]) -> list[tuple[Path, Path]]:
        candidates: dict[Path, Path] = {}
        for start in starts:
            root = self._root_for_directory(start)
            current = start
            while current != root:
                candidates[current] = root
                current = current.parent
        return sorted(candidates.items(), key=lambda item: len(item[0].parts), reverse=True)

    def _plan_cleanup(
        self, starts: Iterable[Path], planned_removed: set[Path]
    ) -> tuple[list[Path], list[Path]]:
        if not self.config.filesystem.prune_empty_dirs:
            return [], []
        cleanup_names = set(self.config.filesystem.cleanup_files)
        virtual_removed = set(planned_removed)
        cleanup_files: list[Path] = []
        directories: list[Path] = []

        for current, root in self._cleanup_directories(starts):
            if current in virtual_removed:
                continue
            resolved = current.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise SafetyError(f"Refusing cleanup outside music root: {current}") from exc
            if not current.exists() or current.is_symlink() or not current.is_dir():
                continue
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            remaining = [entry for entry in entries if entry not in virtual_removed]
            if remaining and cleanup_names:
                removable_cleanup = all(
                    entry.is_file()
                    and not entry.is_symlink()
                    and entry.name.lower() in cleanup_names
                    for entry in remaining
                )
                if removable_cleanup:
                    for entry in remaining:
                        cleanup_files.append(entry)
                        virtual_removed.add(entry)
                    remaining = []
            if not remaining:
                directories.append(current)
                virtual_removed.add(current)

        return cleanup_files, directories

    def _execute_cleanup(self, starts: Iterable[Path]) -> tuple[list[str], list[str]]:
        if not self.config.filesystem.prune_empty_dirs:
            return [], []
        cleanup_names = set(self.config.filesystem.cleanup_files)
        removed_files: list[str] = []
        removed_dirs: list[str] = []

        for current, root in self._cleanup_directories(starts):
            resolved = current.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError as exc:
                raise SafetyError(f"Refusing cleanup outside music root: {current}") from exc
            if not current.exists() or current.is_symlink() or not current.is_dir():
                continue
            try:
                entries = list(current.iterdir())
            except OSError as exc:
                self.logger.warning("Could not inspect directory for cleanup %s: %s", current, exc)
                continue

            if entries:
                removable_cleanup = bool(cleanup_names) and all(
                    entry.is_file()
                    and not entry.is_symlink()
                    and entry.name.lower() in cleanup_names
                    for entry in entries
                )
                if not removable_cleanup:
                    continue
                failed = False
                for entry in entries:
                    try:
                        entry.unlink()
                        removed_files.append(str(entry))
                    except OSError as exc:
                        failed = True
                        self.logger.warning("Could not remove cleanup file %s: %s", entry, exc)
                if failed:
                    continue

            try:
                current.rmdir()
                removed_dirs.append(str(current))
            except OSError:
                # Another file/subdirectory may have appeared; leave the directory untouched.
                continue

        return removed_files, removed_dirs

    def _log_dry_run_plan(self, songs: list[MappedSong]) -> tuple[int, int, int, int]:
        audio_paths = [song.path for song in songs]
        sidecars: list[Path] = []
        for audio in audio_paths:
            sidecars.extend(self._sidecar_paths(audio))
        planned_removed = set(audio_paths) | set(sidecars)
        cleanup_files, directories = self._plan_cleanup(
            (audio.parent for audio in audio_paths), planned_removed
        )

        self.logger.info("DRY RUN deletion plan:")
        for path in sorted(audio_paths):
            self.logger.info("DRY RUN: would remove music file: %s", path)
        for path in sorted(set(sidecars)):
            self.logger.info("DRY RUN: would remove sidecar file: %s", path)
        for path in sorted(set(cleanup_files)):
            self.logger.info("DRY RUN: would remove cleanup file: %s", path)
        for path in directories:
            self.logger.info("DRY RUN: would remove directory: %s", path)
        return len(audio_paths), len(set(sidecars)), len(set(cleanup_files)), len(directories)

    def _success_body(
        self,
        deleted: list[MappedSong],
        sidecar_count: int,
        cleanup_file_count: int,
        directory_count: int,
        scan_warning: str,
    ) -> str:
        directory_word = "directory" if directory_count == 1 else "directories"
        lines = [
            "These songs were successfully deleted:",
            "",
            *[f"- {_song_label(song)}" for song in deleted],
            "",
            f"Cleanup: {sidecar_count} sidecar file(s), {cleanup_file_count} cover/cleanup file(s), "
            f"{directory_count} {directory_word}.",
        ]
        if scan_warning:
            lines.extend(["", scan_warning.strip()])
        return "\n".join(lines)

    def run_once(self, *, force_dry_run: bool = False) -> int:
        now = utcnow()
        dry_run = self.config.policy.dry_run or force_dry_run
        self.logger.info("onesie run started: backend=%s dry_run=%s", self.backend.name, dry_run)
        self.audit.write("run_started", backend=self.backend.name, dry_run=dry_run)
        self.navidrome.ping()
        songs = self.navidrome.all_songs()
        marked_raw = [s for s in songs if rating(s) == self.config.policy.delete_rating]
        self.logger.info("Navidrome returned %d songs; %d delete marker(s)", len(songs), len(marked_raw))

        mapped: list[MappedSong] = []
        mapping_errors: list[str] = []
        for song in marked_raw:
            try:
                mapped.append(self.mapper.map_song(song))
            except SafetyError as exc:
                mapping_errors.append(str(exc))
        if mapping_errors and self.config.policy.strict_validation:
            raise SafetyError(
                f"{len(mapping_errors)} delete marker(s) failed path validation. "
                f"No state or files changed. First error: {mapping_errors[0]}"
            )
        for error in mapping_errors:
            self.logger.warning("Skipping invalid delete marker: %s", error)

        targets = self._prepare_all(mapped)
        mapped = [song for song in mapped if song.key in targets]
        current = {song.key: song for song in mapped}

        state = self.state.load()
        new, cancelled = self._reconcile(state, mapped, now)
        self.state.save(state)
        warning_activity = self._process_warnings(state, current, now, dry_run=dry_run)
        eligible = self._eligible(
            state,
            current,
            now,
            require_warning=self._warnings_required() and not dry_run,
        )
        self.logger.info(
            "Queue=%d new=%d cancelled=%d eligible=%d",
            len(state["tracks"]), len(new), len(cancelled), len(eligible),
        )

        if len(eligible) > self.config.policy.max_deletions_per_run:
            message = (
                f"{len(eligible)} tracks are eligible, exceeding max_deletions_per_run="
                f"{self.config.policy.max_deletions_per_run}. No files were deleted."
            )
            self.audit.write("guard_abort", reason="max_deletions_per_run", eligible=len(eligible))
            self.notifier.send("onesie: batch guard aborted", message, "failure")
            raise SafetyError(message)

        if not eligible:
            if self.config.notifications.notify_on_noop and not warning_activity:
                self.notifier.send("onesie: nothing to delete", f"{len(state['tracks'])} track(s) queued.")
            return 0

        deleted: list[MappedSong] = []
        dry_run_items: list[MappedSong] = []
        failures: list[str] = []
        removed_sidecars: list[str] = []
        affected_dirs: set[Path] = set()

        if dry_run:
            for original in eligible:
                try:
                    live = self._live_verify(original)
                    self.backend.prepare(live)
                    dry_run_items.append(live)
                    self.audit.write(
                        "dry_run",
                        path=live.key,
                        navidrome_id=live.id,
                        backend=self.backend.name,
                    )
                except (OnesieError, OSError, RuntimeError) as exc:
                    failures.append(f"{original.key}: {exc}")
                    self.audit.write("dry_run_failed", path=original.key, detail=str(exc))
                    self.logger.error("Dry-run validation failed for %s: %s", original.key, exc)
            if dry_run_items:
                counts = self._log_dry_run_plan(dry_run_items)
                if self.config.notifications.notify_on_dry_run:
                    self.notifier.send(
                        "onesie: dry run",
                        f"Would remove {counts[0]} music file(s), {counts[1]} sidecar file(s), "
                        f"{counts[2]} cover/cleanup file(s), and {counts[3]} director(y/ies).",
                        "info",
                    )
            return 2 if failures else 0

        for original in eligible:
            try:
                live = self._live_verify(original)
                target = self.backend.prepare(live)
                result = self.backend.delete(target)
                if not result.deleted:
                    raise BackendError(f"Backend did not confirm deletion: {live.key}")
                if live.path.exists():
                    raise BackendError(f"Audio file still exists after backend deletion: {live.path}")
                removed_sidecars.extend(self._remove_sidecars(live.path))
                affected_dirs.add(live.path.parent)
                state["tracks"].pop(live.key, None)
                self.state.save(state)
                self.audit.write(
                    "deleted",
                    path=live.key,
                    navidrome_id=live.id,
                    artist=live.artist,
                    album=live.album,
                    title=live.title,
                    backend=self.backend.name,
                    backend_ref=target.backend_ref,
                )
                deleted.append(live)
            except (OnesieError, OSError, RuntimeError) as exc:
                failures.append(f"{original.key}: {exc}")
                self.audit.write("delete_failed", path=original.key, detail=str(exc))
                self.logger.error("Deletion failed for %s: %s", original.key, exc)

        cleanup_files: list[str] = []
        pruned_dirs: list[str] = []
        if affected_dirs:
            cleanup_files, pruned_dirs = self._execute_cleanup(affected_dirs)
            if cleanup_files or pruned_dirs:
                self.audit.write(
                    "cleanup_completed",
                    cleanup_files=cleanup_files,
                    pruned_directories=pruned_dirs,
                )

        scan_warning = ""
        if deleted and self.config.navidrome.trigger_scan:
            try:
                self.navidrome.start_scan()
            except OnesieError as exc:
                scan_warning = f"Navidrome scan could not be started: {exc}"
                self.logger.warning(scan_warning)
                self.audit.write("scan_failed", detail=str(exc))

        if failures:
            body = (
                f"Deleted {len(deleted)} track(s); {len(failures)} failed. First failure: {failures[0]}."
                + (f" {scan_warning}" if scan_warning else "")
            )
            self.notifier.send("onesie: deletion run completed with errors", body, "failure")
            return 2
        if deleted and self.config.notifications.notify_after_deletion:
            self.notifier.send(
                "onesie: songs successfully deleted",
                self._success_body(
                    deleted,
                    len(removed_sidecars),
                    len(cleanup_files),
                    len(pruned_dirs),
                    scan_warning,
                ),
                "success",
            )
        return 0

    def status(self) -> dict[str, Any]:
        state = self.state.load()
        now = utcnow()
        rows = []
        for key, record in sorted(state["tracks"].items()):
            first_seen = parse_time(record["first_seen"])
            age = max(0, int((now - first_seen).total_seconds()))
            delete_at = self._delete_at(record)
            row = {
                "path": key,
                **record,
                "age_seconds": age,
                "scheduled_delete_at": iso_z(delete_at),
            }
            if self._warnings_required() and not record.get("warning_sent_at"):
                row["warning_due_at"] = iso_z(
                    delete_at
                    - timedelta(seconds=self.config.notifications.warning_before_deletion_seconds)
                )
                last_attempt = record.get("last_warning_attempt_at")
                if last_attempt:
                    row["next_warning_attempt_at"] = iso_z(
                        parse_time(str(last_attempt))
                        + timedelta(seconds=self.config.notifications.warning_retry_interval_seconds)
                    )
            rows.append(row)
        return {"backend": self.backend.name, "dry_run": self.config.policy.dry_run, "tracks": rows}

    def doctor(self) -> list[str]:
        findings: list[str] = []
        for mapping in self.config.filesystem.path_mappings:
            root = mapping.local_root
            if not root.is_dir():
                raise SafetyError(f"configured local music root is not a directory: {root}")
            findings.append(f"music root mapping: OK ({mapping.server_root} -> {root})")
        self.navidrome.ping()
        findings.append("Navidrome authentication: OK")
        songs = self.navidrome.all_songs()
        findings.append(f"Navidrome catalog read: OK ({len(songs)} songs)")
        if songs:
            sample = songs[0]
            try:
                self.mapper.map_song(sample)
                findings.append("Report Real Path / path mapping: OK")
            except SafetyError as exc:
                findings.append(f"Report Real Path / path mapping: ATTENTION ({exc})")
        self.backend.preflight()
        findings.append(f"deletion backend: OK ({self.backend.name})")
        if self.config.notifications.enabled:
            self.notifier._load()
            findings.append("Apprise: OK")
        else:
            findings.append("Apprise: disabled")
        return findings
