# AGENTS.md

## Mandatory rules

1. Never modify `config/fluidd.cfg`; it is read-only on the printer. Implement related behavior elsewhere.
2. Preserve machine/vendor behavior unless explicitly asked to change it.
3. Never add, restore, or commit unredacted hardware identifiers. Treat `config/MCU_ID.cfg` and `config/box.cfg` as sensitive.
4. Keep stock-mapped config files stock:
   - `config/` is stock-mapped except redactions, approved comment translation, generated state blocks, and minimal include wiring.
   - Do not tune values directly in `config/printer.cfg` or stock-mapped `config/klipper-macros-qd/*.cfg`.
   - Stock-value changes belong in guarded `installer/package.yaml patches.*` entries.
   - Klipper behavior changes belong in `installer/klipper/tltg-optimized-macros/`.
   - Slicer behavior changes belong in `orcaslicer_gcode/` and `qidistudio_gcode/`.
   - Installer/runtime metadata belongs under `installer/`.
   - If a stock-mapped `config/` edit is unavoidable, call it out before editing and keep the diff minimal.
5. Use QIDI stock baseline for comparisons: `https://github.com/thelegendtubaguy/Qidi-Max4-Defaults`.
6. Keep OrcaSlicer and QIDI Studio G-code packs functionally aligned while preserving each slicer's syntax/placeholders. Exception: do not add polar cooler controls to `qidistudio_gcode/` unless explicitly asked.
7. Update `openspec/specs/` when desired behavior changes, `openspec/contracts/` when a machine-checked path contract changes, and `openspec/observations/` when external/vendor evidence changes.
8. Translate comments only unless explicitly told otherwise. Leave runtime/status/warning strings unchanged unless the affected string set is approved.
9. Keep `README.md` operator-focused. Do not link internal agent references under `openspec/` from the README unless explicitly requested.
10. Kevin O'Connor's `kevin@koconnor.net` may be retained only in upstream Klipper GPL copyright headers; this approved repository-content email exception requires no additional confirmation.

## Git workflow

- Create feature, bugfix, and general work branches from the latest `origin/dev` unless explicitly told otherwise.
- Open PRs against `dev` unless the PR is a release merge.

## QIDI Studio slicer G-code constraints

- QIDI Studio is not OrcaSlicer with identical placeholder parsing; keep `qidistudio_gcode/` syntax separately tested instead of copying Orca expressions verbatim.
- Do not use `activate_air_filtration_on_completion[...]` or `complete_print_exhaust_fan_speed[...]` in `qidistudio_gcode/`; QIDI Studio 2.6 reports `Not a variable name` for indexed completion-air-filtration placeholders in end G-code. Use a constant `EXHAUST_SPEED=0` unless a QIDI Studio-tested replacement exists.
- Keep QIDI Studio `{if}`, `{else}`, and `{endif}` blocks on separate lines when editing conditionals; this is the tested style used by `qidistudio_gcode/*.gcode`.
- Do not add direct polar cooler controls such as `M106 P4 ...` to `qidistudio_gcode/` unless explicitly requested and validated in QIDI Studio.

## Required agent references

- Desired installer, guarded patch, uninstall, recovery, restore, system optimization, and auto-update behavior:
  - `openspec/specs/installer-lifecycle/spec.md`
- Desired homing, mesh, start/end, filament, cooling, helper, and slicer behavior:
  - `openspec/specs/optimized-printer-behavior/spec.md`
- Start-print branch and command-order contract:
  - `openspec/contracts/gcode-paths/start-print.path.json`
  - `openspec/contracts/gcode-paths/generated/start-print.md`
- External QIDI platform behavior:
  - `openspec/observations/qidi-platform.md`
- QIDI Box topology, commands, state, hardware, metadata, evidence limits, and unresolved behavior:
  - `openspec/observations/qidi-box/topology-and-control.md`
  - `openspec/observations/qidi-box/state-and-commands.md`
  - `openspec/observations/qidi-box/hardware-and-protocols.md`
  - `openspec/observations/qidi-box/material-metadata.md`
  - `openspec/observations/qidi-box/evidence-and-open-questions.md`

## OpenSpec maintenance

- Specifications define repository-controlled desired behavior. Keep requirements behavioral and scenarios testable; use manifest/tests/code as the authority for exhaustive data that is not itself an operator-visible behavior.
- Contracts define controlled machine-checked invariants that are too detailed for requirements. Generated contract views are outputs, not independent authority.
- Observations define behavior outside repository control. Preserve evidence qualifiers from `openspec/observations/README.md`; do not convert harness/static inference into a product requirement or confirmed vendor behavior.
- Update existing specifications and observations as current desired state/evidence. Do not create append-only history or duplicate the same rule across capabilities.
- Describe functional behavior rather than raw diffs. Group related effects by operator or print-sequence behavior and include source paths only where they add traceability.

## Start-print path contract

Before changing start-print behavior:

1. Read `openspec/specs/optimized-printer-behavior/spec.md`, `openspec/contracts/gcode-paths/start-print.path.json`, and `openspec/contracts/gcode-paths/generated/start-print.md`.
2. Update `openspec/contracts/gcode-paths/start-print.path.json` when a branch-level invariant changes.
3. Regenerate and check generated views:

   ```bash
   python3 scripts/check_gcode_paths.py --write
   python3 scripts/check_gcode_paths.py
   ```

4. Include regenerated `openspec/contracts/gcode-paths/generated/start-print.md` and `.mmd` when they change.
5. If generated views do not change after a concrete start-path command change, state why the command is not a branch-level invariant.

Contracted start-path sources include `orcaslicer_gcode/start.gcode`, `qidistudio_gcode/start.gcode`, `config/box.cfg`, `config/klipper-macros-qd/*.cfg`, and `installer/klipper/tltg-optimized-macros/*.cfg`.

## Common paths

- Active runtime include graph: `config/printer.cfg`
  - `MCU_ID.cfg`, `timelapse.cfg`, `klipper-macros-qd/*.cfg`, `tltg-optimized-macros/*.cfg`, `box.cfg`
- Runtime optimized macro source: `installer/klipper/tltg-optimized-macros/`
- Start/end: `config/klipper-macros-qd/start_end.cfg`, `installer/klipper/tltg-optimized-macros/start_end.cfg`
- Homing: `config/klipper-macros-qd/kinematics.cfg`, `installer/klipper/tltg-optimized-macros/kinematics.cfg`
- Filament: `config/klipper-macros-qd/filament.cfg`, `installer/klipper/tltg-optimized-macros/filament.cfg`
- Adaptive mesh: `config/klipper-macros-qd/bed_mesh.cfg`, optimized wrappers under `installer/klipper/tltg-optimized-macros/`
- Cooling: `installer/klipper/tltg-optimized-macros/cooling.cfg`
- Pause/resume/cancel: `config/klipper-macros-qd/pause_resume_cancel.cfg`
- `config/KAMP/*.cfg` exists but is not this machine's active adaptive mesh path.
- `config/box.cfg` is actively included.

## Validation

- If editing `installer/klipper/tltg-optimized-macros/**/*.cfg`, run:

  ```bash
  python3 scripts/format_klipper_configs.py
  ```

- If editing `installer/package.yaml` or `installer/supported_upgrade_sources.yaml`, run:

  ```bash
  python3 scripts/check_installer_known_versions.py
  ```

- If editing slicer G-code, run:

  ```bash
  python3 scripts/check_optimized_slicer_macros.py
  ```

- If changing installer behavior, run:

  ```bash
  python3 scripts/run_installer_core_tests.py
  ```

- If editing launcher, bundle, or release plumbing, run:

  ```bash
  python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test
  ```

- If changing start-print behavior or contract paths, follow the start-print path contract above.
- If changing OpenSpec content, run:

  ```bash
  openspec validate --all --strict
  ```

## Timing and terminology

- `G4 P...` is fixed dead time; `M400` waits only for queued motion to finish.
- For conservative speedups, trim fixed `G4` waits before changing motion speeds/accelerations.
- Stock timing/behavior knobs remain in `config/klipper-macros-qd/globals.cfg`; optimized-only globals live in `installer/klipper/tltg-optimized-macros/globals.cfg`.
- Treat apparently unused stock globals as externally consumed unless proven otherwise.
- Use `purge` only for extrusion over the rear waste chute/wiper area.
- Use `prime line` for the front-of-bed extrusion in slicer start G-code.
