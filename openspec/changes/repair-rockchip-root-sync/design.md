## Context

The QIDI Max 4 vendor image runs `/lib/systemd/system/rockchip.service` with `ExecStart=/etc/init.d/rockchip.sh`. The machine owner reports that the resulting root-mount defect has existed throughout the machine's firmware history.

`/proc/device-tree/compatible` identifies RK3308. The script selects `CHIPNAME="rk3208"`, has no matching `install_packages` case, remounts `/` with `sync`, and exits under `#!/bin/bash -e` when `apt` rejects `/libmali-**-x11*.deb`. `/usr/local/first_boot_flag` remains absent, so the failure repeats at every boot. Neither `/etc/init.d/rockchip.sh` nor `/lib/systemd/system/rockchip.service` is owned by a Debian package.

A live remediation used `/etc/systemd/system/rockchip.service.d/override.conf`:

```ini
[Service]
ExecStart=
ExecStart=/bin/true
```

After `systemctl daemon-reload`, starting `rockchip.service`, and `mount -o remount,rw,async /`, the effective command exited `0`, the root mount changed from `rw,relatime,sync` to `rw,relatime`, and a reboot preserved the asynchronous mount. Klipper, Moonraker, and qidiclient returned active and the printer returned to `standby`.

The installer currently restarts Klipper and selected services but has no host OS reboot path, durable host-reboot marker, delayed reboot scheduler, or post-boot completion check. Auto-update installation must commit its release checksum before any reboot can terminate the updater process.

### Controlled performance measurements

The benchmark ran on Linux `5.10.160`, Python `3.9.2`, and an ext4 root filesystem with `19,766,173,696` bytes available. `qidi-client.service`, `klipper.service`, and `moonraker.service` were inactive for every measured trial. Each mode used one excluded warm-up and five measured trials. Batch-write timings include a final `fsync`, and metadata timings include directory `fsync` after creation and deletion.

| Workload | Synchronous median (range) | Asynchronous median (range) | Latency reduction | Speedup |
|---|---:|---:|---:|---:|
| 512 × 4 KiB writes, 2 MiB total | 1510.856 ms (1478.720–1541.888) | 56.153 ms (55.003–56.659) | 96.3% | 26.91× |
| 8 × 256 KiB writes, 2 MiB total | 74.072 ms (71.087–78.555) | 49.573 ms (48.481–51.603) | 33.1% | 1.49× |
| 64 × 4 KiB writes with `fsync` after each write | 203.015 ms (185.096–218.513) | 192.629 ms (177.627–197.354) | 5.1% | 1.05× |
| Create and remove 64 files, 128 metadata operations | 706.394 ms (669.849–711.707) | 96.790 ms (91.821–101.043) | 86.3% | 7.30× |

Measured 4 KiB batch throughput increased from `1.324 MiB/s` to `35.617 MiB/s`; 256 KiB batch throughput increased from `27.001 MiB/s` to `40.345 MiB/s`. The per-write `fsync` control changed by only 5.1%, consistent with both mount modes paying an explicit durability cost for every write.

Raw elapsed-time samples in milliseconds:

- 4 KiB batch, sync: `1532.075, 1541.888, 1494.706, 1510.856, 1478.720`
- 4 KiB batch, async: `56.153, 56.152, 56.659, 56.413, 55.003`
- 256 KiB batch, sync: `71.087, 78.555, 74.072, 73.097, 74.848`
- 256 KiB batch, async: `49.573, 50.236, 51.603, 48.481, 49.544`
- Durable 4 KiB, sync: `185.096, 203.015, 209.391, 187.652, 218.513`
- Durable 4 KiB, async: `192.629, 179.914, 195.058, 197.354, 177.627`
- Metadata, sync: `692.243, 707.024, 711.707, 706.394, 669.849`
- Metadata, async: `100.926, 96.790, 101.043, 93.369, 91.821`

## Goals / Non-Goals

**Goals:**

- Apply the remediation only through enabled system-optimization policy.
- Preserve `/etc/init.d/rockchip.sh` and `/lib/systemd/system/rockchip.service` unchanged.
- Neutralize only recognized defective vendor state or reconcile a prior installer-owned override.
- Remove the exact `sync` option from the live root mount immediately and record a durable host-reboot requirement for boot-path verification.
- Provide a guarded host OS reboot path after all installer state is committed.
- Apply and reboot the repair for eligible auto-update users without advancing release state after a failed install.
- Preserve first preimages, atomic rollback, auto-update reconciliation, dry-run reporting, and optional uninstall restoration.
- Preserve unknown or operator-managed drop-ins.

**Non-Goals:**

- Complete the generic Rockchip first-boot package, rfkill, power-management, video-node, or GPU setup.
- Create `/usr/local/first_boot_flag`.
- Patch `rk3208` to `rk3308` or install Mali packages.
- Claim a print-quality improvement from the storage benchmark.
- Apply the remediation when system optimizations are disabled.

## Decisions

### Use a systemd drop-in that replaces `ExecStart`

The installer will manage `/etc/systemd/system/rockchip.service.d/override.conf` with the tested `/bin/true` command. The unit remains enabled, preserving the vendor dependency graph, but neither boot nor manual unit start can execute the defective script.

Patching `/etc/init.d/rockchip.sh` was rejected because changing the chip name does not add an RK3308 package case, the expected root-level package artifacts are absent, and successful continuation would execute generic rfkill, power-management, video-node, and hardware-clock mutations that have never completed on the observed Max 4. Creating `/usr/local/first_boot_flag` was rejected because it claims completion of those unexecuted operations. Disabling the unit was rejected because dependency or manual starts can still invoke a disabled unit; masking was rejected as more invasive than an effective-command override.

### Guard the defective structure separately from desired-state recognition

`installer/package.yaml` will declare the unit path, script path, drop-in path and content, required defective-script markers, and desired root mount options. A missing drop-in may be created only when the live unit starts `/etc/init.d/rockchip.sh` and the script contains all declared defect predicates: RK3308 selects `rk3208`, first-boot setup remounts `/` with `sync`, package installation precedes creation of `/usr/local/first_boot_flag`, and error-exit behavior can prevent that flag. An exact desired drop-in is accepted regardless of whether it was created manually or by a prior installer. Hash changes alone do not change classification; conflicting drop-in content, symlinks, a different effective command, or absence of the complete defective structure is preserved and reported without a Rockchip write.

An exact desired drop-in found without ledger ownership is not added to `restore_preimages`. Reconciliation may still remount a synchronous root asynchronously, but uninstall will not remove an override the installer did not create. Prior ledger ownership permits normal reconciliation.

### Treat the drop-in and live mount as one journaled operation

The operation will capture the drop-in type, bytes, mode, ownership, unit state, and whether `/` has the exact `sync` option. Apply will atomically install the drop-in, run `systemctl daemon-reload`, clear the failed state, start the no-op unit, and run `mount -o remount,rw,async /`.

Postflight will require the declared drop-in bytes, effective `ExecStart=/bin/true`, service result `success` with exit status `0`, and no exact `sync` token in the root mount options. `inactive/dead` is valid because the existing unit is `Type=simple` and `/bin/true` exits immediately.

Apply failure will restore the captured drop-in, reload systemd, restore the captured root mount mode, and retain the existing system recovery semantics. Accepted uninstall restoration will perform the same restoration only while the live drop-in still matches the installer-owned desired bytes; drift is preserved and reported. `--keep-system-optimizations` leaves the override and asynchronous mount unchanged.

### Reconcile after boot or vendor drift

Auto-update reconciliation will evaluate the operation whenever stored policy is `system_optimizations: enabled`. This repairs a missing installer-owned drop-in and remounts `/` asynchronously if vendor startup restored `sync`. Vendor updates remain eligible while the complete defective structure is present; a corrected structure is preserved without an override.

### Persist reboot requirement before leaving the system transaction

A successful installer-owned Rockchip apply or reconciliation will atomically write `/home/qidi/printer_data/.tltg_optimized_host_reboot_required` with mode `0600`. The JSON marker will contain a schema version, reason `rockchip_root_sync`, operation ID, package version, creation time, and the current `/proc/sys/kernel/random/boot_id`. The marker contains no executable command.

A later invocation compares the marker boot ID with the current boot ID. A changed boot ID clears the marker only after Rockchip postflight verifies the desired drop-in, effective command, successful unit result, and asynchronous root mount. An unchanged boot ID keeps the reboot pending.

### Schedule host reboot only at a terminal boundary

A new host-reboot helper will perform one final Moonraker idle-state query immediately before scheduling. Printing, paused, or unknown state leaves the marker pending and performs no reboot. After emitting the final operator status, the helper will use authenticated sudo to schedule `systemctl reboot` through a delayed transient systemd unit, allowing the installer process to return and release its lock before shutdown begins.

Interactive install will offer the reboot when a marker is created or remains pending. `install.sh --reboot-host` authorizes scheduling of a valid pending managed reboot after a successful direct install or uninstall; it is not an arbitrary reboot command. Direct `--yes` without that flag leaves the marker pending rather than introducing an unexpected reboot. Accepted uninstall restoration clears a pending Rockchip repair marker because the repaired state is no longer desired; operator-drift preservation and `--keep-system-optimizations` retain it. Dry-run reports the reboot decision without creating a marker or invoking systemd.

### Complete auto-update state before unattended reboot

A changed-release auto-update child applies the Rockchip operation under the stored `system_optimizations: enabled` policy and writes the reboot marker, but it does not reboot. The marker records the checksum present before child installation. The parent updater verifies child success, advances `config/tltg_optimized_auto_update_state.json`, then performs the final idle check and schedules the host reboot. Failed child installation does not advance the checksum and cannot schedule a reboot.

The release that first introduces this behavior may be launched by an older parent updater that cannot consume the new marker. To bridge that transition, the successful child arms a delayed transient follow-up auto-update invocation, not a reboot. The follow-up waits for the installer lock, compares the durable checksum with the marker's pre-install checksum, and schedules the reboot only after advancement. If the old parent failed before advancement, normal changed-release processing retries instead of rebooting.

An already-current auto-update run continues to reconcile enabled system optimizations. If reconciliation newly applies the Rockchip repair or finds a valid pending marker on the same boot, it schedules the reboot after reconciliation. A marker from a completed manual reboot is postflight-verified and cleared. Disabled or absent system-optimization policy does not apply the repair or create a reboot request.

### Keep benchmarks outside installer execution

The installer will not run storage benchmarks. Unit and integration tests will use the existing fake system root plus mocked `systemctl`, `findmnt`, and `mount` calls. The live metrics above remain evidence for the desired operation, not an installation acceptance threshold.

## Risks / Trade-offs

- [A future QIDI image gives `rockchip.service` required behavior] → Absence of the complete defective structural signature prevents the override from being applied.
- [A user already manages the same drop-in] → Exact desired content is accepted without ownership; any other content or symlink is preserved.
- [Remount or systemd postflight fails after writing the drop-in] → The system journal and captured preimage restore the file, daemon state, and prior mount mode.
- [Uninstall restoration recreates the vendor defect] → Restoration requires explicit operator acceptance; `--keep-system-optimizations` preserves the repair.
- [Five-trial measurements overstate fleet-wide gains] → Metrics are retained as single-machine evidence with workload, service state, ranges, and raw samples; no performance threshold is normative.
- [An unattended reboot interrupts a print started after initial preflight] → Recheck Moonraker state immediately before scheduling and fail closed while printing, paused, or unknown.
- [Auto-update reboots before recording the installed release] → Only the parent updater schedules after child success and checksum persistence.
- [The reboot scheduler fails or the host does not reboot] → Retain the boot-ID-bound marker and retry on a later direct or auto-update invocation.

## Migration Plan

1. Extend the manifest model and parser with a platform-wide Rockchip operation guarded by the complete defective unit-and-script structure.
2. Add classification, apply, postflight, rollback, reconciliation, dry-run, and uninstall restoration paths.
3. Add the durable reboot marker, post-boot verification, direct CLI request, delayed scheduler, and final idle-state guard.
4. Connect changed-release and already-current auto-update paths so release state commits before reboot scheduling.
5. Add fake-root unit tests and end-to-end system-optimization, host-reboot, and auto-update flow coverage.
6. Record the vendor defect in `openspec/observations/qidi-platform.md` and update `openspec/specs/installer-lifecycle/spec.md` through this delta.
7. Validate the installer suite and OpenSpec artifacts.
8. On an idle test printer, validate direct and auto-update marker handling, delayed reboot, post-boot cleanup, async mount, and service health.
