---
name: qidi-firmware-release-update
description: Investigate and implement QIDI Max 4 optimized-config updates after QIDI publishes a new firmware/default-config release. Use when a new QIDI Max 4 firmware tag, defaults commit, or Qidi-Max4-Defaults update needs review, OpenSpec planning, installer support, stock snapshot updates, validation, or optional printer install.
license: MIT
compatibility: Qidi-Max-4-Optimized repo with sibling Qidi-Max4-Defaults checkout and OpenSpec CLI.
---

# QIDI Firmware Release Update

Use this skill when QIDI publishes a new Max 4 firmware/default-config release and the optimized installer/configs need to be investigated or updated.

## Non-negotiable project rules

- Never modify `config/fluidd.cfg`.
- Never add unredacted hardware identifiers. Treat `config/MCU_ID.cfg`, `config/box.cfg`, and copied printer-specific files as sensitive.
- Do not copy `MCU_ID.cfg`, `box.cfg`, `fluidd.cfg`, or `saved_variables.cfg` into bundled stock snapshots.
- Keep `config/` stock-mapped: stock baseline changes belong in guarded `installer/package.yaml` patches or firmware-specific stock snapshots unless a direct `config/` edit is explicitly approved.
- Preserve existing optimized behavior unless the firmware release requires a compatibility change or the user explicitly approves a behavior change.
- Keep OrcaSlicer and QIDI Studio packs aligned if slicer G-code changes are required; do not add QIDI Studio polar cooler controls unless explicitly requested.
- Do not install on the printer unless the user explicitly asks.
- Before any printer contact, follow the `qidi-max4-printer` skill active-print guard exactly.

## Initial investigation

1. Confirm working tree and branch.

   ```bash
   git status --short
   git branch --show-current
   ```

2. Update the sibling defaults repo. It is expected one level up.

   ```bash
   git -C ../Qidi-Max4-Defaults status --short
   git -C ../Qidi-Max4-Defaults pull --ff-only
   git -C ../Qidi-Max4-Defaults log --oneline -5
   git -C ../Qidi-Max4-Defaults tag --points-at HEAD
   ```

3. Identify the previous and new firmware/defaults baseline.

   ```bash
   git -C ../Qidi-Max4-Defaults diff --stat <previous>..<new>
   git -C ../Qidi-Max4-Defaults diff --name-only <previous>..<new>
   git -C ../Qidi-Max4-Defaults diff --find-renames --find-copies --minimal --unified=80 <previous>..<new> -- config
   ```

4. Summarize before changing anything unless the user already asked to implement.

   Include:
   - changed files and firmware/tag/commit identifiers
   - stock behavior changes by area: motion/closed-loop, homing, pause/resume/cancel, idle, QIDI Box/runout, thermals, filament/material metadata, slicer entrypoints, installer implications
   - whether changes look firmware-coupled or cosmetic
   - risks for current optimized behavior
   - whether installer support needs firmware-specific variants or stock snapshots

## Branch and OpenSpec workflow

When implementation is requested:

1. Create a branch from latest `origin/dev` unless the user says otherwise.

   ```bash
   git fetch origin dev
   git switch -c support-qidi-firmware-<version-token> origin/dev
   ```

2. Create or update an OpenSpec change before broad implementation.

   Recommended change name format:

   ```text
   support-qidi-firmware-<major-minor-patch-build>
   ```

   Example for `01.01.06.04`:

   ```bash
   openspec new change "support-qidi-firmware-01-01-06-04"
   openspec status --change "support-qidi-firmware-01-01-06-04" --json
   ```

3. Proposal/design/spec/tasks should cover:
   - supported firmware matrix: old baseline(s) plus new firmware
   - firmware-specific guarded patch behavior
   - firmware-specific stock snapshot restoration
   - stock config deltas from `../Qidi-Max4-Defaults`
   - optimized behavior decisions, including any explicit user decisions
   - tests and docs required

4. During implementation, mark tasks complete as they are actually completed.

## Implementation pattern

### Firmware support and guarded patches

- Add the new firmware to `installer/package.yaml firmware.supported`.
- Existing optimized patch targets that apply to both firmware baselines usually need variants for each supported firmware.
- New firmware-only stock options must be active only for firmware versions that ship those options. Do not require new vendor options on older firmware.
- If a new firmware introduces options absent from older firmware, ensure runtime preflight/install applies only active patch entries for the detected firmware.
- Keep desired optimized homing/speed values unchanged unless the user explicitly decides otherwise.
- Treat `:` vs `=` delimiter changes as non-semantic unless evidence shows otherwise; the installer parses both.

### Stock snapshots

- Use firmware-specific stock snapshot roots:

  ```text
  installer/stock/qidi-max4-defaults/firmwares/<firmware>/config/
  ```

- Preserve prior supported firmware snapshots.
- Add the new firmware snapshot from `../Qidi-Max4-Defaults/config/` while excluding:

  ```text
  MCU_ID.cfg
  box.cfg
  fluidd.cfg
  saved_variables.cfg
  ```

- Redact email addresses or private identifiers in copied comment-only stock snapshot content when necessary.
- Update `installer/runtime/legacy_manual_install.py` so legacy reset selects the snapshot using detected firmware.
- Update `scripts/smoke_test_installer_bundle.py` if bundle snapshot paths change.

### Runtime behavior changes

- Review active stock macros before carrying vendor behavior into optimized macros.
- If QIDI changes cancel ordering, compare `CANCEL_PRINT` and `OPTIMIZED_CANCEL_PRINT_ON_ERROR`; preserve optimized no-park/no-toolhead-move semantics unless the user approves movement.
- If QIDI changes filament names, update `openspec/observations/qidi-box/material-metadata.md` and tests that assert official material metadata; do not rename unrelated stock tables such as `drying.conf` unless QIDI changed them upstream.
- If QIDI changes start-print behavior, follow the start-print path contract in `AGENTS.md` before editing start-path sources.

### Version metadata

- When the patch target set or installer behavior changes, bump package metadata with:

  ```bash
  python3 scripts/bump_installer_version.py <new-version>
  ```

- Then run:

  ```bash
  python3 scripts/check_installer_known_versions.py
  ```

## Tests to add or update

Add focused tests for:

- install accepts the new firmware
- old firmware still installs without requiring new firmware-only options
- new firmware stock-drift patches classify/apply correctly
- legacy manual reset selects the old firmware snapshot
- legacy manual reset selects the new firmware snapshot
- missing firmware snapshot fails before stock files are overwritten
- optimized cancel/error semantics if cancel ordering changed
- bundle smoke test checks all required snapshot roots

Useful files:

```text
installer/tests/integration/test_install_flow.py
scripts/smoke_test_installer_bundle.py
installer/tests/fixtures/runtime/base/
```

## OpenSpec content to update

Update the applicable authorities when behavior, installer flow, snapshot layout, or external assumptions change:

```text
openspec/specs/installer-lifecycle/spec.md
openspec/specs/optimized-printer-behavior/spec.md
openspec/contracts/gcode-paths/*                 # only for start-path changes
openspec/observations/qidi-platform.md
openspec/observations/qidi-box/*.md              # only for QIDI Box/material evidence
```

Specification and observation writing rules in `AGENTS.md` apply: state current behavior directly, preserve evidence qualifiers, include paths/commands where useful, and avoid process narration.

## Required validation

Run the relevant subset, then prefer the full installer validation before commit:

```bash
python3 scripts/format_klipper_configs.py
python3 scripts/check_installer_known_versions.py
python3 scripts/run_installer_core_tests.py
python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test
openspec validate <change-name>
openspec validate --specs
git diff --check
```

If start-print behavior changed, also run:

```bash
python3 scripts/check_gcode_paths.py --write
python3 scripts/check_gcode_paths.py
```

If slicer G-code changed, run:

```bash
python3 scripts/check_optimized_slicer_macros.py
```

## Verification and archive

Before archiving:

1. Verify all OpenSpec tasks are complete.
2. Map each spec requirement to implementation evidence from files/tests/commands.
3. Sync delta specs into `openspec/specs/`.
4. Archive the change.

Typical commands:

```bash
openspec status --change <change> --json
openspec validate <change>
openspec archive <change> --yes
```

If specs were manually synced and archive reports already-existing requirements, use:

```bash
openspec archive <change> --yes --skip-specs
```

## Commit checklist

Before committing:

```bash
git status --short --untracked-files=all
git diff --name-only | sort
git diff --check
rg -n "([A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}|/dev/serial/by-id|usb-Klipper|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|/Users/|/home/<local-user>" <changed-paths> -S
```

Redact newly introduced real email addresses in copied stock snapshots unless the user explicitly approves keeping upstream copyright email addresses. Do not churn already-tracked email addresses unless they are in files being recopied or rewritten for the firmware snapshot.

Commit with a direct message such as:

```bash
git commit -m "Support QIDI Max 4 firmware <version>"
```

Do not push unless explicitly instructed.

## Optional printer install

Only if the user asks to install on the printer:

1. Load and follow `qidi-max4-printer`.
2. First printer contact must be:

   ```bash
   curl -fsS 'http://192.168.20.165/printer/objects/query?print_stats'
   ```

3. Stop if state is `printing`, `paused`, unknown, or cannot be determined.
4. Build bundle locally:

   ```bash
   python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test
   ```

5. Transfer and extract to `/home/qidi/tltg-optimized-macros`.
6. Run a remote dry-run first:

   ```bash
   ssh qidi@192.168.20.165 '/home/qidi/tltg-optimized-macros/install.sh --dry-run --plain'
   ```

7. For real install, avoid `--yes` if the user specified choices such as no auto-updates. Use explicit stdin responses or interactive input. To skip OS hardening:

   ```bash
   ssh qidi@192.168.20.165 "cd /home/qidi/tltg-optimized-macros && printf 'yes\nyes\n' | ./install.sh --plain --skip-system-optimizations"
   ```

8. If auto-update units already exist, the installer may repair them. If the user requested no automatic updates, disable them after install:

   ```bash
   ssh qidi@192.168.20.165 'cd /home/qidi/tltg-optimized-macros && ./auto-update.sh --disable-systemd'
   ```

9. Verify final printer state:

   ```bash
   curl -fsS 'http://192.168.20.165/printer/objects/query?print_stats'
   ssh qidi@192.168.20.165 'systemctl is-active klipper moonraker --no-pager'
   ssh qidi@192.168.20.165 'test ! -e /etc/systemd/system/tltg-optimized-auto-update.timer && test ! -e /etc/systemd/system/tltg-optimized-auto-update.service && echo auto_update=absent'
   ```

10. Verify installed state file package version, detected firmware, and key runtime options changed by the release.
