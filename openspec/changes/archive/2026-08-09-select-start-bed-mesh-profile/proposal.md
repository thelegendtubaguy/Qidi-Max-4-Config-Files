## Why

Optimized print start always runs a fresh adaptive mesh, so an operator cannot configure the printer to reuse a named saved mesh profile. Mesh selection belongs to persistent Klipper state so every compatible slicer and previously sliced file receives the same behavior without G-code changes.

## What Changes

- Read the optional persistent Klipper saved variable `tltg_start_bed_mesh_profile` during optimized print preparation.
- Continue fresh adaptive `kamp` calibration when the variable is absent or contains an empty string.
- When the variable contains a profile name, ask Klipper to load that exact saved profile and skip print-start mesh calibration.
- Report to the Klipper console whether print preparation is loading the named saved profile or calibrating a fresh adaptive `kamp` mesh.
- Let Klipper stop print preparation when the requested profile cannot be loaded rather than silently printing without compensation or creating a replacement mesh.
- Keep OrcaSlicer and QIDI Studio G-code unchanged and independent from the printer-side preference.
- Document the Klipper console commands for setting and clearing the preference.
- Keep Z tilt, offset handling, temperature waits, filament preparation, and sensor preparation unchanged for both mesh paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: Define persistent printer-side saved-mesh selection, backward-compatible adaptive fallback, and identical behavior across slicer and filament-source paths.

## Impact

- Optimized mesh orchestration in `installer/klipper/tltg-optimized-macros/`.
- Operator reference commands in `README.md`.
- Start-print path contract and generated views.
- Installer package and upgrade-source metadata for version `26.08.09.1`.
- Focused macro, slicer-invariance, saved-variable, and missing-profile validation.
