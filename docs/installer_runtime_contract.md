# Installer_Runtime_Contract

Installer metadata used by the runtime:
- `installer/package.yaml package.version` is the bundle package version.
- `installer/package.yaml package.known_versions` contains the installed package versions accepted from `config/tltg_optimized_state.yaml` during install and must exactly match the supported prior-version keys in `installer/supported_upgrade_sources.yaml`.
- `installer/package.yaml firmware.supported` contains install-supported firmware versions `01.01.06.03` and `01.01.06.04`.
- `installer/package.yaml backup.source_directory` is `config`.
- `installer/package.yaml backup.label_prefix` is the install backup label prefix.
- `installer/package.yaml state.record_file` is `config/tltg_optimized_state.yaml`.
- `installer/package.yaml preflight.required_files`, `preflight.required_sections`, and `preflight.required_lines` define aggregated install preflight targets.
- `config/box.cfg` and `[box_extras]` are not install preflight targets; `OPTIMIZED_START_PRINT_FILAMENT_PREP` branches around the QIDI Box stack when `printer["box_extras"]` is not defined or `enable_box != 1`.
- During interactive install, when `config/box.cfg` contains `[box_extras]`, `config/saved_variables.cfg [Variables] box_count` is greater than `0`, and `[Variables] enable_box` is `0`, the runtime prompts to set `enable_box = 1`; this saved-variable edit is not recorded in `config/tltg_optimized_state.yaml` and uninstall does not revert it.
- During install, when `config/saved_variables.cfg [Variables] box_count` requires `value_tN` entries that are missing or empty, the runtime writes each required `value_tN = 'slotN'` without prompting; this saved-variable edit is not recorded in `config/tltg_optimized_state.yaml` and uninstall does not revert it.
- During interactive install, when existing non-empty `config/saved_variables.cfg [Variables] value_tN` entries do not equal `'slotN'`, the runtime prompts to rewrite each mismatched `value_tN` to `'slotN'`; this saved-variable edit is not recorded in `config/tltg_optimized_state.yaml` and uninstall does not revert it.
- `installer/package.yaml install.ensure_directories`, `install.managed_tree`, and `install.ensure_lines` define the installer-managed runtime changes under `config/`.
- `installer/package.yaml patches.set_options[]` defines guarded `file + section + option` runtime value patches.
- `installer/package.yaml patches.delete_sections[]` defines guarded `file + section` runtime section deletion patches; each variant stores `expected_normalized_sha256` for the normalized section text that must be present before deletion.
- `installer/package.yaml install.managed_tree.required_files[]` lists required files under `installer/klipper/tltg-optimized-macros/`; install preflight fails when the managed-tree source is missing, empty, symlinked, or missing any required file.
- `installer/package.yaml install.managed_tree` defines the managed-tree file set validated during install postflight.
- Install postflight compares each runtime `config/tltg-optimized-macros/*` file SHA-256 against the bundle source SHA-256 before writing `config/tltg_optimized_state.yaml`.
- `installer/package.yaml postflight.verify_lines` defines additional final install line-verification targets.
- `installer/supported_upgrade_sources.yaml` defines the supported prior installed package versions and the exact uninstall-allowed guarded patch target tuples (`file + section + option`) for each version.
- `python3 scripts/bump_installer_version.py <version>` updates `installer/package.yaml package.version`, appends `<version>` to `package.known_versions`, appends the `<version>` uninstall tuple set to `installer/supported_upgrade_sources.yaml`, and updates `installer/klipper/tltg-optimized-macros/globals.cfg variable_package_version`.

State-file contract:
- Public installer bundles write `config/tltg_optimized_state.yaml schema_version: 1`.
- `config/tltg_optimized_state.yaml system_ledger` is optional and stores installer-managed OS hardening policy, action records, and restore preimages for paths outside `config/`.
- `system_ledger.policy.system_optimizations` stores `enabled` or `disabled`; `system_ledger.policy.ai_detection` stores `disable`, `keep_enabled`, or `unset`.
- `system_ledger.restore_preimages` preserves the first preimage captured before the installer changed `/home/qidi/moonraker/moonraker/components/file_manager/metadata.py`, DNS, APT sources, systemd services, or qidiclient GIF files.
- Auto-update applies or reapplies the Moonraker 3MF metadata patch whenever system reconciliation runs; DNS, APT, service, qidiclient GIF, and AI-service reconciliation require `system_ledger.policy.system_optimizations = enabled`; missing `system_ledger.policy` skips system reconciliation.
- `config/tltg_optimized_state.yaml` stores package identity, install timestamp, detected install firmware, backup label, managed-tree file hashes, and the guarded patch ledger used for uninstall.
- `config/tltg_optimized_state.yaml` and `/home/qidi/printer_data/.tltg_optimized_recovery_required` are written with mode `0600`; existing broader file modes are replaced.
- Public installer bundles accept only schema-valid installed-state ledgers compatible with `schema_version: 1`.
- Pre-ledger dev installs that do not provide the managed-tree file hashes and guarded patch ledger are unsupported inputs for public uninstall bundles.
- External tamper detection is not yet implemented, so uninstall trusts a schema-valid local installed-state ledger subject to tuple allowlists and live-value checks.
- Uninstall validates that every ledger patch target matches an explicit uninstall-allowed `file + section + option` tuple for the stored installed package version from `installer/supported_upgrade_sources.yaml`.
- Newer public bundles may uninstall older supported installs only when that stored installed package version and its allowed tuple set are present in `installer/supported_upgrade_sources.yaml`.

Managed-tree drift semantics:
- Install/reinstall compares current `config/tltg-optimized-macros/` file hashes against the existing `config/tltg_optimized_state.yaml managed_tree.files[]` hashes to detect local drift.
- Install/reinstall compares the same existing `managed_tree.files[]` hashes against the new bundle contents to detect expected bundle-version changes.
- Differences between the prior installed-state ledger and the new bundle contents are expected bundle changes by themselves and are not reported as local managed-tree drift.

Patch manifest semantics:
- Manifest validation rejects any `installer/package.yaml patches.set_options[]` or `patches.delete_sections[]` block unless it provides at least one `variants[]` firmware entry, references only values from `installer/package.yaml firmware.supported[]`, and defines no more than one variant for the same firmware.
- Existing optimized patch targets provide variants for both `01.01.06.03` and `01.01.06.04`; `.04`-only stock-baseline targets such as closed-loop `trigger_*` options define only `01.01.06.04` variants.
- Install preflight validates patch targets active for the detected firmware; `.04`-only patch targets are not required on `01.01.06.03` configs.
- Variant selection changes `expected`/`desired` values for option patches and `expected_normalized_sha256` for section deletion patches.

Install statuses and terminal messages:
- Status `checking firmware version` covers reading `/home/qidi/update/firmware_manifest.json`, parsing `SOC.version`, and comparing it against `installer/package.yaml firmware.supported`.
- Failure during install `checking firmware version` returns `Could not detect firmware version.` when `/home/qidi/update/firmware_manifest.json` is missing, corrupt, or does not contain a valid `SOC.version`, and stops before `config/tltg_optimized_state.yaml`, backup creation, or any write under `config/`.
- Failure during install `checking firmware version` returns `Your firmware version is not supported.` when the detected firmware is not present in `installer/package.yaml firmware.supported`, and stops before `config/tltg_optimized_state.yaml`, backup creation, or any write under `config/`.
- Status `checking package version` covers reading and validating `config/tltg_optimized_state.yaml` when that file already exists.
- Missing `config/tltg_optimized_state.yaml` is a fresh-install path and does not block the run.
- Failure during `checking package version` returns `Could not validate previous package version.` when `config/tltg_optimized_state.yaml` is unreadable, malformed, schema-incompatible, missing required install-state fields, or stores a package version not present in `installer/package.yaml package.known_versions`.
- Status `performing preflight checks` covers runtime symlink rejection for managed paths, the local Moonraker printer-state query, managed-tree source validation, `installer/package.yaml preflight.required_files`, `preflight.required_sections`, `preflight.required_lines`, every unique patch target from `patches.set_options[]` and `patches.delete_sections[]`, and free-space checks.
- Failure during `performing preflight checks` returns `Cannot continue because you're missing these things.` and prints the full target report grouped by files, sections, lines, and patch targets; patch-target entries may be missing or ambiguous.
- Failure during `performing preflight checks` returns `Could not determine printer state.` when local Moonraker `print_stats.state` data is unavailable, malformed, or inconsistent.
- Failure during `performing preflight checks` returns `Cannot continue while a print is active or paused.` when local Moonraker `print_stats.state` is `printing` or `paused`.
- Failure during `performing preflight checks` returns `There is not enough free space to continue.` when available free space is less than the reserved total for zip backup bytes, rollback preimages, new/rewritten files, same-directory atomic temp files, and a safety margin of `max(64 MiB, 20% of the subtotal)`.
- The Moonraker printer-state query runs before legacy manual-copy detection; an active or paused print stops before any legacy rollback prompt or config write.
- When no installer state file exists and legacy manually-copied optimized config markers are found in `config/printer.cfg`, `config/klipper-macros-qd/filament.cfg`, `config/klipper-macros-qd/start_end.cfg`, `config/klipper-macros-qd/kinematics.cfg`, `config/klipper-macros-qd/globals.cfg`, or `config/tltg-optimized-macros/`, interactive install prompts before normal install confirmation.
- Accepted legacy rollback creates a `tltg-optimized-macros-before-optimize-legacy-manual-reset-...zip` backup of `config/`, restores the bundled QIDI stock snapshot from `installer/stock/qidi-max4-defaults/firmwares/<detected-firmware>/config/`, removes `config/tltg-optimized-macros/`, preserves `config/MCU_ID.cfg`, `config/box.cfg`, `config/fluidd.cfg`, `config/saved_variables.cfg`, and any direct `config/KAMP` symlink, restarts `qidi-client.service`, then continues with normal install preflight and installation.
- Declined legacy rollback prints `Legacy config rollback declined. Installation cancelled.` and exits zero before backup creation or any write under `config/`.
- Interactive yes/no prompts backed by `installer/runtime/interaction.py` strip surrounding whitespace, uppercase the response, accept `Y` or `YES`, decline `N` or `NO`, and re-render the same prompt for any other input including blank input.
- After install preflight succeeds and before backup creation when `--dry-run` is not active, the runtime prompts `Would you like us to take a backup of your configs and proceed with installation?`.
- Install confirmation input normalized to `N` or `NO` returns `Installation cancelled.` and exits zero before backup creation or any write under `config/`.
- Status `creating backup` covers backup label creation and `.zip` archive creation for `/home/qidi/printer_data/config`; backup creation fails when `config/` is missing, not a directory, empty, or contains symlinks other than direct `config/KAMP` and `config/fluidd.cfg` symlinks.
- After a successful new installer backup is created during `install`, the runtime prunes installer-created backup zip files under `/home/qidi/printer_data/` to retain only the newest three archives across install and uninstall backup prefixes; unrelated `.zip` files are ignored and `--dry-run` never prunes.
- Status `installing` starts before the first runtime write under `config/` and stays active through optional `config/saved_variables.cfg [Variables] enable_box = 1` and `value_tN = 'slotN'` prompt/writes, drift detection, `install.managed_tree`, `patches.set_options[]`, `patches.delete_sections[]`, `install.ensure_lines`, managed-tree postflight verification, `postflight.verify_lines`, and the final state-file write.
- Final install success returns `Installed.` after `config/tltg_optimized_state.yaml` is written.
- Final install success output lists only the `patches.set_options[]` targets skipped as user-modified.
- Final install success output lists `Managed tree drift overwritten:` only when existing `config/tltg-optimized-macros/` files differed from the prior installed-state ledger before mirror mode overwrote or removed them.
- Final install success output does not list `patches.set_options[]` targets that already matched `desired` before the run.
- Before managed-tree writes during `installing`, install prompts `We see {box_count} QIDI Box unit(s) recorded in saved_variables.cfg, but enable_box is 0. Would you like to enable QIDI Box support now?` when `[box_extras]` exists, `box_count > 0`, `enable_box == 0`, and input is interactive.
- Accepted QIDI Box enablement writes `enable_box = 1` in `config/saved_variables.cfg [Variables]`, prints `QIDI Box support enabled in saved_variables.cfg.`, and leaves the change out of the optimized install-state ledger.
- QIDI Box enablement input normalized to `N` or `NO` prints `QIDI Box support left disabled.` and preserves the existing saved variable.
- Before managed-tree writes during `installing`, install writes missing or empty `value_t0` through `value_t<min(box_count * 4, 16) - 1>` entries to `slotN`, prints `Added {count} missing QIDI Box tool-slot mapping(s) to saved_variables.cfg.`, and leaves the change out of the optimized install-state ledger.
- Before managed-tree writes during `installing`, install prints each mismatched `value_tN: <current> -> slotN` and prompts `Tool numbers do not line up with slot numbers. Would you like us to correct this?` when any existing non-empty `config/saved_variables.cfg [Variables] value_tN` does not resolve to `slotN` after stripping surrounding string quotes.
- Accepted tool-slot correction writes each mismatched `value_tN = 'slotN'` in `config/saved_variables.cfg [Variables]`, prints `Tool-slot mappings corrected in saved_variables.cfg.`, and leaves the change out of the optimized install-state ledger.
- Tool-slot correction input normalized to `N` or `NO` prints `Tool-slot mappings left unchanged.` and preserves the existing saved variables.
- After install state-file write and before final install success output, interactive install prompts `Would you like to apply system hardening and OS optimizations?` when `installer/package.yaml system_optimizations` is present.
- Interactive reinstall prompts again even when `config/tltg_optimized_state.yaml system_ledger.policy.system_optimizations` already stores `enabled` or `disabled`; noninteractive install and auto-update reuse the stored policy.
- Install applies the Moonraker 3MF metadata patch to `/home/qidi/moonraker/moonraker/components/file_manager/metadata.py` before optional OS hardening; `.gcode.3mf` extraction reads `Metadata/slice_info.config`, uses `<metadata key="index" value="N"/>` for `Metadata/plate_N.gcode`, `Metadata/plate_N.json`, and `generate_thumb_path(..., N)`, falls back to plate 1 when the index is missing or invalid, records the original file in `system_ledger.restore_preimages`, and restarts `moonraker.service` after a patch or restore.
- Accepted system optimization confirmation writes or reconciles DHCP-first DNS with `1.1.1.1` and `8.8.8.8` fallbacks, Debian Bullseye APT sources, qidiclient static GIFs, disabled `xl2tpd`, disabled `bluetooth`, and optional `algo_app.service` disablement.
- Service operations record `systemctl is-enabled` and `systemctl is-active` state before changing units; missing units are recorded with action status `missing` and are skipped without a restore preimage.
- Service disablement runs `systemctl disable --now <service>`, then an explicit `systemctl stop <service>`; SysV-style service names without a dot also run `/etc/init.d/<service> stop` as a quiet fallback before postflight checks.
- When AI detection is kept enabled, `algo_app.service` is recorded with action status `skipped_by_policy` and no restore preimage.
- The qidiclient static GIF operation requires `/home/qidi/QIDI_Client/access` to exist, backs up replaced files under `/home/qidi/QIDI_Client/access/.gif-backup-<timestamp>`, preserves existing replaced-file owner and mode, restarts `qidi-client.service` when present, and verifies installed GIF hashes against `installer/system/qidiclient-static-gifs.tar.gz`.
- Declined system optimization confirmation writes `system_ledger.policy.system_optimizations = disabled` and skips DNS, APT, qidiclient GIF, VPN, Bluetooth, and AI backend changes; the Moonraker 3MF metadata patch still applies when `/home/qidi/moonraker/moonraker/components/file_manager/metadata.py` matches the supported QIDI extraction shape.
- When system optimizations are accepted, install prompts `Would you like us to disable QIDI AI detection features?`; accepted confirmation disables the backend service and prints touchscreen guidance for `Settings -> Printing Options -> Spaghetti Detection` and `Foreign Object Detection`, and input normalized to `N` or `NO` preserves `algo_app.service`.
- `install.sh --skip-system-optimizations` records disabled system optimization policy and skips OS hardening.
- `install.sh --disable-ai-detection` records `system_ledger.policy.ai_detection = disable`; `install.sh --keep-ai-detection` records `system_ledger.policy.ai_detection = keep_enabled`.
- System optimization failures print `System optimizations were not fully applied. Klipper config installation remains installed.` and leave the verified Klipper config install in place.
- After final install success output, install repairs an existing `/etc/systemd/system/tltg-optimized-auto-update.service` or `/etc/systemd/system/tltg-optimized-auto-update.timer` before restart prompting; when neither unit exists, install prompts `Would you like to enable hourly automatic updates for the TLTG configs?` when input is interactive.
- Auto-update repair attempts to fetch the latest release `.sha256`; when the fetch succeeds it writes `config/tltg_optimized_auto_update_state.json`, and when the fetch fails the existing state is left unchanged.
- Auto-update repair runs quiet sudo commands through `sudo -S -p ''` using `TLTG_OPTIMIZED_SUDO_PASSWORD` when set, otherwise QIDI's public default password `qiditech`; if the initial `sudo -v` attempt fails and input is interactive, it prints `Initial sudo authentication failed.`, prompts `Enter sudo password to continue:`, retries `sudo -v` with that password, then uses that password for the remaining sudo commands.
- Successful auto-update repair installs `/etc/systemd/system/tltg-optimized-auto-update.service` and `/etc/systemd/system/tltg-optimized-auto-update.timer`, runs `systemctl daemon-reload`, runs `systemctl enable tltg-optimized-auto-update.timer`, runs `systemctl restart tltg-optimized-auto-update.timer`, and prints `Auto-updates repaired.`.
- Auto-update repair failure prints `Could not repair auto-updates. <reason>` and does not change the successful install result.
- Accepted auto-update confirmation attempts to fetch the latest release `.sha256`; when the fetch succeeds it writes `config/tltg_optimized_auto_update_state.json`, and when the fetch fails the failure is silent because the first successful timer run initializes auto-update state without installing.
- Accepted auto-update confirmation runs quiet sudo commands through `sudo -S -p ''` using `TLTG_OPTIMIZED_SUDO_PASSWORD` when set, otherwise QIDI's public default password `qiditech`; if the initial `sudo -v` attempt fails and input is interactive, it prints `Initial sudo authentication failed.`, prompts `Enter sudo password to continue:`, retries `sudo -v` with that password, then uses that password for the remaining sudo commands.
- Accepted auto-update confirmation installs `/etc/systemd/system/tltg-optimized-auto-update.service` and `/etc/systemd/system/tltg-optimized-auto-update.timer`, runs `systemctl daemon-reload`, runs `systemctl enable tltg-optimized-auto-update.timer`, and runs `systemctl restart tltg-optimized-auto-update.timer` through sudo.
- The installed auto-update timer runs `~/tltg-optimized-macros/auto-update.sh --run` five minutes after boot and then one hour after each active run.
- The installed auto-update service unsets `TLTG_AUTO_UPDATE_ARCHIVE_URL`, `TLTG_AUTO_UPDATE_CHECKSUM_URL`, `TLTG_INSTALLER_ARCHIVE_URL`, and `TLTG_INSTALLER_CHECKSUM_URL` before running `~/tltg-optimized-macros/auto-update.sh --run`.
- Each `auto-update-check` run first checks Moonraker print state; when Klipper is reachable and the printer is idle, it compares `config/saved_variables.cfg [Variables] box_count` against `config/tltg_optimized_runtime_state.json last_observed_box_count`, writes missing or empty required `value_tN = 'slotN'` entries after a changed count, and updates the observed count. If the printer is busy or state is unavailable, the QIDI Box tool-slot reconcile is skipped without writing.
- Auto-update systemd setup failure prints `Could not enable auto-updates. <reason>` and does not change the successful install result.
- After auto-update repair, after the auto-update prompt returns, or when auto-update prompting is skipped, the runtime prompts `Would you like me to restart Klipper to apply changes?`.
- Accepted restart confirmation triggers a local Moonraker `POST /printer/restart` request.
- Failed automatic restart prints `Could not restart Klipper automatically. Restart Klipper to apply changes.` and does not change the successful install result.
- Restart confirmation input normalized to `N` or `NO` returns without restarting and prints `Restart Klipper to apply changes.`.
- `install.sh --yes` suppresses interactive legacy rollback confirmation, install confirmation, restart confirmation, and auto-update setup prompts; preflight checks still run. Existing auto-update systemd units are still repaired without prompting. If legacy manually-copied optimized configs are detected, `--yes` backs up and restores stock-managed configs before install. If QIDI Box enablement or tool-slot correction is detected, `--yes` applies the saved-variable change without prompting.
- `install.sh -h` and `install.sh --help` print `QIDI Max 4 Optimized installer <package.version>` before usage text, where `<package.version>` is read from `installer/package.yaml package.version`.
- `install.sh -v` and `install.sh --version` print `QIDI Max 4 Optimized installer <package.version>` and exit without running install, uninstall, preflight, or runtime checks.

Auto-update runtime behavior:
- `auto-update.sh --run` calls installer mode `auto-update-check`.
- `auto-update-check` acquires `/home/qidi/printer_data/.tltg_optimized_installer.lock`, stops when `/home/qidi/printer_data/.tltg_optimized_recovery_required` exists, fetches the latest release checksum from GitHub, and compares it to `config/tltg_optimized_auto_update_state.json latest_checksum`.
- Checksum fetch failure prints `Auto-update skipped because the latest release checksum could not be fetched.`, exits zero, and does not run the installer.
- Matching checksums print `Auto-update check: already current.` and do not run the installer bundle replacement path.
- Matching checksums still reconcile the Moonraker 3MF metadata patch when system reconciliation runs; opted-in OS hardening is reconciled when `system_ledger.policy.system_optimizations = enabled`, the printer is idle, and the live OS state differs from the desired hardening state.
- Missing checksum state atomically writes the fetched checksum to `config/tltg_optimized_auto_update_state.json`, prints `Auto-update check: initialized latest release state.`, and does not run the installer.
- Changed checksums query local Moonraker `print_stats.state`; `printing` or `paused` prints `Auto-update skipped because a print is active or paused.` and does not run the installer.
- Unknown printer state prints `Auto-update skipped because printer state could not be determined.` and does not run the installer.
- Idle changed checksums fetch `tltg-optimized-macros.tar.gz`, verify the archive SHA-256 against the fetched checksum, validate that every tar member is under `tltg-optimized-macros/`, reject `..`, absolute paths, non-file/non-directory entries, and archives missing `tltg-optimized-macros/install.sh`, replace `~/tltg-optimized-macros` with the staged archive, and run `/bin/sh ~/tltg-optimized-macros/install.sh --yes --plain` while the parent `auto-update-check` process holds the installer lock.
- Successful auto-update atomically writes the new checksum to `config/tltg_optimized_auto_update_state.json` and prints `Auto-update complete.`.
- `auto-update.sh --enable-systemd` installs the systemd service/timer, runs `systemctl daemon-reload`, runs `systemctl enable tltg-optimized-auto-update.timer`, and runs `systemctl restart tltg-optimized-auto-update.timer` through sudo without running the main install flow; sudo uses `TLTG_OPTIMIZED_SUDO_PASSWORD` when set, otherwise QIDI's public default password `qiditech`, and prompts for a password when the initial sudo attempt fails and input is interactive.
- `auto-update.sh --disable-systemd` disables `tltg-optimized-auto-update.timer`, removes the service/timer unit files from `/etc/systemd/system/`, reloads systemd, and removes `config/tltg_optimized_auto_update_state.json`; sudo uses `TLTG_OPTIMIZED_SUDO_PASSWORD` when set, otherwise QIDI's public default password `qiditech`, and prompts for a password when the initial sudo attempt fails and input is interactive.

Uninstall statuses and terminal messages:
- Status `checking firmware version` covers best-effort current firmware detection from `/home/qidi/update/firmware_manifest.json` for audit/reporting during uninstall.
- Missing or corrupt current firmware metadata does not block uninstall when `config/tltg_optimized_state.yaml` contains a valid installed-state ledger.
- Status `checking installed package` covers checking install markers from state-file path presence, managed-tree presence, and the active include line, plus reading and validating `config/tltg_optimized_state.yaml` when it exists.
- Guarded patch targets are checked as additional install markers only after a valid installed-state ledger has been loaded.
- Failure during `checking installed package` returns `Could not validate installed package state.` when any non-patch installation marker remains but `config/tltg_optimized_state.yaml` is missing, corrupt, schema-incompatible, lacks the required uninstall ledger fields, or contains patch tuples outside the explicit allowed tuple set for the stored installed package version in `installer/supported_upgrade_sources.yaml`.
- Missing install markers returns `Nothing to uninstall.` and exits zero before backup creation or any write under `config/`.
- Status `performing uninstall preflight checks` covers runtime symlink rejection for state, include-line, and managed-tree paths, every patch target from the installed-state ledger, the local Moonraker printer-state query, and free-space checks.
- Failure during `performing uninstall preflight checks` returns `Could not determine printer state.` when local Moonraker `print_stats.state` data is unavailable, malformed, or inconsistent.
- Failure during `performing uninstall preflight checks` returns `Cannot continue while a print is active or paused.` when local Moonraker `print_stats.state` is `printing` or `paused`.
- Failure during `performing uninstall preflight checks` returns `There is not enough free space to continue.` when available free space is less than the reserved total for zip backup bytes, rollback preimages, new/rewritten files, same-directory atomic temp files, and a safety margin of `max(64 MiB, 20% of the subtotal)`.
- After uninstall preflight succeeds and before backup creation when `--dry-run` is not active, the runtime prompts `Are you sure you want to uninstall?`.
- Uninstall confirmation input normalized to `N` or `NO` returns `Uninstall cancelled.` and exits zero before backup creation or any write under `config/`.
- Status `creating backup` covers uninstall backup label creation and `.zip` archive creation for `/home/qidi/printer_data/config`; direct `config/KAMP` and `config/fluidd.cfg` symlinks are allowed and are not traversed into the backup archive.
- After a successful new installer backup is created during `uninstall`, the runtime prunes installer-created backup zip files under `/home/qidi/printer_data/` to retain only the newest three archives across install and uninstall backup prefixes; unrelated `.zip` files are ignored and `--dry-run` never prunes.
- Status `uninstalling` starts before the first uninstall write under `config/` and stays active through guarded patch reversal, include removal, managed-tree removal, uninstall postflight, and final state-file deletion.
- Final uninstall success returns `Uninstalled.` after uninstall postflight succeeds and `config/tltg_optimized_state.yaml` is deleted.
- Final uninstall success output lists only guarded patch targets preserved as user-modified.
- Final uninstall success output lists managed-tree drift only when `config/tltg-optimized-macros/` contained local modifications before removal.
- During interactive uninstall, after the existing uninstall confirmation and before backup creation, uninstall prompts `Would you like to restore system settings changed by the optimized installer?` when `config/tltg_optimized_state.yaml system_ledger.restore_preimages` contains system changes.
- Accepted system restore confirmation restores `/home/qidi/moonraker/moonraker/components/file_manager/metadata.py`, DNS, APT source, service state, qidiclient GIF files, and AI backend service state recorded in `system_ledger.restore_preimages`.
- Service restore skips any unit that no longer exists at restore time; qidiclient GIF restore copies replaced files from the recorded `/home/qidi/QIDI_Client/access/.gif-backup-<timestamp>` directory and removes files created by the static GIF archive.
- Declined system restore confirmation removes Klipper config changes and leaves current OS hardening state in place.
- `install.sh --uninstall --keep-system-optimizations` skips system setting restore while uninstalling Klipper config changes.
- After final uninstall success output, uninstall removes `/etc/systemd/system/tltg-optimized-auto-update.service` and `/etc/systemd/system/tltg-optimized-auto-update.timer` through sudo when either unit file exists; sudo uses `TLTG_OPTIMIZED_SUDO_PASSWORD` when set, otherwise QIDI's public default password `qiditech`, and prompts for a password when the initial sudo attempt fails and input is interactive.
- Auto-update removal failure prints `Could not disable auto-updates. <reason>` and does not change the successful uninstall result.
- After auto-update removal returns or when no auto-update unit is installed, the runtime prompts `Would you like me to restart Klipper to apply changes?`.
- Accepted restart confirmation triggers a local Moonraker `POST /printer/restart` request.
- Failed automatic restart prints `Could not restart Klipper automatically. Restart Klipper to apply changes.` and does not change the successful uninstall result.
- Restart confirmation input normalized to `N` or `NO` returns without restarting and prints `Restart Klipper to apply changes.`.

Interrupt handling:
- `Ctrl+C` raises `KeyboardInterrupt`, prints `Interrupted. No further installer actions will run.`, returns exit code `130`, and does not print a Python traceback.

Rich terminal UI:
- When stdout is an interactive TTY and `TERM` is not `dumb`, `installer/runtime/reporter.py` renders the install, uninstall, dry-run, clear-recovery-sentinel, and restore-helper flows through the vendored Rich dependency under `installer/runtime/vendor/rich`.
- `--plain`, non-TTY stdout, `TERM=dumb`, or a missing vendored Rich dependency select `PlainReporter` and preserve the line-oriented status output.
- Rich install and uninstall status panels show `stage N/5`, the current status string, preflight target counters, and install/uninstall operation counters emitted by `installer/runtime/preflight.py`, `installer/runtime/runner.py`, and `installer/runtime/uninstall.py`.
- Rich confirmation prompts render the required question and instruction strings in a prompt panel before reading the same stdin response accepted by `installer/runtime/interaction.py`.
- Rich detail output renders preflight target reports, dry-run plans, user-modified patch reports, managed-tree drift, rollback recovery actions, restore backup lists, restore warnings, and restore verification in Rich panels or tables.
- Contract-required status strings, prompt strings, success strings, warning strings, recovery strings, and error strings remain present in Rich output and unchanged in `--plain` output.

Demo-TUI mode:
- `install.sh --demo-tui` renders the install status sequence `checking firmware version`, `checking package version`, `performing preflight checks`, `creating backup`, and `installing` without reading `/home/qidi/update/firmware_manifest.json`, `/home/qidi/printer_data/config`, or Moonraker.
- `install.sh --uninstall --demo-tui` renders the uninstall status sequence `checking firmware version`, `checking installed package`, `performing uninstall preflight checks`, `creating backup`, and `uninstalling` without reading `/home/qidi/update/firmware_manifest.json`, `/home/qidi/printer_data/config`, or Moonraker.
- Demo-TUI mode does not acquire `/home/qidi/printer_data/.tltg_optimized_installer.lock`, does not inspect `/home/qidi/printer_data/.tltg_optimized_recovery_required`, does not create backup archives under `/home/qidi/printer_data/`, and does not write under `/home/qidi/printer_data/config`.
- Demo-TUI mode waits 5 seconds after each visible status change before advancing to the next screen.
- Demo-TUI mode finishes with `Installed.` for install and `Uninstalled.` for uninstall.
- Demo-TUI mode does not emit confirmation prompts, backup-history lines, `Nothing to uninstall.`, or restart prompts.

Internal lock and recovery behavior:
- A single-run advisory lock is acquired before visible status changes and is not represented as a visible status.
- Lock acquisition does not reorder the visible install or uninstall status flow.
- The runtime checks `/home/qidi/printer_data/.tltg_optimized_recovery_required` before visible status changes.
- When the recovery sentinel exists, the run returns `Previous recovery did not complete. Restore from backup before continuing.` and stops before any further action.
- The documented safe clear path is `install.sh --clear-recovery-sentinel` only after restoring from the printed backup and verifying the runtime tree is consistent.
- `install.sh --clear-recovery-sentinel` clears `/home/qidi/printer_data/.tltg_optimized_recovery_required` only when the current `config/` tree byte-matches the recorded backup zip path stored in that sentinel, excluding ignored direct `config/KAMP` and `config/fluidd.cfg` symlinks.

Debug logging:
- `install.sh --debug` emits human-readable `debug | event=...` lines to stdout only.
- Debug output is terminal-only and is never persisted under `/home/qidi/printer_data/`.
- Contract-required status strings, success strings, recovery guidance, and error messages remain unchanged when `--debug` is not set.

Manual restore helper:
- Release bundles ship `restore.sh` beside `install.sh`.
- `restore.sh` uses `RichReporter` for restore backup lists, selected-backup details, destructive restore warning, and restore verification output when stdout is an interactive TTY and `TERM` is not `dumb`; `restore.sh --plain`, non-TTY stdout, `TERM=dumb`, or a missing vendored Rich dependency use `PlainReporter`.
- `restore.sh` lists installer-created backup zip files from `/home/qidi/printer_data/` by archive timestamp and label when no `--backup <path>` argument is provided.
- `restore.sh --backup <path>` restores the specified archive only when it contains a valid, non-empty archived `config/` snapshot.
- `restore.sh` warns that restore overwrites current config changes under `/home/qidi/printer_data/config`, requires an explicit `RESTORE` confirmation, stages the selected archive through a temporary directory, validates the staged `config/` snapshot before any live write, copies the staged snapshot to a full replacement tree under `/home/qidi/printer_data/`, preserves ignored direct `config/KAMP` and `config/fluidd.cfg` symlinks when absent from the selected archive, swaps the replacement tree into `/home/qidi/printer_data/config` with directory renames, rolls back the original tree when the replacement rename fails, and verifies the restored tree before success output.
- `restore.sh` does not clear `/home/qidi/printer_data/.tltg_optimized_recovery_required`.
- After a valid restore when the recovery sentinel exists, the clear step remains `install.sh --clear-recovery-sentinel`.
Required install flow:
1. Acquire the single-run advisory lock before visible status changes unless `auto-update-check` is already holding it for the child install process.
2. Stop the run before visible status changes when `/home/qidi/printer_data/.tltg_optimized_recovery_required` exists.
3. Set the visible status to `checking firmware version` before reading `/home/qidi/printer_data/config`.
4. Detect the printer firmware from `/home/qidi/update/firmware_manifest.json` field `SOC.version` with installer-owned logic.
5. Stop the run before reading `config/tltg_optimized_state.yaml`, creating a backup, or writing any file under `config/` when firmware detection fails.
6. Compare the detected firmware string against `installer/package.yaml firmware.supported`.
7. Stop the run before reading `config/tltg_optimized_state.yaml`, creating a backup, or writing any file under `config/` when the detected firmware is not present in `installer/package.yaml firmware.supported`.
8. Set the visible status to `checking package version` after firmware support is confirmed.
9. Read and validate `config/tltg_optimized_state.yaml` when that file already exists.
10. Continue as a fresh install when `config/tltg_optimized_state.yaml` does not exist.
11. Compare the stored package version from `config/tltg_optimized_state.yaml` against `installer/package.yaml package.known_versions` when `config/tltg_optimized_state.yaml` exists.
12. Stop the run before backup or writes when previous install-state validation fails.
13. Set the visible status to `performing preflight checks` after package-version validation passes.
14. Reject symlink components in runtime paths under `/home/qidi/printer_data` for `config/`, managed-tree destination paths, state file paths, include-line files, preflight target files, patch target files, postflight target files, and backup source paths before creating a backup or modifying runtime files.
15. Validate `installer/klipper/tltg-optimized-macros/` exists as a real non-empty directory and contains every file listed in `installer/package.yaml install.managed_tree.required_files[]` before creating a backup or modifying runtime files.
16. Validate every path in `installer/package.yaml preflight.required_files` before creating a backup or modifying runtime files.
17. Validate every `file + section` target in `installer/package.yaml preflight.required_sections` before creating a backup or modifying runtime files.
18. Validate every `file + line` target in `installer/package.yaml preflight.required_lines` before creating a backup or modifying runtime files.
19. Validate every unique `file + section + option` target in `installer/package.yaml patches.set_options[]` and every unique `file + section` target in `installer/package.yaml patches.delete_sections[]` before creating a backup or modifying runtime files.
20. Query local Moonraker `http://127.0.0.1:7125/printer/objects/query?print_stats` and inspect `print_stats.state` during preflight.
21. Preflight free space for zip backup bytes, rollback preimages, new/rewritten files, same-directory atomic temp files, and the configured safety margin before creating a backup or modifying runtime files.
22. Collect every missing file, section, line, and patch-target result in one pass instead of stopping on the first missing target.
23. Stop the run before backup or writes when any install preflight target is missing, printer-state data cannot be trusted, the printer is printing or paused, or free space is insufficient.
24. During non-dry-run install, prompt `Would you like us to take a backup of your configs and proceed with installation?`; strip surrounding whitespace, uppercase the response, and re-render the prompt for input that does not normalize to `Y`, `YES`, `N`, or `NO`.
25. During non-dry-run install, print `Installation cancelled.` and exit zero before backup or writes when the confirmation input normalizes to `N` or `NO`.
26. Set the visible status to `creating backup` after aggregated preflight checks pass and install confirmation succeeds.
27. Build the backup label from `installer/package.yaml backup.label_prefix`, the detected install firmware, `installer/package.yaml package.version`, and an install timestamp.
28. Create a `.zip` backup of `/home/qidi/printer_data/config` before any write to any file under `config/`; direct `config/KAMP` and `config/fluidd.cfg` symlinks are allowed and are not traversed into the backup archive.
29. Set the visible status to `installing` after the `.zip` backup completes.
30. When an existing installed-state ledger is present, compare current managed-tree file hashes against `managed_tree.files[]` from that ledger and record local managed-tree drift before mirror mode.
31. Compare prior `managed_tree.files[]` ledger hashes against the new bundle contents to distinguish expected bundle-version changes from local drift.
32. When `config/box.cfg [box_extras]` exists, `config/saved_variables.cfg [Variables] box_count > 0`, and `[Variables] enable_box == 0`, prompt to set `enable_box = 1` before managed-tree writes; accepted confirmation writes only `config/saved_variables.cfg` and does not add the value to `config/tltg_optimized_state.yaml`.
33. When `min(config/saved_variables.cfg [Variables] box_count * 4, 16)` requires missing or empty `value_tN` entries, write each required entry to `'slotN'` before managed-tree writes; write only `config/saved_variables.cfg` and do not add the values to `config/tltg_optimized_state.yaml`.
34. When existing non-empty `config/saved_variables.cfg [Variables] value_tN` entries do not equal `'slotN'`, prompt to write each mismatched entry to `'slotN'` before managed-tree writes; accepted confirmation writes only `config/saved_variables.cfg` and does not add the values to `config/tltg_optimized_state.yaml`.
35. Create each directory listed in `installer/package.yaml install.ensure_directories` before mirroring installer-managed files.
36. Mirror `installer/klipper/tltg-optimized-macros/` into runtime `config/tltg-optimized-macros/` according to `installer/package.yaml install.managed_tree` without adding the optimized include line yet.
37. Select the matching `variants[]` entry in each `installer/package.yaml patches.set_options[]` block with a matching `variants[].firmwares[]` entry.
38. Read the current value for each `file + section + option` target in `installer/package.yaml patches.set_options[]` from the runtime file before writing.
39. Write `desired` when the current runtime value equals `expected` in the selected `variants[]` entry.
40. Record a silent no-op for that patch when the current runtime value already equals `desired` in the selected `variants[]` entry.
41. Record that patch as user-modified when the current runtime value differs from both `expected` and `desired` in the selected `variants[]` entry.
42. For each `installer/package.yaml patches.delete_sections[]` target, delete the runtime section only when the current normalized section text SHA-256 equals the selected `variants[].expected_normalized_sha256`; store the deleted section text in `config/tltg_optimized_state.yaml patch_ledger[].expected` and store `__TLTG_SECTION_DELETED__` in `patch_ledger[].desired`.
43. Treat a missing `patches.delete_sections[]` target as an install no-op only when a prior valid installed-state ledger already contains that section-deletion patch target.
44. Record a section deletion patch as user-modified when the current section exists and its normalized SHA-256 does not match `variants[].expected_normalized_sha256`.
45. Ensure `[include tltg-optimized-macros/*.cfg]` exists in runtime `config/printer.cfg` after `[include klipper-macros-qd/*.cfg]` according to `installer/package.yaml install.ensure_lines`, moving or deduping active include lines as needed, only after `patches.delete_sections[]` processing completes.
46. Validate every runtime file implied by `installer/package.yaml install.managed_tree` by SHA-256 and every `file + line` target in `installer/package.yaml postflight.verify_lines` after the managed tree mirror, guarded patches, and include-line write complete.
47. Write `config/tltg_optimized_state.yaml` only after the install postflight checks pass.
48. Store `package.id`, `package.version`, detected install firmware, backup label, install timestamp, managed-tree file hashes, and the guarded patch ledger in `config/tltg_optimized_state.yaml`.
49. Trigger automatic rollback on any failure after the first runtime write, not only on postflight failure.
50. Leave the previous `config/tltg_optimized_state.yaml` content unchanged when the run stops before install postflight completion.
51. Print `Installed.`, then the final user-modified patch report, then `Managed tree drift overwritten:` when present.
52. Repair existing auto-update systemd units before restart prompting; when no auto-update unit exists, prompt for auto-update setup when input is interactive.
53. During auto-update repair or accepted auto-update setup confirmation, attempt to fetch and store the latest release checksum, authenticate with sudo, install the systemd service/timer units, reload systemd, enable `tltg-optimized-auto-update.timer`, and restart `tltg-optimized-auto-update.timer`.
54. Continue systemd timer setup when the initial checksum seed fails; the first successful timer check initializes `config/tltg_optimized_auto_update_state.json` without installing when no previous state exists.
55. Treat auto-update repair and setup failure as non-fatal after install success.
56. Prompt `Would you like me to restart Klipper to apply changes?`; strip surrounding whitespace, uppercase the response, and re-render the prompt for input that does not normalize to `Y`, `YES`, `N`, or `NO`.
57. During accepted restart confirmation, request local Moonraker `POST /printer/restart`.
58. Print `Could not restart Klipper automatically. Restart Klipper to apply changes.` when the restart request fails.
59. Print `Restart Klipper to apply changes.` and exit without restarting when the restart confirmation input normalizes to `N` or `NO`.

Required uninstall flow:
1. Acquire the single-run advisory lock before visible status changes.
2. Stop the run before visible status changes when `/home/qidi/printer_data/.tltg_optimized_recovery_required` exists.
3. Set the visible status to `checking firmware version`.
4. Attempt best-effort current firmware detection from `/home/qidi/update/firmware_manifest.json` for audit/reporting without requiring supported current firmware.
5. Set the visible status to `checking installed package`.
6. Check non-patch installation markers from state-file path presence, managed-tree presence, and the active `[include tltg-optimized-macros/*.cfg]` line.
7. Read and validate `config/tltg_optimized_state.yaml` when it exists.
8. Validate the stored installed package version against `installer/supported_upgrade_sources.yaml` when a state-file ledger is present.
9. Validate that every ledger patch target matches an explicit uninstall-allowed `file + section + option` tuple for that stored installed package version from `installer/supported_upgrade_sources.yaml`.
10. When a valid installed-state ledger is available, additionally treat guarded patch targets currently at recorded `desired` values as install markers.
11. Print `Nothing to uninstall.` and exit zero before backup or writes when no installation markers are present after the non-patch marker check and any valid-ledger patch-marker check.
12. Stop the run before backup or writes when any non-patch installation marker remains but the installed-state ledger is missing, corrupt, schema-incompatible, lacks the required uninstall ledger fields, stores an unsupported installed package version, or contains patch tuples outside the explicit allowed tuple set for that stored installed package version.
13. Set the visible status to `performing uninstall preflight checks`.
14. Reject symlink components in runtime paths under `/home/qidi/printer_data` for `config/printer.cfg`, managed-tree paths, state-file paths, include-line paths, and every patch target from the installed-state ledger.
15. Validate `config/printer.cfg`, managed-tree paths, and every patch target from the installed-state ledger.
16. Resolve every uninstall patch target to exactly one active section and exactly one active option assignment; zero matches are missing targets and multiple matches are ambiguous targets.
17. Query local Moonraker `http://127.0.0.1:7125/printer/objects/query?print_stats` and inspect `print_stats.state` during uninstall preflight.
18. Preflight free space for zip backup bytes, rollback preimages, new/rewritten files, same-directory atomic temp files, and the configured safety margin before creating a backup or modifying runtime files.
19. Detect and record managed-tree drift against the installed-state file manifest before deletion.
20. Stop the run before backup or writes when uninstall preflight targets are missing or ambiguous, printer-state data cannot be trusted, the printer is printing or paused, or free space is insufficient.
21. During non-dry-run uninstall, prompt `Are you sure you want to uninstall?`; strip surrounding whitespace, uppercase the response, and re-render the prompt for input that does not normalize to `Y`, `YES`, `N`, or `NO`.
22. During non-dry-run uninstall, print `Uninstall cancelled.` and exit zero before backup or writes when the confirmation input normalizes to `N` or `NO`.
23. Set the visible status to `creating backup`.
24. Build the uninstall backup label as `tltg-optimized-macros-before-uninstall-<current-firmware-or-unknown-firmware>-<stored-package.version>-<timestamp>`, where the exact fallback token is `unknown-firmware`.
25. Create a `.zip` backup of `/home/qidi/printer_data/config` before any uninstall write to any file under `config/`; direct `config/KAMP` and `config/fluidd.cfg` symlinks are allowed and are not traversed into the backup archive.
26. Set the visible status to `uninstalling`.
27. For each guarded patch target from the installed-state ledger, write recorded `expected` only when the current runtime value still matches the recorded `desired` value.
28. Record a silent no-op for uninstall when the current runtime value already matches the recorded `expected` value.
29. Record that patch as user-modified when the current runtime value differs from both recorded `expected` and recorded `desired` values.
30. Remove the active `[include tltg-optimized-macros/*.cfg]` line from runtime `config/printer.cfg`.
31. Remove runtime `config/tltg-optimized-macros/` after managed-tree drift reporting.
32. Validate uninstall postflight: the include line is absent, the managed tree is absent, and reverted patch targets are at recorded `expected` values unless recorded as user-modified.
33. Delete `config/tltg_optimized_state.yaml` only after uninstall postflight passes.
34. Trigger automatic rollback on any failure after the first uninstall write, not only on uninstall postflight failure.
35. Keep `config/tltg_optimized_state.yaml` unchanged when uninstall stops before uninstall postflight completion.
36. Print `Uninstalled.`, then preserved user-modified patch targets, then managed-tree drift when present.
37. Remove the auto-update systemd service/timer after successful uninstall when either unit file exists.
38. Treat auto-update removal failure as non-fatal after uninstall success.
39. Prompt `Would you like me to restart Klipper to apply changes?`; strip surrounding whitespace, uppercase the response, and re-render the prompt for input that does not normalize to `Y`, `YES`, `N`, or `NO`.
40. During accepted restart confirmation, request local Moonraker `POST /printer/restart`.
41. Print `Could not restart Klipper automatically. Restart Klipper to apply changes.` when the restart request fails.
42. Print `Restart Klipper to apply changes.` and exit without restarting when the restart confirmation input normalizes to `N` or `NO`.
Patch semantics:
- Manifest validation requires every `installer/package.yaml patches.set_options[]` and `patches.delete_sections[]` block to provide at least one firmware-scoped `variants[]` entry, reference only supported firmware values, and define no duplicate variant for the same firmware; install preflight validates only patch targets active for the detected firmware.
- `installer/package.yaml patches.set_options[] variants[].expected` is the runtime value the installer expects to find before writing a guarded install patch.
- `installer/package.yaml patches.set_options[] variants[].desired` is the runtime value the installer writes when the current runtime value still matches `expected`.
- Patch target section resolution requires exactly one active section header with the requested name.
- Patch target option resolution requires exactly one active assignment for the requested option inside the resolved section.
- Patch option matching trims surrounding whitespace around key and value, ignores inline comments, and does not numerically coerce values.
- Zero matching sections or options make the patch target missing.
- Multiple matching sections or options make the patch target ambiguous.
- Missing or ambiguous patch targets fail closed during preflight; the runtime never guesses which target to read or write.
- `installer/package.yaml patches.set_options[]` targets that already match `desired` are install silent no-ops.
- `installer/package.yaml patches.set_options[]` targets that match neither `expected` nor `desired` are treated as install user-modified and are reported after installation completes.
- `installer/package.yaml patches.delete_sections[] variants[].expected_normalized_sha256` is the SHA-256 of the normalized section text from `installer/runtime/klipper_cfg.py`, which removes comments and whitespace outside quoted strings.
- `installer/package.yaml patches.delete_sections[]` targets are deleted only when the live normalized section text hash matches `expected_normalized_sha256`.
- `installer/package.yaml patches.delete_sections[]` targets that are missing during reinstall are install silent no-ops only when the prior `config/tltg_optimized_state.yaml patch_ledger[]` already contains the same `file + section + __section__` tuple.
- `installer/package.yaml patches.delete_sections[]` targets whose live normalized section hash does not match `expected_normalized_sha256` are treated as install user-modified and fail install preflight before backup or writes.
- Uninstall uses the recorded `expected` and `desired` values from `config/tltg_optimized_state.yaml`, not the current bundle manifest, to decide guarded patch reversal.
- Uninstall accepts a ledger patch target only when its `file + section + option` tuple is explicitly allowed for the stored installed package version in `installer/supported_upgrade_sources.yaml`.
- Schema-valid installed-state ledgers are trusted as uninstall inputs; uninstall still refuses to overwrite a live runtime value unless that value matches the recorded `desired` value exactly.
