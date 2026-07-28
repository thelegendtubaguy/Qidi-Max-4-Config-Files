# QIDI platform observations

## Firmware and service behavior

- Supported QIDI Max 4 stock comparisons use `https://github.com/thelegendtubaguy/Qidi-Max4-Defaults`.
- Firmware identity is exposed in `/home/qidi/update/firmware_manifest.json` at `SOC.version`.
- **Runtime-confirmed:** [GitHub issue #71](https://github.com/thelegendtubaguy/Qidi-Max-4-Optimized/issues/71) reported firmware `01.01.06.04` with `/home/qidi/klipper/klippy/extras/homing.py` SHA-256 `0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761`. The attached file differs from the previously captured `01.01.06.04` SHA-256 `ff0439f8b9e702537f66c16508f7b0a137b27cff51eb653aa951172d3e5184a0` only by a conditional `endstop_sync_reset()` block before endstop `home_start()` setup.
- Unresolved: `Qidi-Max4-Defaults` firmware snapshots currently contain `config/` and do not establish which `01.01.06.04` distribution or update path supplies each `homing.py` variant.
- QIDI firmware packages can replace `/home/qidi/QIDI_Client`, restore animated touchscreen assets, and enable/start `algo_app.service`; opted-in system hardening therefore requires reconciliation rather than one-time migration.
- Firmware `01.01.06.04` exposes the touchscreen through `qidi-client.service` with executable `/home/qidi/QIDI_Client/bin/qidiclient`.
- Firmware `01.01.06.04` exposes AI detection through `algo_app.service`, executable `/usr/local/bin/algo_app/main`, and port `9010`; the observed `/version` response reported `sw_version=1.1.0`.
- Observed `/config` flags were false for general detection, spaghetti detection, foreign-object detection, and related checks on the captured machine.

## Rockchip first-boot service and root mount

- `/proc/device-tree/compatible` on the observed Max 4 identifies RK3308, while `/etc/init.d/rockchip.sh` assigns `CHIPNAME="rk3208"` for that hardware.
- `/lib/systemd/system/rockchip.service` runs `/etc/init.d/rockchip.sh`; neither file was owned by a Debian package on the observed image.
- The script runs under `#!/bin/bash -e`, remounts `/` with `sync`, then calls package installation before creating `/usr/local/first_boot_flag`.
- The `rk3208` value has no matching package case. `apt` rejects `/libmali-**-x11*.deb`, the script exits with status `100`, and `/usr/local/first_boot_flag` remains absent. The service repeats the synchronous remount on later boots.
- A systemd drop-in replacing the effective `ExecStart` with `/bin/true`, followed by `systemctl daemon-reload`, a no-op service start, and `mount -o remount,rw,async /`, produced service result `success`, status `0`, and root options without `sync`.
- After reboot with that drop-in installed, the root mount remained asynchronous; `qidi-client.service`, `klipper.service`, and `moonraker.service` were active and printer state returned to `standby`.

### Controlled root-mount measurements

Measurements used Linux `5.10.160`, Python `3.9.2`, and ext4 with `19,766,173,696` bytes available. `qidi-client.service`, `klipper.service`, and `moonraker.service` were inactive for every trial. Each mode used one excluded warm-up and five measured trials. Batch writes include a final `fsync`; metadata timings include directory `fsync` after creation and deletion.

| Workload | `sync` median (range) | asynchronous median (range) | Latency reduction | Speedup |
|---|---:|---:|---:|---:|
| 512 × 4 KiB writes, 2 MiB total | 1510.856 ms (1478.720–1541.888) | 56.153 ms (55.003–56.659) | 96.3% | 26.91× |
| 8 × 256 KiB writes, 2 MiB total | 74.072 ms (71.087–78.555) | 49.573 ms (48.481–51.603) | 33.1% | 1.49× |
| 64 × 4 KiB writes with per-write `fsync` | 203.015 ms (185.096–218.513) | 192.629 ms (177.627–197.354) | 5.1% | 1.05× |
| Create and remove 64 files | 706.394 ms (669.849–711.707) | 96.790 ms (91.821–101.043) | 86.3% | 7.30× |

The 4 KiB batch increased from `1.324 MiB/s` to `35.617 MiB/s`; the 256 KiB batch increased from `27.001 MiB/s` to `40.345 MiB/s`. The per-write `fsync` control changed by 5.1% because both modes explicitly requested durability for each write.

## Touchscreen AI state

- `Spaghetti Detection` and `Foreign Object Detection` toggles under `Settings -> Printing Options` are qidiclient UI state, not `algo_app.service` enablement state.
- Disabling `algo_app.service` does not clear those toggles.
- Changing those toggles did not re-enable the disabled service, restore port `9010`, or restore its API in the observed runtime.

## Stock integration constraints

- `config/fluidd.cfg` is printer-owned and read-only for this repository.
- Stock QIDI macro names are consumed by Fluidd, QIDI Client, and vendor modules even when optimized wrappers supersede their behavior.
- `G4 P...` adds fixed dead time; `M400` waits only for queued motion.
- Stock globals with no repository caller may still be consumed by vendor components.
- Config-confirmed QIDI Max 4 machine profiles installed with OrcaSlicer and QIDI Studio move to absolute `Z10` after `M1002 A1`, then apply `G92_ Z{10 - ((nozzle_temperature_initial_layer[initial_tool] - 130) / 14 - 5.0) / 100}` before first-layer priming.
