# installer-lifecycle Specification

## Purpose

The installer admits compatible firmware, applies guarded configuration, Klipper source, and optional system changes, preserves recoverability, and activates updates safely across install, auto-update, restore, and uninstall.

## Requirements

### Requirement: Firmware-gated baseline management
The installer SHALL apply configuration changes or legacy stock restoration only from the validated baseline selected for the detected firmware, reject unsupported firmware before state reads, and reject unmatched or invalid baseline data before backup creation or live writes.

#### Scenario: Supported firmware selects one baseline
- **WHEN** `/home/qidi/update/firmware_manifest.json SOC.version` is `01.01.06.03` or `01.01.06.04`
- **THEN** firmware validation passes
- **AND** every active configuration target selects no more than one variant for that firmware
- **AND** each declared target has at least one variant using supported firmware

#### Scenario: Unsupported or unreadable firmware is rejected
- **WHEN** firmware cannot be read or is not listed in `installer/package.yaml firmware.supported`
- **THEN** installation stops before reading `config/tltg_optimized_state.yaml`, creating a backup, or writing live files

#### Scenario: Firmware-specific expectations are enforced
- **WHEN** guarded preflight runs for supported firmware
- **THEN** configuration and source expectations come from that firmware's manifest variant
- **AND** targets belonging only to another firmware do not affect preflight

#### Scenario: Current 01.01.06.04 stock baseline is selected
- **WHEN** firmware `01.01.06.04` is detected
- **THEN** its baseline represents QIDI defaults commit `5da5767379ac22fc4fbe1606ec7093ce056229ae`
- **AND** X/Y closed-loop stock values include `query_cycle:10`, `trigger_current:400`, `trigger_time:2`, and `trigger_speed:50`
- **AND** `_km_idle_timeout` saves `saved_extruder_temp` on `RESUME_PRINT`
- **AND** `Chamber_Thermal_Protection_Sensor max_temp` is `170`
- **AND** official filament `[fila25]` uses `PA6-CF` for both `filament` and `type`

#### Scenario: Legacy manual installation is migrated
- **WHEN** legacy optimized markers exist without valid installer state
- **THEN** an active or paused print blocks migration
- **AND** accepted migration backs up `config/`, restores only the detected firmware's validated stock snapshot, removes the legacy optimized tree, and restarts `qidi-client.service`
- **AND** `config/MCU_ID.cfg`, `config/box.cfg`, `config/fluidd.cfg`, `config/saved_variables.cfg`, and direct `config/KAMP` symlinks are preserved
- **AND** missing or invalid snapshot data fails before stock files are overwritten

### Requirement: Transactional configuration lifecycle
Install, reinstall, restore, and uninstall SHALL validate ownership and runtime safety before mutation, preserve recoverable preimages, and never overwrite unowned drift.

#### Scenario: A single safe transaction is admitted
- **WHEN** an installer operation starts
- **THEN** it acquires `/home/qidi/printer_data/.tltg_optimized_installer.lock`
- **AND** blocks before visible work when `/home/qidi/printer_data/.tltg_optimized_recovery_required` exists
- **AND** rejects managed-path symlinks except direct `config/KAMP` and `config/fluidd.cfg` symlinks
- **AND** validates prior state, required bundle/config targets, printer state, and free space before backup or writes
- **AND** unavailable or malformed Moonraker state and `printing` or `paused` states fail closed
- **AND** reserved space covers backup data, rollback preimages, rewritten files, atomic temporary files, and `max(64 MiB, 20% of the subtotal)`

#### Scenario: Guarded patches preserve ownership
- **WHEN** an active option or section patch is classified
- **THEN** exactly one active section and option must resolve
- **AND** recognized stock content is changed to desired content
- **AND** already-desired content is a no-op
- **AND** prior-managed content may migrate only when a valid ledger proves ownership
- **AND** unrecognized live content is preserved as user-modified or fails closed where section deletion cannot be proven
- **AND** uninstall reverses only explicit stored-package tuples whose live value still equals the recorded desired value

#### Scenario: Managed configuration is committed atomically
- **WHEN** installation proceeds after confirmation or `--yes`
- **THEN** `/home/qidi/printer_data/config` is backed up before the first write
- **AND** installer backups under `/home/qidi/printer_data/` retain the newest three install/uninstall archives without pruning unrelated archives
- **AND** local drift inside the prior-ledger-owned optimized tree is reported and overwritten by mirror convergence
- **AND** differences between prior bundle hashes and new bundle content alone are treated as expected version changes, not local drift
- **AND** guarded patches are applied and `[include tltg-optimized-macros/*.cfg]` is placed after the stock macro include
- **AND** hashes and postflight lines are verified before writing mode-`0600` `config/tltg_optimized_state.yaml`
- **AND** state records package and firmware identity, backup label, managed-tree hashes, guarded patch provenance, and optional source/system ledgers
- **AND** failure after the first write restores tracked preimages and leaves the previous state file unchanged

#### Scenario: Dry-run and interruption do not mutate state
- **WHEN** `--dry-run` is selected
- **THEN** planned config, source, system, and service actions are reported without backup creation, pruning, or live writes
- **AND** when `Ctrl+C` interrupts a run, no later installer action executes and the process exits `130` without a traceback

#### Scenario: Uninstall is ledger-bound
- **WHEN** uninstall finds a valid supported installed-state ledger
- **THEN** it backs up config and declared external sources before writes
- **AND** reverts only unchanged installer-owned patches and sources
- **AND** reports and preserves user-modified targets
- **AND** removes the optimized include and managed tree
- **AND** deletes installed state only after postflight succeeds
- **AND** a missing or invalid ledger blocks uninstall whenever non-patch install markers remain

#### Scenario: Restore replaces and verifies the archived runtime
- **WHEN** `restore.sh` receives explicit `RESTORE` confirmation for a validated installer archive
- **THEN** config and external members are staged before writes
- **AND** the live config tree is replaced by directory rename while absent direct `config/KAMP` and `config/fluidd.cfg` symlinks are preserved
- **AND** partial failure compensates config, external files, and activation-marker state
- **AND** every restored root is verified before success
- **AND** OS-level system settings are not restored by `restore.sh`
- **AND** the recovery sentinel remains until `install.sh --clear-recovery-sentinel` verifies config and external snapshots against the recorded archive

#### Scenario: Reporting modes preserve the same contract
- **WHEN** Rich TTY, `--plain`, non-TTY, `TERM=dumb`, debug, help/version, or demo-TUI reporting is selected
- **THEN** safety decisions and required operator outcomes remain equivalent
- **AND** demo-TUI performs no printer, lock, backup, or config mutation
- **AND** debug output remains terminal-only

### Requirement: Guarded managed-source lifecycle and activation
The installer SHALL manage vendor Python only through validated firmware-scoped provenance, atomic and recoverable file transactions, guarded restoration, and verified replacement of the Klipper service process.

#### Scenario: Source manifest and preflight are trusted
- **WHEN** a managed-source entry is validated
- **THEN** its ID and destination are unique
- **AND** its bundle source is a non-symlink regular file under `installer/klipper/`
- **AND** its destination is a relative non-traversing path under `klippy/extras/`
- **AND** each entry defines exactly one variant for every supported firmware with exact expected and desired SHA-256 values
- **AND** a live target is accepted only when it matches the selected stock hash, selected desired hash, or a desired hash proven by valid prior state
- **AND** unknown content, symlinks, or path escape fail before backup or live writes

#### Scenario: Source deployment preserves provenance
- **WHEN** a stock or prior-managed source is changed
- **THEN** the pending-activation marker exists before the first source write
- **AND** the first verified original bytes, SHA-256, and mode are retained across upgrades
- **AND** the payload is replaced atomically in the destination directory with its mode preserved
- **AND** Python syntax and the declared desired SHA-256 are verified before installed state is committed

#### Scenario: Source state is validated before trust
- **WHEN** a schema-version-1 installed-state ledger is loaded
- **THEN** an absent `source_patches` field is treated as empty
- **AND** records are strictly validated for unique identity, normalized allowlisted destination, firmware/manifest binding, hashes, strict base64 preimage, and mode `0000` through `0777`
- **AND** an original hash is trusted only when it is an enumerated stock or supported-upgrade baseline
- **AND** malformed, tampered, cross-firmware, or self-consistent but unauthorized provenance fails before backup or live writes

#### Scenario: Source-aware backup and rollback are complete
- **WHEN** install, uninstall, or restore can change managed source
- **THEN** backup precedes the first source write and `.tltg-external-files.json` binds each source ID and destination to one regular archive member, firmware, SHA-256, and mode
- **AND** invalid, incomplete, unallowlisted, symlinked, cross-firmware, or hash-mismatched metadata is rejected before live writes
- **AND** transaction failure restores and verifies the preimage atomically
- **AND** failed compensation records the target and archive in the mode-`0600` recovery sentinel and blocks further mutation

#### Scenario: Source restore does not overwrite drift
- **WHEN** uninstall or restore processes managed source
- **THEN** a live desired hash may be restored to the recorded original bytes and mode
- **AND** a live original hash is a no-op
- **AND** any other live hash is preserved and reported as drift
- **AND** archives labeled `26.07.26.1` or later, with unknown provenance, or with state declaring source patches require complete external metadata
- **AND** only the explicit pre-source-patch compatibility set may use config-only archives

#### Scenario: Source activation requires a new ready process
- **WHEN** managed Python is written, restored, rolled back, or remains pending activation
- **THEN** `/home/qidi/printer_data/.tltg_optimized_klipper_restart_required` exists with mode `0600` and binds each destination to its intended live hash
- **AND** activation records a positive integer process ID from `GET /printer/info`
- **AND** sends `POST /machine/services/restart` with content type `application/json` and JSON `{"service":"klipper"}`
- **AND** succeeds only after bounded polling observes `ready` under a different positive integer process ID
- **AND** configuration-only changes with no pending marker may use `POST /printer/restart`

#### Scenario: Every entrypoint resolves pending activation
- **WHEN** install, uninstall, restore, or auto-update encounters valid pending activation
- **THEN** automatic activation is blocked if any bound live source hash has drifted
- **AND** interactive flows verify an accepted service restart or retain the marker and manual instructions
- **AND** `--yes` and auto-update require verified restart after idle-printer preflight and return nonzero on failure
- **AND** the marker is removed atomically only after verified activation

### Requirement: QIDI Box saved-variable reconciliation
The installer SHALL keep QIDI Box enablement and logical tool mappings usable without claiming ownership of vendor-managed saved variables.

#### Scenario: Box support is optional
- **WHEN** `config/box.cfg` or `[box_extras]` is absent
- **THEN** Box reconciliation is skipped
- **AND** installation preflight and optimized non-Box behavior remain available

#### Scenario: Detected Box may be enabled
- **WHEN** `[box_extras]` exists, `box_count > 0`, and `enable_box = 0`
- **THEN** interactive install offers to set `enable_box = 1`
- **AND** `--yes` applies the enablement without prompting
- **AND** a declined prompt preserves the disabled value

#### Scenario: Required tool mappings are present
- **WHEN** `box_count` requires logical tools `0` through `min(box_count * 4, 16) - 1`
- **THEN** missing or empty `value_tN` entries are written as `'slotN'`
- **AND** interactive install asks before correcting non-empty mismatches
- **AND** `--yes` corrects mismatches without prompting

#### Scenario: Vendor saved variables remain outside installer ownership
- **WHEN** enablement or mapping reconciliation changes `config/saved_variables.cfg`
- **THEN** those values are not recorded in `config/tltg_optimized_state.yaml`
- **AND** uninstall does not revert them

#### Scenario: Box-count changes reconcile only while idle
- **WHEN** auto-update observes a changed `box_count` while Klipper is reachable and idle
- **THEN** missing or empty mappings are added
- **AND** `config/tltg_optimized_runtime_state.json last_observed_box_count` is updated
- **AND** busy or unknown printer state causes reconciliation to skip without writes

### Requirement: Policy-driven system optimizations
The installer SHALL apply OS-level changes only through explicit stored policy, retain first restore preimages, reconcile opted-in desired state, and isolate system failures from a verified Klipper configuration install.

#### Scenario: Moonraker 3MF metadata extraction is patched independently
- **WHEN** the supported QIDI metadata extractor shape is present
- **THEN** `.gcode.3mf` uses `Metadata/slice_info.config` plate index `N` for `plate_N.gcode`, `plate_N.json`, and thumbnail selection
- **AND** missing or invalid index falls back to plate `1`
- **AND** the original file is retained for uninstall and `moonraker.service` restarts after change or restore

#### Scenario: System policy is explicit and persistent
- **WHEN** interactive install reaches system optimization selection
- **THEN** it records `system_optimizations` as `enabled` or `disabled`
- **AND** records AI detection as `disable`, `keep_enabled`, or `unset`
- **AND** `--skip-system-optimizations`, `--disable-ai-detection`, and `--keep-ai-detection` set the corresponding noninteractive policy
- **AND** auto-update reuses valid stored policy without prompting

#### Scenario: Enabled hardening applies the declared operations
- **WHEN** `system_optimizations = enabled`
- **THEN** `/etc/resolv.conf` targets `/run/resolvconf/resolv.conf` with an empty resolvconf head and fallback tail `1.1.1.1`, then `8.8.8.8`
- **AND** `/etc/apt/sources.list` uses the declared Debian Bullseye main, security, and updates entries without running `apt update` or `apt upgrade`
- **AND** existing `xl2tpd` and `bluetooth` services are stopped and disabled
- **AND** validated bundled static GIFs replace qidiclient assets with original ownership/mode preserved and `qidi-client.service` restarted when present
- **AND** `algo_app.service` is disabled only when AI policy is `disable`
- **AND** missing service units are recorded and do not fail the Klipper install

#### Scenario: System changes are recoverable and reconcilable
- **WHEN** a system operation first changes live state
- **THEN** `system_ledger.restore_preimages` retains its first file/service preimage and backup path
- **AND** `/home/qidi/printer_data/.tltg_optimized_system_journal.json` records in-progress work
- **AND** operation failure rolls back journaled system work, deletes no verified Klipper config installation, and reports partial system failure
- **AND** successful reconciliation reapplies drifted opted-in operations without replacing first restore preimages

#### Scenario: Uninstall respects the operator's system decision
- **WHEN** uninstall finds system restore preimages
- **THEN** interactive uninstall offers to restore them
- **AND** accepted or noninteractive restoration restores recorded DNS, APT, service, Moonraker, and qidiclient state when targets still exist
- **AND** `--keep-system-optimizations` or a declined prompt leaves current system state unchanged

### Requirement: Safe automatic update lifecycle
Automatic updates SHALL run as an idle-only, checksum-verified installer flow, resolve pending source activation before release decisions, and keep setup/removal failures separate from successful config operations.

#### Scenario: Timer units are installed and repaired
- **WHEN** automatic updates are enabled or existing units are repaired
- **THEN** `/etc/systemd/system/tltg-optimized-auto-update.service` and `.timer` run five minutes after boot and one hour after each active run
- **AND** service execution clears installer/archive URL override environment variables
- **AND** systemd setup or repair failure is reported without changing successful installation status

#### Scenario: Pending activation precedes release checks
- **WHEN** `auto-update-check` finds a Klipper activation marker
- **THEN** it validates idle state, target allowlist, and live hashes and retries verified activation before checksum fetch, equality, or missing-state initialization
- **AND** failed activation retains the marker and checksum state

#### Scenario: Release state is advanced only after verified installation
- **WHEN** no pending activation remains
- **THEN** checksum fetch failure exits successfully without changing state
- **AND** a matching checksum reports current without installing
- **AND** missing checksum state initializes `config/tltg_optimized_auto_update_state.json` without installing
- **AND** a changed checksum proceeds only while the printer is idle
- **AND** the archive SHA-256 is verified before replacing the bundle
- **AND** every member is a regular file or directory under `tltg-optimized-macros/` with no absolute or `..` path
- **AND** `tltg-optimized-macros/install.sh` exists as a regular file
- **AND** the child runs `install.sh --yes --plain`
- **AND** the checksum advances only after child install and required source activation succeed

#### Scenario: Disable removes update state
- **WHEN** automatic updates are disabled or uninstall completes
- **THEN** the timer is disabled, service/timer units are removed, systemd is reloaded, and auto-update checksum state is removed
- **AND** removal failure is reported without changing successful uninstall status
