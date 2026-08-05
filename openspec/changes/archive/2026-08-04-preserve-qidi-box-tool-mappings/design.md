## Context

QIDI's touchscreen and Box runout flow persist logical-tool mappings in `save_variables` as `value_t0` through `value_t15`. A non-identity value is necessary while a screen-selected or automatically substituted slot is active, but the value has no provenance and can silently affect a later direct slicer print.

The installer currently creates missing mappings and treats noninteractive confirmation as approval to normalize mismatches. Optimized print end performs filament retention or unloading before starting nozzle cooldown. Cancellation and virtual-SD error handling bypass the slicer's normal end sequence.

## Goals / Non-Goals

**Goals:**

- Preserve every non-empty mapping selected before or during an active print.
- Restore identity mappings after successful optimized print completion.
- Keep missing mappings usable for the physically active Box range.
- Permit explicit idle-time normalization from Fluidd or the Klipper console.
- Keep automatic updates from rewriting non-empty vendor-managed mappings.
- Start nozzle cooldown before mapping normalization work.

**Non-Goals:**

- Determine whether a non-identity mapping originated from touchscreen selection, runout handling, or manual vendor interaction.
- Normalize mappings after cancellation, print error, power loss, or another interrupted end sequence.
- Change QIDI's Box command implementations or persist mapping ownership in installer state.

## Decisions

### Use print boundaries instead of mapping provenance

`OPTIMIZED_START_PRINT_FILAMENT_PREP` invokes start-time reconciliation before its generated commands consume vendor mappings. Reconciliation writes identity values only for missing or empty entries in `0` through `min(box_count * 4, 16) - 1` and leaves every non-empty mapping unchanged before `BOX_PRINT_START` executes. Because Klipper renders the enclosing macro before executing the helper, the enclosing template uses matching identity defaults for missing mappings.

`OPTIMIZED_END_PRINT_FILAMENT_PREP` arms a volatile reset only after retention or unload commands complete. The existing slicer sequence then invokes `OPTIMIZED_END_NOZZLE_COOLDOWN_START`, which starts thermal shutdown before consuming the arm and normalizing mappings during an unpaused active print.

A provenance tracker was rejected because `value_tN`, runout counters, and the recovered vendor interfaces do not reliably distinguish screen selection from runout replacement across restart and interruption paths.

### Separate internal reconciliation from the public command

An internal ensure macro performs missing-only start repair. An internal reset macro examines all indices `0` through `15`, rewrites each existing non-identity or empty value to `slotN`, and creates absent values only when the index is inside the active Box range. Both helpers emit `SAVE_VARIABLE` only when a value changes.

`TLTG_RESET_TOOL_MAPPINGS` delegates to the internal reset only when the stock print-state predicates all indicate idle: idle-timeout is not printing, virtual SD is inactive, and pause/resume is not paused. The cooldown macro consumes the internal end-prep arm only while an unpaused print remains active, clears the arm without resetting when the print is no longer active, and every optimized start clears a stale arm.

Embedding reset in stock `PRINT_END` was rejected because stock cancellation calls that macro and `config/klipper-macros-qd/start_end.cfg` is stock-mapped.

### Make installer alignment explicitly interactive

Every installer mode continues to create missing active mappings before alignment detection. Non-empty mismatch normalization is offered whenever `input_stream` is interactive, including a manual update. Noninteractive install and update paths preserve mismatches instead of interpreting `--yes` as alignment approval. Automatic update remains missing-only.

The mapping values stay outside `tltg_optimized_state.yaml`; uninstall does not restore them.

### Keep mapping orchestration in Klipper configuration

OrcaSlicer and QIDI Studio retain their existing start and end G-code. `OPTIMIZED_START_PRINT_FILAMENT_PREP`, `OPTIMIZED_END_PRINT_FILAMENT_PREP`, and `OPTIMIZED_END_NOZZLE_COOLDOWN_START` coordinate reconciliation through optimized Klipper macro calls and volatile macro state. The start-print path contract records the common macro-side reconciliation ordering and generated views are regenerated.

### Release as 26.08.05.1

`scripts/bump_installer_version.py 26.08.05.1` updates package metadata, compatibility inheritance, and the runtime version banner. `CHANGELOG.md` lists the mapping lifecycle and the GPLv3/third-party licensing material included by the release bundle.

## Risks / Trade-offs

- [Interrupted prints retain a stale mapping] → Preserve unresolved physical state and provide the idle-only reset command for deliberate recovery.
- [An interrupted end leaves reset state armed] → Consume the arm only during an unpaused active print, clear it without normalization when cooldown runs idle, and clear stale state at the next optimized start.
- [A successful print leaves filament physically retained in a slot whose logical mapping is reset] → Record retained physical-slot metadata before normalization; the next screen selection or direct identity tool determines whether guarded reuse is valid.
- [Repeated saved-variable writes add storage churn] → Emit writes only for missing, empty, or non-identity values.
- [A vendor application rewrites mappings after slicer end] → Keep normalization after cooldown start but before `PRINT_END`, while the active job still owns the sequence.
- [Manual update behavior diverges from automatic update] → Bind alignment solely to interactive input availability and cover both paths with installer tests.
