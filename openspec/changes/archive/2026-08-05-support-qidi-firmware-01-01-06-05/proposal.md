## Why

QIDI Max 4 firmware `01.01.06.05` is available, but the installer currently rejects it before backup or live writes. The repository needs a validated `.05` baseline so existing optimized behavior can be installed, restored, and uninstalled without accepting unknown firmware state or discarding QIDI's firmware-managed changes.

## What Changes

- Admit firmware `01.01.06.05` while retaining `.03` and `.04` support.
- Add `.05` guarded configuration variants using the unchanged `.04` expected and optimized values.
- Add the `.05` `homing.py` stock preimage `0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761` and the existing sync-reset optimized payload.
- Add a sanitized `.05` stock snapshot from `Qidi-Max4-Defaults` commit `c75c0b662d1d4fd2a7dd19e49843b91e6544a1ed`, including its package-owned macro state and excluding sensitive/runtime-owned files.
- Preserve QIDI's `.05` polar-cooler policy: no optimized P4 mutation is added to pause or resume, and the removed polar-cooler smart-pin object is not recreated.
- Keep package version `26.08.05.1`; the existing version was not fully released.
- Extend installer, source-patch, restore, and bundle tests across `.03`, `.04`, and `.05`.
- Update external platform and QIDI Box observations with the `.05` config, compiled-module, and qidiclient evidence. No qidiclient binary, firmware-managed Klipper module, slicer G-code, or start-print command-path change is introduced.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `installer-lifecycle`: Add firmware admission, guarded patch coverage, source provenance, stock restoration, and compatibility metadata for `01.01.06.05`.
- `optimized-printer-behavior`: Extend the validated sync-reset homing baseline to `.05` and define that pause/resume does not add optimized polar-cooler state changes on this firmware.

## Impact

Affected paths include `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`, `installer/stock/qidi-max4-defaults/firmwares/`, installer fixtures and tests, bundle smoke validation, `openspec/specs/installer-lifecycle/spec.md`, `openspec/specs/optimized-printer-behavior/spec.md`, and relevant `openspec/observations/` files. Installer runtime schemas and algorithms remain unchanged; legacy restoration already selects snapshots by detected firmware. The static-GIF optimization remains enabled and continues using the existing qidiclient asset paths.
