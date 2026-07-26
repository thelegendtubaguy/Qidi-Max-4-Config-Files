# QIDI platform observations

## Firmware and service behavior

- Supported QIDI Max 4 stock comparisons use `https://github.com/thelegendtubaguy/Qidi-Max4-Defaults`.
- Firmware identity is exposed in `/home/qidi/update/firmware_manifest.json` at `SOC.version`.
- QIDI firmware packages can replace `/home/qidi/QIDI_Client`, restore animated touchscreen assets, and enable/start `algo_app.service`; opted-in system hardening therefore requires reconciliation rather than one-time migration.
- Firmware `01.01.06.04` exposes the touchscreen through `qidi-client.service` with executable `/home/qidi/QIDI_Client/bin/qidiclient`.
- Firmware `01.01.06.04` exposes AI detection through `algo_app.service`, executable `/usr/local/bin/algo_app/main`, and port `9010`; the observed `/version` response reported `sw_version=1.1.0`.
- Observed `/config` flags were false for general detection, spaghetti detection, foreign-object detection, and related checks on the captured machine.

## Touchscreen AI state

- `Spaghetti Detection` and `Foreign Object Detection` toggles under `Settings -> Printing Options` are qidiclient UI state, not `algo_app.service` enablement state.
- Disabling `algo_app.service` does not clear those toggles.
- Changing those toggles did not re-enable the disabled service, restore port `9010`, or restore its API in the observed runtime.

## Stock integration constraints

- `config/fluidd.cfg` is printer-owned and read-only for this repository.
- Stock QIDI macro names are consumed by Fluidd, QIDI Client, and vendor modules even when optimized wrappers supersede their behavior.
- `G4 P...` adds fixed dead time; `M400` waits only for queued motion.
- Stock globals with no repository caller may still be consumed by vendor components.
