## 1. Manifest and evidence model

- [x] 1.1 Add the Rockchip unit, script, drop-in, desired content, mount target, and complete defective structural signature to `installer/package.yaml` without firmware-version gating.
- [x] 1.2 Extend `installer/runtime/models.py` and `installer/runtime/manifest.py` with strict absolute-path, structural-marker, and drop-in-content validation for the Rockchip operation.
- [x] 1.3 Add `/home/qidi/printer_data/.tltg_optimized_host_reboot_required` to runtime paths and define a strict versioned marker model containing reason, operation ID, package version, creation time, and boot ID.
- [x] 1.4 Update `openspec/observations/qidi-platform.md` with the RK3308-to-RK3208 failure sequence, first-boot flag behavior, live reboot result, and controlled benchmark table from `design.md`.

## 2. Guarded Rockchip operation

- [x] 2.1 Add classification for the complete defective stock structure, exact desired unowned state, prior installer-owned state, missing or corrected vendor structure, conflicting content, and symlinked paths.
- [x] 2.2 Capture the drop-in bytes/type/mode/ownership, effective unit state, and exact root `sync` mount token without treating `async` as `sync`.
- [x] 2.3 Atomically install the declared drop-in, reload systemd, reset/start the no-op unit, remount `/` with `rw,async`, and verify desired bytes, effective `/bin/true`, exit status `0`, and mount options.
- [x] 2.4 Create the host-reboot marker only after an installer-owned Rockchip apply or reconciliation succeeds; exact desired unowned state must remain outside installer file ownership.
- [x] 2.5 Integrate the operation with selected operation IDs, desired-state reporting, dry-run output, action records, and auto-update reconciliation under enabled system-optimization policy.

## 3. Recovery and uninstall

- [x] 3.1 Extend the system journal rollback path to restore the captured drop-in, reload systemd, restore the prior exact root mount mode, and remove a marker created by the failed transaction.
- [x] 3.2 Retain the first installer-owned Rockchip preimage across reinstalls and avoid claiming an exact desired drop-in that existed without valid ledger ownership.
- [x] 3.3 Restore installer-owned Rockchip state only when the live drop-in still equals desired content; preserve and report later operator drift, and honor `--keep-system-optimizations`.

## 4. Host reboot and auto-update orchestration

- [x] 4.1 Add a host-reboot runtime helper that atomically validates markers, reads `/proc/sys/kernel/random/boot_id`, verifies post-boot Rockchip state, and clears only completed markers.
- [x] 4.2 Add an immediate fail-closed Moonraker idle-state check and authenticated delayed transient-systemd reboot scheduler that retains the marker on unsafe state or scheduling failure.
- [x] 4.3 Add interactive reboot prompting plus `--reboot-host` parsing, help text, release-wrapper forwarding, validation, dry-run reporting, and direct `--yes` behavior that does not reboot without the explicit flag.
- [x] 4.4 Invoke reboot handling only after install or uninstall state, success output, auto-update setup, and required Klipper activation have completed.
- [x] 4.5 Ensure changed-release auto-update children apply enabled Rockchip system optimization and write checksum-bound markers without rebooting; arm a delayed parent follow-up for the first rollout and schedule only after child success and durable checksum advancement.
- [x] 4.6 Ensure already-current auto-update reconciles enabled Rockchip state, retries same-boot pending reboots while idle, and verifies or clears earlier-boot markers before release processing.
- [x] 4.7 Preserve disabled or absent system-optimization policy during auto-update so it neither applies the repair nor creates or schedules a reboot.

## 5. Automated validation

- [x] 5.1 Add manifest parser tests for complete and incomplete structural signatures, malformed content or paths, duplicate markers, and missing required Rockchip fields.
- [x] 5.2 Add fake-root unit tests for structurally defective stock across changed file hashes, already-desired unowned state, prior-owned reconciliation, corrected or missing vendor structure, conflicting drop-in, symlink rejection, and exact mount-token parsing.
- [x] 5.3 Add failure-injection tests for atomic write, daemon reload, unit start, remount, and postflight failures, including verified rollback of file, mount, and reboot-marker preimages.
- [x] 5.4 Add host-reboot tests for malformed markers, current and changed boot IDs, post-boot verification, interactive acceptance/decline, explicit CLI request, active/unknown printer state, delayed scheduling, and scheduler failure.
- [x] 5.5 Add changed-release and already-current auto-update tests proving enabled-policy repair, child no-reboot behavior, checksum-before-reboot ordering, pending retry, completed-marker cleanup, and disabled-policy preservation.
- [x] 5.6 Add uninstall and integration coverage for accepted restoration, keep policy, operator drift, vendor replacement, dry-run non-mutation, and isolation from a successful Klipper configuration install.

## 6. Completion checks

- [x] 6.1 Run `python3 scripts/run_installer_core_tests.py`.
- [x] 6.2 Run `python3 scripts/build_installer_bundle.py --output-dir dist --channel dev --build-id local --smoke-test`.
- [x] 6.3 Run `openspec validate --all --strict` and resolve every warning or error.
- [x] 6.4 On an idle test printer, validate direct and auto-update marker handling, delayed reboot, post-boot cleanup, async mount persistence, and qidiclient, Klipper, and Moonraker health.
