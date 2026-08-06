## Context

Firmware `01.01.06.03` and `01.01.06.04` configure `[heater_fan hotend_fan] tachometer_poll_interval` as `0.0015 s`; firmware `01.01.06.05` configures it as `0.0005 s`. Klipper requires the interval to remain below `30 / (tachometer_ppr × maximum_rpm)` with margin. A controlled `140 °C` hotend test on firmware `01.01.06.05` measured a `13,553 RPM` startup maximum and a stable `12,990–13,135 RPM` range at `tachometer_ppr: 2`.

The stock-mapped `config/printer.cfg` cannot carry optimized tuning directly. Firmware-specific stock values must remain represented as guarded expected values in `installer/package.yaml`, with installer state retaining the preimage needed for drift-safe uninstall.

## Goals / Non-Goals

**Goals:**

- Provide reliable tachometer sampling margin for the measured hotend fan speed.
- Reduce THR GPIO polling relative to firmware `01.01.06.05` without returning to the undersampled pre-`.05` value.
- Apply and reverse the value through existing firmware-guarded installer ownership.
- Preserve QIDI fan control and fan-failure shutdown semantics.

**Non-Goals:**

- Change fan PWM, commanded speed, `tachometer_ppr`, RPM report cadence, or QIDI watchdog behavior.
- Attribute or mitigate the separate Linux upstream USB-hub reset incidents.
- Modify stock snapshots or `config/printer.cfg` directly.
- Install the change on a printer as part of implementation.

## Decisions

### Use a `0.00075 s` polling interval

At two pulses per revolution, `0.00075 s` has a Klipper sampling boundary of `20,000 RPM`, approximately `47.6%` above the observed `13,553 RPM` maximum. It polls at approximately `1,333 Hz`, reducing THR tachometer callbacks by one third relative to `.05`'s `2,000 Hz` setting.

`0.001 s` was rejected because its `15,000 RPM` boundary leaves approximately `10.7%` speed margin. `0.0005 s` remains valid but provides a `30,000 RPM` boundary unsupported by observed need.

### Apply one desired value through firmware-specific guarded variants

A new `patches.set_options` entry will target `config/printer.cfg`, section `heater_fan hotend_fan`, option `tachometer_poll_interval`. Firmware `.03` and `.04` variants will require stock `0.0015`; firmware `.05` will require stock `0.0005`; all variants will set `0.00075`.

The existing patch ledger supplies upgrade ownership, drift rejection, transaction rollback, and uninstall restoration. No direct stock-mapped configuration edit is required.

### Preserve the complete tachometer safety path

The patch changes only the polling interval. `tachometer_pin: THR:PB5`, `tachometer_ppr: 2`, the one-second `FrequencyCounter` sample period, full-speed heater-fan control, and QIDI's ten-second zero-RPM shutdown path remain unchanged.

### Validate manifests and lifecycle behavior

Installer tests will assert variant selection and stock-to-desired transitions for all supported firmware, upgrade from a prior package whose ledger does not own this option, drift preservation, and restoration of each firmware's recorded stock value. Known-version validation and the full installer core suite will cover manifest consistency and lifecycle regressions.

## Risks / Trade-offs

- [A replacement fan exceeds `20,000 RPM`] → Firmware baseline review must remeasure the fan and select a smaller interval before support is added.
- [A non-50% tachometer waveform needs more sampling margin] → The selected boundary retains substantially more margin than `0.001 s`; runtime RPM should remain stable in controlled validation.
- [Existing installations have an unowned stock value after package upgrade] → Upgrade tests must prove the new guarded patch recognizes that firmware's stock value and records ownership.
- [RPM evidence depends on the configured two-pulse assumption] → Preserve QIDI's `tachometer_ppr: 2`; physical optical-tach validation remains outside this change.

## Migration Plan

1. Add the guarded set-option variants and bump package metadata.
2. Update installer fixtures and lifecycle tests for install, upgrade, drift, and uninstall.
3. Update the optimized behavior specification and QIDI runtime observation.
4. Run manifest, installer-core, OpenSpec, and bundle smoke validation.
5. Roll back by uninstalling while the managed value remains unchanged; the patch ledger restores the firmware-specific preimage.

## Open Questions

None.
