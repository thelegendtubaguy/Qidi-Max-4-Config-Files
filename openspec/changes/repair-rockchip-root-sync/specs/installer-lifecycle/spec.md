## ADDED Requirements

### Requirement: Safe host OS reboot orchestration
The installer SHALL persist, schedule, and verify host OS reboot requests without rebooting an active printer or interrupting an uncommitted installer or auto-update transaction.

#### Scenario: Rockchip repair records a reboot requirement
- **WHEN** an installer-owned Rockchip operation is newly applied or reconciled on the current boot
- **THEN** `/home/qidi/printer_data/.tltg_optimized_host_reboot_required` is atomically written with mode `0600`
- **AND** it records a schema version, reason, operation ID, package version, creation time, and current boot ID
- **AND** the marker contains no executable command

#### Scenario: Interactive install may schedule the host reboot
- **WHEN** interactive install completes successfully with a valid pending host-reboot marker
- **THEN** it offers to reboot the host OS
- **AND** an accepted prompt schedules the reboot only after final installer success reporting
- **AND** a declined prompt leaves the marker pending

#### Scenario: Explicit direct reboot bypasses the prompt
- **WHEN** `--reboot-host` is supplied to a successful direct install or uninstall with a valid pending managed reboot
- **THEN** the guarded scheduling path runs without an additional prompt
- **AND** direct `--yes` without `--reboot-host` does not implicitly schedule a host reboot

#### Scenario: Host reboot fails closed while printer state is unsafe
- **WHEN** host reboot scheduling is requested
- **THEN** printer state is queried again immediately before scheduling
- **AND** `printing`, `paused`, or unknown state performs no reboot and retains the pending marker
- **AND** idle state permits scheduling to continue

#### Scenario: Reboot occurs after process completion
- **WHEN** an idle host reboot is authorized
- **THEN** authenticated sudo creates a delayed transient systemd reboot job
- **AND** final status is emitted and the installer lock can be released before shutdown begins
- **AND** scheduler failure is reported without changing successful config or system-operation state
- **AND** the pending marker remains available for retry

#### Scenario: Completed reboot is verified
- **WHEN** a later invocation reads a pending marker whose boot ID differs from the current boot
- **THEN** the marker is removed only after the bound Rockchip operation passes post-boot verification
- **AND** failed verification retains the marker and reports incomplete host-reboot activation

#### Scenario: Dry-run does not request a reboot
- **WHEN** dry-run evaluates a host reboot that would be required or explicitly requested
- **THEN** it reports the pending marker and delayed reboot action
- **AND** it does not create the marker, query sudo, schedule systemd work, or reboot the host

## MODIFIED Requirements

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
- **AND** a recognized defective `rockchip.service` is replaced by the declared systemd drop-in command and `/` is remounted without the exact `sync` option
- **AND** missing service units are recorded and do not fail the Klipper install

#### Scenario: Defective Rockchip first-boot service is neutralized
- **WHEN** system optimizations are enabled, the Rockchip drop-in is absent, and the live unit and script contain the complete declared defective structure regardless of file hash
- **THEN** the installer atomically creates `/etc/systemd/system/rockchip.service.d/override.conf` with the declared no-op command
- **AND** `/etc/init.d/rockchip.sh`, `/lib/systemd/system/rockchip.service`, and `/usr/local/first_boot_flag` remain unchanged
- **AND** systemd is reloaded, the no-op unit completes with result `success` and exit status `0`, and `inactive/dead` is accepted after completion
- **AND** the live root mount is remounted read-write without the exact `sync` option
- **AND** postflight verifies the drop-in bytes, effective command, service result, and root mount options

#### Scenario: Existing desired Rockchip override remains unowned
- **WHEN** the Rockchip drop-in already has the exact declared desired content without valid prior installer ownership
- **THEN** it is accepted without adding its file preimage to installer restore ownership
- **AND** a synchronous live root mount is reconciled asynchronously while system optimizations remain enabled

#### Scenario: Unknown or corrected Rockchip state is preserved
- **WHEN** the drop-in has any other content or is a symlink, or the absent-drop-in vendor unit and script do not contain the complete declared defective structure
- **THEN** the installer preserves the live files and reports the Rockchip operation as unsupported or drifted
- **AND** no Rockchip file, service, completion flag, or mount option is changed

#### Scenario: System changes are recoverable and reconcilable
- **WHEN** a system operation first changes live state
- **THEN** `system_ledger.restore_preimages` retains its first file/service preimage and backup path
- **AND** Rockchip restore state records the drop-in preimage, unit state, and whether `/` had the exact `sync` option
- **AND** `/home/qidi/printer_data/.tltg_optimized_system_journal.json` records in-progress work
- **AND** operation failure rolls back journaled system work, including systemd reload and the prior root mount mode for Rockchip remediation, deletes no verified Klipper configuration installation, and reports partial system failure
- **AND** successful reconciliation reapplies drifted opted-in operations without replacing first restore preimages
- **AND** auto-update reconciliation restores an installer-owned Rockchip drop-in and removes a reintroduced exact `sync` root mount option

#### Scenario: Dry-run reports Rockchip actions without mutation
- **WHEN** `--dry-run` evaluates enabled system optimizations on a recognized defective Rockchip shape
- **THEN** it reports the planned drop-in, systemd reload, no-op unit start, and asynchronous root remount
- **AND** it does not write the drop-in, invoke systemd mutation, change the mount, or create a restore preimage

#### Scenario: Uninstall respects the operator's system decision
- **WHEN** uninstall finds system restore preimages
- **THEN** interactive uninstall offers to restore them
- **AND** accepted or noninteractive restoration restores recorded DNS, APT, service, Moonraker, qidiclient, and Rockchip state when installer-owned targets still equal their declared desired state
- **AND** Rockchip restoration restores the recorded drop-in preimage, reloads systemd, and restores the recorded root mount mode
- **AND** a user-modified Rockchip drop-in is preserved and reported instead of overwritten
- **AND** accepted Rockchip restoration clears its pending repair-reboot marker because repaired state is no longer desired
- **AND** operator drift or `--keep-system-optimizations` preserves the pending marker with current system state
- **AND** a declined prompt leaves current system state unchanged

### Requirement: Safe automatic update lifecycle
Automatic updates SHALL run as an idle-only, checksum-verified installer flow, resolve pending activation before release decisions, apply enabled system optimizations from the installed release, and keep setup, removal, and reboot failures separate from successful config operations.

#### Scenario: Timer units are installed and repaired
- **WHEN** automatic updates are enabled or existing units are repaired
- **THEN** `/etc/systemd/system/tltg-optimized-auto-update.service` and `.timer` run five minutes after boot and one hour after each active run
- **AND** service execution clears installer/archive URL override environment variables
- **AND** systemd setup or repair failure is reported without changing successful installation status

#### Scenario: Pending activation precedes release checks
- **WHEN** `auto-update-check` finds a Klipper activation marker
- **THEN** it validates idle state, target allowlist, and live hashes and retries verified activation before checksum fetch, equality, or missing-state initialization
- **AND** failed activation retains the marker and checksum state

#### Scenario: Changed-release auto-update applies the Rockchip repair
- **WHEN** a changed release is installed by auto-update and stored policy has `system_optimizations = enabled`
- **THEN** the child installer evaluates and applies the Rockchip operation from the new release
- **AND** the child records any required host reboot and the pre-install auto-update checksum but does not schedule the reboot
- **AND** the successful child arms a delayed follow-up auto-update invocation so a parent from the preceding release cannot strand the marker
- **AND** the follow-up schedules no reboot until checksum advancement is durable
- **AND** disabled or absent system-optimization policy neither applies the repair nor requests a reboot

#### Scenario: Already-current auto-update reconciles the Rockchip repair
- **WHEN** the release checksum is already current and enabled Rockchip desired state is absent or drifted
- **THEN** `auto-update-check` reconciles the Rockchip operation while the printer is idle
- **AND** successful reconciliation records any required host reboot

#### Scenario: Release state is advanced before reboot scheduling
- **WHEN** no pending Klipper activation remains
- **THEN** checksum fetch failure exits successfully without changing state
- **AND** a matching checksum reports current without installing before any needed system reconciliation
- **AND** missing checksum state initializes `config/tltg_optimized_auto_update_state.json` without installing
- **AND** a changed checksum proceeds only while the printer is idle
- **AND** the archive SHA-256 is verified before replacing the bundle
- **AND** every member is a regular file or directory under `tltg-optimized-macros/` with no absolute or `..` path
- **AND** `tltg-optimized-macros/install.sh` exists as a regular file
- **AND** the child runs `install.sh --yes --plain`
- **AND** the checksum advances only after child install and required source activation succeed
- **AND** a Rockchip host reboot is scheduled only after the advanced checksum is durably written and printer state is revalidated as idle
- **AND** failed child installation does not advance the checksum or schedule a reboot

#### Scenario: Pending auto-update reboot is retried
- **WHEN** `auto-update-check` finds a valid host-reboot marker from the current boot
- **THEN** idle state schedules the delayed reboot and active or unknown state retains the marker without rebooting

#### Scenario: Completed auto-update reboot is cleared
- **WHEN** the host-reboot marker records an earlier boot
- **THEN** successful Rockchip post-boot verification clears the marker before release processing continues
- **AND** failed verification retains the marker and reports incomplete activation

#### Scenario: Disable removes update state
- **WHEN** automatic updates are disabled or uninstall completes
- **THEN** the timer is disabled, service/timer units are removed, systemd is reloaded, and auto-update checksum state is removed
- **AND** removal failure is reported without changing successful uninstall status
