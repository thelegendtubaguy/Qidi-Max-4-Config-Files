# QIDI Box evidence and unresolved behavior

Runtime QIDI Box evidence applies to QIDI Max 4 firmware `01.01.06.04`; `01.01.06.05` findings are config-confirmed, harness-recovered, or static-recovered where explicitly labeled.

## Evidence inventory

| Evidence | Scope | Limitation |
|---|---|---|
| 2026-05-07 live one-Box config and non-motion Moonraker captures | Include graph, saved state, status schema, heater/sensor values, query/RFID/autofeed idle command effects | No feeder motion, valid RFID result, anti-wrap event, retry, or failure path occurred. |
| 2026-06-13 live two-Box topology refresh | Two MCU/Box object graph, `box_count=2`, missing second-Box tool mappings, slot16 fallback | No physical second-Box load/unload operation was captured. |
| Captured `box.cfg`, `box_config.py`, `officiall_filas_list.cfg`, and qidiclient strings | Hardware pins, generated topology, metadata maps, UI/object names, detection/update strings | Strings prove references, not execution. |
| Fake Klipper harnesses for compiled modules | Script fragments, constants, command formats, saved-variable writes, branch candidates | Fake objects can select invalid branches or omit required context. |
| Aarch64 symbols, strings, and disassembly | Function boundaries, ownership, error strings, unresolved target locations | Cython source-level DWARF was absent; instruction flow alone is not a reliable high-level predicate source. |
| `01.01.06.04`/`.05` ARM64 Python 3.9 differential execution | Changed command traces, flow selection, mapping scan, and virtual-SD lookup for identical controlled inputs | Fake Klipper objects do not prove physical outcomes or all native branch predicates. |
| `01.01.06.05` `save_variables.py` source | Missing metadata-key defaults | Persisted runtime values bypass these defaults. |

Captured extension modules were aarch64 Cython shared objects with debug-info markers but no useful Cython line/variable DWARF. Static function address/size inventories are intentionally not retained because readable harness/runtime behavior supersedes them.

## Evidence precedence

1. Controlled live before/after state and logs.
2. Captured stock config or generated Python source.
3. Fake-harness scripts, constants, and protocol payloads.
4. Static symbols, names, strings, and disassembly.

A command returning `ok` proves acceptance in that captured state; it does not prove a successful physical operation. An unchanged already-matching state does not prove that a command is generally side-effect free.

## Resolved conflicts

- Live loaded-slot RFID commands were accepted without `QDE_004_011`; the conflicting fake-harness guard is not treated as live behavior.
- `slot16` can reach `EXTRUDER_LOAD` when a tool mapping is absent; any special behavior is downstream and remains unresolved.
- **Harness-recovered:** `BOX_PRINT_START` writes saved-variable state; prior uncertainty about the emitted writes is obsolete, while live mutation remains unresolved.
- Generated and runtime sensor names `heater_temp_a_boxN` / `heater_temp_b_boxN` supersede the incomplete `box1_env` inventory.
- Heater target may be active while `dry_state=0`; `heater_generic heater_boxN.target` is the heater-state source.
- The 2026-06-13 two-Box topology supersedes one-Box assumptions about maximum active slots but does not invalidate the earlier loaded-slot state/schema capture.
- **Harness-recovered for `.05`:** `switch_next_slot()` scans `value_t0` through `value_t15` instead of relying on `get_key_by_value(...)`.
- **Harness-recovered for `.05`:** external-spool `LOAD` with slot `16`, filament present, and empty `last_load_slot` selects flow `6` instead of flow `0`.
- **Static-recovered for `.05`:** public Box classes, method signatures, defaults, and recovered `QDE_004_*` code sets are unchanged; this does not establish unchanged internal behavior.

## Unresolved behavior

### Feeder and print start

- Exact live `BOX_PRINT_START` predicates for same-slot reuse, changed slot, unsynced state, cut-before-unload, and direct-feed `slot16`.
- Live `EXTRUDER_LOAD`, `EXTRUDER_UNLOAD`, and `SLOT_UNLOAD` sensor gates, retries, state writes, and failure recovery.
- Exact `.05` `EXTRUDER_LOAD` retry threshold/order around `diff_mv`, `box_autofeed`, `retry_success`, and `limit_a_state`.
- Real slot/extruder synchronization movement, unbind behavior, prompt movement, and `.05` `switch_next_slot()` auto-reload movement after target selection.
- Live task-queue normalization before `_decide_flow_id()`, flow progress semantics, and completion predicates.

### RFID and metadata

- Valid QIDI tag payload bytes and `status` interpretation.
- Successful `multi_color_controller.rfid.results` schema.
- Reader order, four-slot-to-two-chip-select multiplexing, and slot-to-reader mapping.
- Raw tag conversion into filament/color/vendor IDs.
- Whether `BOX_PRINT_START` initiates RFID reads directly.
- Whether failed/no-result reads preserve prior metadata in every state; they did so in the observed loaded-slot capture.

### Autofeed and anti-wrap

- `MCB_STATE`, `MCB_DONE`, and `MCB_ERROR` payload schemas.
- `MCB_AUTO_START` physical behavior and active abort semantics.
- `limit_a_event`, wrapping count/event gate, `QDE_004_013`, and pause behavior.
- Whether pressure-sensor status reflects all autofeed/anti-wrap states.

### Recovery, dryer, and failures

- Live `TRY_MOVE_AGAIN`, `TRY_RESUME_PRINT`, `RESUME_PRINT_1`, `AUTO_RELOAD_FILAMENT`, `RELOAD_ALL`, and `TIGHTEN_FILAMENT` behavior.
- Exact live predicates for each `QDE_004_*` code.
- Dryer timer/state transitions and their relationship to direct heater targets.
- Box button, tool-change, interruption, and power-loss resume transitions.

## Validation boundary

- Status queries and documented idle commands may be recaptured without motion.
- Heater/dryer validation requires temperature and target monitoring.
- RFID validation requires a known tagged QIDI spool aligned to a confirmed reader.
- Autofeed, feeder movement, retry, resume, and failure-path validation require operator-approved physical preflight and live stop capability.
- Changes based only on fake-harness predicates must remain guarded until live evidence exists.
- `.05` load/retry, slot-selection, external-spool, and unload findings may guide compatibility tests but do not authorize live motion tests or repository replacements for vendor modules.
