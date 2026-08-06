# Changelog

## 26.08.06.2
- Tuned hotend-fan tachometer polling to 0.75 ms across supported firmware, retaining margin for the measured 13,553 RPM maximum while reducing THR polling from firmware 01.01.06.05.

## 26.08.06.1
- Prevented the articulated cable-chain mount from striking the rear enclosure by orienting it with a full-width X traverse before rear-bed scraping and moving the scrape range to Y392–Y395.
- Increased travel to the randomized Z-home point to 750 mm/s and widened independent X/Y randomization to ±10 mm.

## 26.08.05.1
- Reset QIDI Box tool-to-slot mappings to identity after successful prints while preserving touchscreen and runout mappings during active or interrupted prints, with missing-mapping repair and an idle-only Fluidd/console reset macro.
- Added GPLv3 project licensing and bundled `LICENSE`, `THIRD_PARTY_NOTICES.md`, and component license texts under `LICENSES/`.

## 26.07.28.1
- Added guarded support for both known QIDI firmware `01.01.06.04` `homing.py` variants while preserving each variant's endstop synchronization behavior.
- Included live and accepted source hashes in unsupported managed-source drift errors.

## 26.07.03.2
- Fixed `26.07.03.1` release tooling so the publish workflow can compare the previous package version without requiring the old manifest to match the current schema.

## 26.07.03.1
- Patched Moonraker `.gcode.3mf` plate metadata extraction so plate-indexed G-code, JSON, and thumbnails resolve from `Metadata/slice_info.config`.
- Moved the front prime line ahead of first-layer object bounds when bed room exists, with the fixed front-center line retained as fallback.

## 26.06.15.1
- Tightened interactive installer prompts to accept explicit `Y/YES` or `N/NO` and re-prompt on invalid input.
- Prompted for system optimization policy again during interactive reinstall while keeping stored policy reuse for noninteractive installs and auto-update.

## 26.06.13.1
- Added dual QIDI Box tool-slot mapping support and runtime reconciliation when recorded box counts change.

## 26.06.11.1
- Added installer support for QIDI Max 4 firmware `01.01.06.04` alongside the existing `01.01.06.03` baseline.
- Added firmware-scoped stock baselines and guarded installer patch handling for supported firmware versions.

## 26.06.04.1
- Preserved the active print Z offset across startup reset, KAMP mesh save, and offset reapply.
- Kept retained-filament startup waiting at the purge chute while bed and chamber reach target temperature.
- Tracked retained QIDI Box filament from `slot_sync` so auto-runout reloads can be reused when the next print selects the reloaded slot.
- Suppressed disabled-timelapse console noise by setting stock `TIMELAPSE_TAKE_FRAME` verbose output off during install.
- Fixed QIDI Studio end G-code compatibility by avoiding unsupported indexed completion-air-filtration placeholders.
- Stopped SysV-backed services explicitly after system optimization disablement so `xl2tpd` does not remain active after install.

## 26.05.27.1
- Removed hardcoded shaper algo for x and y

## 26.05.21.1
- Fixed legacy manual install reset when stock `config/KAMP` is a symlink

## 26.05.19.1
- Removed the older QIDI Max 4 firmware baseline from installer support
- Added system optimizations (DNS, APT, qidiclient, algo_app)

## 26.05.04.1
- Added probe accuracy and screw-tilt helper macros
- Added `TLTG_SET_BOX_TEMP` macro to be able to set the box temp from fluidd
- Installer hardening
- Optimized nozzle cleaning
- Clean nozzle post print
- Finalized slicer gcode contract
