## Context

`OPTIMIZED_START_PRINT_FILAMENT_PREP` currently sets `G31` and repeats the same `BED_MESH_CLEAR`, Z-home, `BED_MESH_CALIBRATE PROFILE=kamp`, profile-state save, and configuration-save sequence in its retained-filament, fresh-Box, and external-spool branches. Both slicer packs call that macro without mesh-selection input, and existing sliced files have the same call shape.

Klipper exposes persistent values through `printer.save_variables.variables` and owns named mesh lookup through `BED_MESH_PROFILE LOAD=<name>`. Optimized behavior remains under `installer/klipper/tltg-optimized-macros/`; stock-mapped configuration and both slicer G-code packs remain unchanged.

## Goals / Non-Goals

**Goals:**

- Preserve fresh adaptive meshing when no printer-side preference is configured.
- Apply one persistent saved-profile preference to every optimized slicer and filament-source path.
- Forward the configured profile name without case normalization or an allowlist.
- Centralize repeated mesh preparation so selection cannot drift between branches.
- Keep installation non-owning with respect to the optional saved variable.
- Provide direct console commands for configuring and clearing the preference.

**Non-Goals:**

- Add or accept a slicer G-code mesh parameter.
- Add a slicer UI setting or modify OrcaSlicer or QIDI Studio G-code.
- Generate, repair, or select a profile automatically when a configured name is unavailable.
- Skip Z tilt, homing, nozzle preparation, temperature waits, offset application, or sensor preparation.
- Change stock `G29`, `G31`, or `G32` definitions.

## Decisions

### Use the optional saved variable `tltg_start_bed_mesh_profile`

The shared optimized mesh helper reads:

```jinja
{% set profile = printer.save_variables.variables.tltg_start_bed_mesh_profile|default('')|string %}
```

An absent key or empty string selects fresh adaptive calibration. Any non-empty string selects saved-profile loading. The value is not stripped, case-normalized, restricted to `default`, or interpreted as a special adaptive-mode token; Klipper remains authoritative for matching the configured name.

A volatile `gcode_macro` variable was rejected because it would be lost across Klipper restarts. Reusing `profile_name` was rejected because existing QIDI macros write it as last-profile metadata, so it cannot represent an operator preference. A slicer parameter was rejected because it duplicates printer policy across profiles and cannot update existing sliced files.

### Keep the preference outside installer ownership

Installation does not create, initialize, migrate, or remove `tltg_start_bed_mesh_profile`. The README documents direct console operations:

```gcode
SAVE_VARIABLE VARIABLE=tltg_start_bed_mesh_profile VALUE='"default"'
SAVE_VARIABLE VARIABLE=tltg_start_bed_mesh_profile VALUE='""'
```

The first command selects a saved profile; the second restores adaptive calibration. The saved value persists through restart and uninstall because it is operator-owned state in `saved_variables.cfg`.

A dedicated setter macro was rejected because `SAVE_VARIABLE` already provides the required persistent interface and a second command surface would not improve runtime safety.

### Centralize selection in one optimized helper

A private helper reads the saved preference after each filament-source branch completes Z tilt. For an empty preference, it reports `Calibrating fresh adaptive KAMP bed mesh` through `action_respond_info`, then performs the current `G31`, extruder disable, mesh clear, safe Z-home, adaptive `kamp` calibration, profile-state save, short settle, and `SAVE_CONFIG_QD` sequence. For a non-empty preference, it reports `Loading saved bed mesh profile: <name>` through `action_respond_info`, then performs `G32`, clears stale active mesh state, and issues `BED_MESH_PROFILE LOAD="{profile}"` without probing or saving configuration. Klipper's `printer.bed_mesh.profile_name` remains authoritative; the named-profile path does not copy arbitrary operator input into QIDI's legacy `profile_name` saved-variable metadata.

The configured name is quoted and embedded double quotes are escaped using Klipper's documented macro pattern so mixed-case and whitespace-containing names survive shell-style extended-G-code parsing. Klipper's load command owns missing-profile errors; the helper contains no calibration fallback in the named-profile branch.

Each retained-filament, fresh-Box, and external-spool branch calls the helper before offset application, final positioning, and sensor enablement. This removes three duplicated command blocks while preserving branch-specific filament and cleaning behavior.

Calling stock `G29` was rejected because its `kamp` branch performs a full `G28`, uses a longer fixed wait, and couples selection to mutable `_km_globals.bedmesh_before_print` state before the optimized branch reaches mesh preparation.

### Keep slicer files byte-equivalent to the current path

`orcaslicer_gcode/start.gcode` and `qidistudio_gcode/start.gcode` retain their existing parameter-free `OPTIMIZED_START_PRINT_FILAMENT_PREP` calls and receive no explanatory comments or alternate calls. The start-print contract continues to own those exact entrypoint strings and forbids `BED_MESH_PROFILE` from slicer G-code.

### Extend the path contract and focused static validation

The start-print contract records the shared helper call in all three filament branches and defines mutually exclusive adaptive and named-profile helper paths. Generated Markdown and Mermaid views are regenerated.

Existing macro contract coverage verifies absent/empty preference fallback, exact saved-variable lookup, one mode-specific console report per path, quoted profile loading, Klipper-owned missing-profile failure, calibration exclusion from the named-profile path, and branch-wide helper use. Slicer validation verifies that neither pack references `BED_MESH_PROFILE` or `tltg_start_bed_mesh_profile`.

## Risks / Trade-offs

- [A selected saved mesh no longer reflects current bed state] → Require explicit persistent operator configuration and keep adaptive calibration as the absent/empty default.
- [The operator specifies a missing or misspelled profile] → Let `BED_MESH_PROFILE LOAD=<name>` fail instead of printing uncompensated or unexpectedly probing.
- [A stale preference affects every slicer] → Document the empty-string clear command and keep the preference in one inspectable saved variable.
- [Nested parsing truncates a profile name at whitespace] → Quote the native load argument and escape embedded double quotes.
- [Repeated branch logic diverges] → Route every filament source through one private helper and contract each call site.

## Migration Plan

Installer update adds preference-aware macro behavior without creating the preference. Existing printers and sliced files continue fresh `kamp` calibration because the key is absent. Operators opt in through `SAVE_VARIABLE`; no slicer migration is required.

Rollback restores the prior optimized macro. The operator-owned saved variable may remain unused and does not alter stock behavior or uninstall state.
