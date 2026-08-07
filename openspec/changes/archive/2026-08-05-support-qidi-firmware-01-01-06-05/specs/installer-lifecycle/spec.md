## MODIFIED Requirements

### Requirement: Firmware-gated baseline management
The installer SHALL apply configuration changes or legacy stock restoration only from the validated baseline selected for the detected firmware, reject unsupported firmware before state reads, and reject unmatched or invalid baseline data before backup creation or live writes.

#### Scenario: Supported firmware selects one baseline
- **WHEN** `/home/qidi/update/firmware_manifest.json SOC.version` is `01.01.06.03`, `01.01.06.04`, or `01.01.06.05`
- **THEN** firmware validation passes
- **AND** every active configuration target selects no more than one variant for that firmware
- **AND** each declared target has at least one variant using supported firmware

#### Scenario: Unsupported or unreadable firmware is rejected
- **WHEN** firmware cannot be read or is not listed in `installer/package.yaml firmware.supported`
- **THEN** installation stops before reading `config/tltg_optimized_state.yaml`, creating a backup, or writing live files

#### Scenario: Firmware-specific expectations are enforced
- **WHEN** guarded preflight runs for supported firmware
- **THEN** configuration expectations come from that firmware's manifest variant
- **AND** managed-source expectations come from the firmware and matching live stock preimage
- **AND** targets belonging only to another firmware do not affect preflight

#### Scenario: Current 01.01.06.04 stock baseline is selected
- **WHEN** firmware `01.01.06.04` is detected
- **THEN** its baseline represents QIDI defaults commit `5da5767379ac22fc4fbe1606ec7093ce056229ae`
- **AND** X/Y closed-loop stock values include `query_cycle:10`, `trigger_current:400`, `trigger_time:2`, and `trigger_speed:50`
- **AND** `_km_idle_timeout` saves `saved_extruder_temp` on `RESUME_PRINT`
- **AND** `Chamber_Thermal_Protection_Sensor max_temp` is `170`
- **AND** official filament `[fila25]` uses `PA6-CF` for both `filament` and `type`

#### Scenario: Current 01.01.06.05 stock baseline is selected
- **WHEN** firmware `01.01.06.05` is detected
- **THEN** its baseline represents QIDI defaults commit `c75c0b662d1d4fd2a7dd19e49843b91e6544a1ed`
- **AND** guarded configuration targets use the same stock and desired values as their `.04` variants
- **AND** the hotend fan `tachometer_poll_interval` is `0.0005`
- **AND** `[smart_output_pin polar_cooler]` and `[smart_output_pin beeper]` are inactive while their underlying output pins remain available
- **AND** `M4031` establishes Z position, moves Z by `1 mm`, and restores absolute positioning before enabling and driving the Z steppers
- **AND** stock `homing.py` SHA-256 is `0310d9ed0a838b2a7ecff8cd2ec15488b1ae3d8f165a458addd16d8366a60761`

#### Scenario: Legacy manual installation is migrated
- **WHEN** legacy optimized markers exist without valid installer state
- **THEN** an active or paused print blocks migration
- **AND** accepted migration backs up `config/`, restores only the detected firmware's validated stock snapshot, removes the legacy optimized tree, and restarts `qidi-client.service`
- **AND** `config/MCU_ID.cfg`, `config/box.cfg`, `config/fluidd.cfg`, `config/saved_variables.cfg`, and direct `config/KAMP` symlinks are preserved
- **AND** missing or invalid snapshot data fails before stock files are overwritten
