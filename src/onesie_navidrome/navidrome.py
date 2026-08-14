from __future__ import annotations

import hashlib
import secrets
from typing import Any

import requests

from .config import NavidromeConfig
from .errors import OnesieError


class NavidromeClient:
    def __init__(self, config: NavidromeConfig):
        self.config = config
        self.session = requests.Session()

    def _auth_params(self) -> dict[str, str]:
        salt = secrets.token_hex(8)
        try:
            digest = hashlib.md5(
                (self.config.password + salt).encode("utf-8"), usedforsecurity=False
            ).hexdigest()
        except TypeError:  # Python builds without usedforsecurity keyword
            digest = hashlib.md5((self.config.password + salt).encode("utf-8")).hexdigest()  # noqa: S324
        return {
            "u": self.config.username,
            "t": digest,
            "s": salt,
            "v": self.config.api_version,
            "c": self.config.client,
            "f": "json",
        }

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query: dict[str, Any] = self._auth_params()
        if params:
            query.update(params)
        try:
            response = self.session.get(
                f"{self.config.url}/rest/{method}",
                params=query,
                timeout=self.config.request_timeout,
                verify=self.config.verify_tls,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise OnesieError(f"Navidrome request {method} failed: {exc}") from exc
        wrapper = payload.get("subsonic-response")
        if not isinstance(wrapper, dict):
            raise OnesieError(f"Navidrome returned an invalid Subsonic response for {method}")
        if wrapper.get("status") != "ok":
            error = wrapper.get("error") or {}
            raise OnesieError(
                f"Navidrome {method} failed: {error.get('code', '?')} {error.get('message', 'unknown error')}"
            )
        return wrapper

    def ping(self) -> None:
        self.call("ping")

    def all_songs(self) -> list[dict[str, Any]]:
        songs: list[dict[str, Any]] = []
        offset = 0
        seen_pages: set[tuple[Any, ...]] = set()
        while True:
            wrapper = self.call(
                "search3",
                {
                    "query": "",
                    "artistCount": 0,
                    "albumCount": 0,
                    "songCount": self.config.page_size,
                    "songOffset": offset,
                },
            )
            page = (wrapper.get("searchResult3") or {}).get("song") or []
            if not isinstance(page, list):
                raise OnesieError("Navidrome search3 returned an invalid song list")
            if not page:
                break
            signature = (len(page), page[0].get("id"), page[-1].get("id"))
            if signature in seen_pages:
                raise OnesieError("Navidrome search3 pagination repeated a page; aborting")
            seen_pages.add(signature)
            songs.extend(page)
            if len(page) < self.config.page_size:
                break
            offset += len(page)
            if offset > 2_000_000:
                raise OnesieError("Navidrome returned an implausibly large library; aborting")
        return songs

    def get_song(self, song_id: str) -> dict[str, Any]:
        wrapper = self.call("getSong", {"id": song_id})
        song = wrapper.get("song")
        if not isinstance(song, dict):
            raise OnesieError(f"Navidrome getSong returned no song for id {song_id}")
        return song

    def start_scan(self) -> None:
        self.call("startScan", {"fullScan": "false"})
