## ADDED Requirements

### Requirement: Hotend fan tachometer sampling margin
Optimized configuration SHALL use a guarded hotend fan tachometer polling interval that covers the measured fan speed with substantial sampling margin while preserving QIDI fan control and fan-failure shutdown behavior.

#### Scenario: Supported firmware receives the optimized interval
- **WHEN** installation processes `[heater_fan hotend_fan]` from a supported firmware baseline
- **THEN** stock `tachometer_poll_interval` value `0.0015` on firmware `01.01.06.03` and `01.01.06.04`, or `0.0005` on firmware `01.01.06.05`, is changed to `0.00075`
- **AND** the guarded patch records the firmware-specific preimage for drift-safe uninstall

#### Scenario: Tachometer sampling retains measured-speed margin
- **WHEN** the hotend fan operates at the observed maximum of `13,553 RPM` with `tachometer_ppr: 2`
- **THEN** the `0.00075 s` polling interval retains a `20,000 RPM` sampling boundary under Klipper's `30 / (tachometer_ppr × maximum_rpm)` constraint
- **AND** `tachometer_pin`, `tachometer_ppr`, one-second RPM reporting, fan output, and the QIDI zero-RPM watchdog remain unchanged

#### Scenario: Uninstall restores the firmware preimage
- **WHEN** uninstall processes an unchanged installer-owned hotend fan polling value
- **THEN** it restores the recorded firmware-specific stock value
- **AND** a user-modified live value is preserved and reported as drift
