## Why

The Max 4 hotend fan reaches approximately `13,553 RPM`, above the reliable range of the `0.0015 s` tachometer polling shipped through firmware `01.01.06.04`. Firmware `01.01.06.05` reduces the interval to `0.0005 s`, which restores measurement margin but triples THR tachometer polling when a less aggressive interval can cover the measured fan speed.

## What Changes

- Guard the `[heater_fan hotend_fan]` tachometer polling interval at `0.00075 s` across supported firmware versions.
- Preserve the stock fan output, two-pulse-per-revolution conversion, one-second RPM reporting, and QIDI fan-failure shutdown behavior.
- Restore each firmware's stock polling interval during uninstall.
- Record the measured startup and steady-state hotend fan RPM evidence used to select the interval.
- Validate installation, upgrade, and uninstall behavior against each supported stock baseline.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `optimized-printer-behavior`: Hotend fan tachometer monitoring uses a guarded polling interval with sufficient margin for measured fan speed while avoiding unnecessarily aggressive THR polling.

## Impact

- `installer/package.yaml` guarded `config/printer.cfg` patch variants and package version metadata.
- Installer fixtures and core tests covering firmware-specific expected and restored values.
- `openspec/specs/optimized-printer-behavior/spec.md` cooling behavior.
- `openspec/observations/qidi-platform.md` runtime fan-speed evidence and firmware baseline difference.
