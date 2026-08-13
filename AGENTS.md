# AGENTS.md

## Safety and ownership

- Never modify `config/fluidd.cfg`; it is read-only on the printer. Implement related behavior elsewhere.
- Preserve machine/vendor behavior unless explicitly asked to change it. Compare against `https://github.com/thelegendtubaguy/Qidi-Max4-Defaults`.
- Never add, restore, or commit unredacted hardware identifiers. Treat `config/MCU_ID.cfg` and `config/box.cfg` as sensitive.
- Keep `config/` stock-mapped except for redactions, approved comment translation, generated state blocks, and minimal include wiring. Do not tune `config/printer.cfg` or stock-mapped `config/klipper-macros-qd/*.cfg`; put stock-value changes in guarded `installer/package.yaml patches.*`, Klipper behavior in `installer/klipper/tltg-optimized-macros/`, slicer behavior in `orcaslicer_gcode/` and `qidistudio_gcode/`, and installer/runtime metadata under `installer/`. Call out unavoidable stock-mapped edits before making them and keep them minimal.
- Translate comments only unless explicitly told otherwise. Leave runtime/status/warning strings unchanged unless the affected string set is approved.
- Keep `README.md` operator-focused; do not link internal `openspec/` agent references unless explicitly requested.
- `kevin@koconnor.net` is permitted only in upstream Klipper GPL copyright headers.

## Git

- Create feature, bugfix, and general branches from latest `origin/dev` unless explicitly told otherwise. Target PRs to `dev` except release merges.

## Slicer constraints

- Keep OrcaSlicer and QIDI Studio packs functionally aligned while preserving each slicer's syntax and placeholders.
- Treat QIDI Studio syntax as independently tested: do not copy Orca expressions verbatim; keep `{if}`, `{else}`, and `{endif}` on separate lines.
- In `qidistudio_gcode/`, do not use `activate_air_filtration_on_completion[...]` or `complete_print_exhaust_fan_speed[...]`; QIDI Studio 2.6 reports `Not a variable name`. Use `EXHAUST_SPEED=0` unless a QIDI Studio-tested replacement exists.
- Do not add direct polar-cooler controls such as `M106 P4 ...` to `qidistudio_gcode/` unless explicitly requested and validated in QIDI Studio.

## OpenSpec

Read as applicable:

- Installer lifecycle: `openspec/specs/installer-lifecycle/spec.md`
- Printer behavior: `openspec/specs/optimized-printer-behavior/spec.md`
- Start path: `openspec/contracts/gcode-paths/start-print.path.json` and `openspec/contracts/gcode-paths/generated/start-print.md`
- QIDI platform: `openspec/observations/qidi-platform.md`
- QIDI Box: `openspec/observations/qidi-box/{topology-and-control,state-and-commands,hardware-and-protocols,material-metadata,evidence-and-open-questions}.md`

- Update `openspec/specs/` for desired behavior, `openspec/contracts/` for machine-checked path contracts, and `openspec/observations/` for external/vendor evidence.
- Specs define repository-controlled behavior: keep requirements behavioral and scenarios testable; manifests, tests, and code own exhaustive non-operator-visible data.
- Contracts own detailed machine-checked invariants; generated views are outputs, not authority.
- Observations describe external behavior: retain qualifiers from `openspec/observations/README.md`; do not promote harness/static inference to requirements or confirmed vendor behavior.
- Keep specs and observations current, not append-only; avoid duplicate rules. Describe behavior rather than diffs and include paths only when they improve traceability.

## Start-print changes

Before changing start-print behavior, read the printer spec, contract, and generated view above. Update `start-print.path.json` for branch-level invariants, then run:

```bash
python3 scripts/check_gcode_paths.py --write
python3 scripts/check_gcode_paths.py
```

Include changed generated `.md` and `.mmd` files. If a concrete command change leaves generated views unchanged, state why it is not a branch-level invariant. Contracted sources are `orcaslicer_gcode/start.gcode`, `qidistudio_gcode/start.gcode`, `config/box.cfg`, `config/klipper-macros-qd/*.cfg`, and `installer/klipper/tltg-optimized-macros/*.cfg`.

## Runtime paths

- Include graph: `config/printer.cfg` → `MCU_ID.cfg`, `timelapse.cfg`, `klipper-macros-qd/*.cfg`, `tltg-optimized-macros/*.cfg`, `box.cfg`; `box.cfg` is active.
- Optimized macros: `installer/klipper/tltg-optimized-macros/`.
- Stock/optimized pairs: start/end `start_end.cfg`; homing `kinematics.cfg`; filament `filament.cfg`; adaptive mesh `bed_mesh.cfg` plus optimized wrappers.
- Cooling: `installer/klipper/tltg-optimized-macros/cooling.cfg`; pause/resume/cancel: `config/klipper-macros-qd/pause_resume_cancel.cfg`.
- `config/KAMP/*.cfg` exists but is not the active adaptive-mesh path.

## Package versions and changelog

- Changes to installed macros, installer behavior, or release-bundle contents must advance the package version.
- Run `python3 scripts/bump_installer_version.py <version>` so `installer/package.yaml`, `installer/supported_upgrade_sources.yaml`, and `installer/klipper/tltg-optimized-macros/globals.cfg` stay aligned; do not update those version authorities independently.
- Every new package version must have a matching `CHANGELOG.md` section in the same change. Include every operator-visible change shipped under that version, including changes merged since the previous changelog version.
- Before finalizing, compare recent versions in `installer/package.yaml` with `CHANGELOG.md` headings and run `python3 scripts/check_installer_known_versions.py`; missing or inconsistent release notes block completion.

## Validation

- Optimized macro `.cfg`: `python3 scripts/format_klipper_configs.py`
- `installer/package.yaml` or `installer/supported_upgrade_sources.yaml`: `python3 scripts/check_installer_known_versions.py`
- Slicer G-code: `python3 scripts/check_optimized_slicer_macros.py`
- Installer behavior: `python3 scripts/run_installer_core_tests.py`
- Launcher, bundle, or release plumbing: `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test`
- OpenSpec: `openspec validate --all --strict`
- Start-print behavior or contract paths: follow **Start-print changes**.

## Tests

- Keep tests minimal: represent durable behavior, safety boundaries, and release contracts rather than implementation detail.
- Extend existing lifecycle, contract, matrix, or smoke coverage before adding tests; prefer complete end-to-end scenarios and parameterized firmware/source matrices.
- Do not test private helpers, call order, constants, exact status wording, standard-library behavior, or synthetic examples unless enforcing a security boundary. Run executable checkers directly; do not wrap them.
- Put static invariants in contracts or standalone checkers. Remove or merge superseded and overlapping tests with behavior changes.
- Add a test module only for a distinct boundary; net growth requires an unrepresented operator-visible behavior or safety invariant.
- Keep `python3 scripts/run_installer_core_tests.py` below five seconds on a typical development machine; bundle smoke time is excluded.

## Timing and terminology

- `G4 P...` is fixed dead time; `M400` waits only for queued motion. For conservative speedups, trim `G4` before changing motion speeds or acceleration.
- Stock timing/behavior globals remain in `config/klipper-macros-qd/globals.cfg`; optimized-only globals belong in `installer/klipper/tltg-optimized-macros/globals.cfg`. Treat apparently unused stock globals as externally consumed unless proven otherwise.
- Use “purge” only for rear waste-chute/wiper extrusion; use “prime line” for front-of-bed slicer-start extrusion.
