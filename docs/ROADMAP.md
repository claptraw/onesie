# Roadmap

## Near term

- Live validation against a real Navidrome 0.63.x instance in both filesystem and Beets modes.
- Thin optional Beets plugin wrapper providing `beet onesie` while reusing the same core policy.
- Release automation and PyPI publication under the distribution name `onesie-navidrome`.
- Additional recovery/reporting commands for stale queue entries and missing files.

## Later

- Evaluate a native Navidrome WASM edition for Navidrome-only users who want zero external scheduler/runtime.
- Keep the WASM edition and CLI edition behaviorally aligned, while accepting that local Apprise/Beets integrations are CLI-runtime features unless Navidrome exposes suitable host capabilities.
- Multi-user deletion policies only after single-owner semantics are mature and well tested.
