## Context

`OPTIMIZED_START_PRINT_FILAMENT_PREP` and `OPTIMIZED_END_PRINT_FILAMENT_PREP` currently read `keep_loaded_between_prints` from `_tltg_optimized_globals`, where the installer-managed default is `True`. Operators can edit that managed file or set the macro variable temporarily, but installer updates overwrite the file and Klipper restarts discard runtime changes.

Klipper already persists operator-owned preferences in `saved_variables.cfg` and exposes them through `printer.save_variables.variables`. The existing non-retention branch clears retained metadata and calls `OPTIMIZED_UNLOAD_FILAMENT`, which delegates cutting and unloading to QIDI Box commands while preserving optimized end sequencing.

## Goals / Non-Goals

**Goals:**

- Make retention a persistent saved preference initialized to the current enabled default.
- Use one saved preference consistently at print start and normal print completion.
- Preserve all existing physical-state guards before retaining or reusing Box filament.
- Initialize the absent preference during installation while preserving subsequent operator control.
- Preserve existing slicer entrypoints and cut-and-unload commands.

**Non-Goals:**

- Change external-spool end behavior.
- Change cancellation or interrupted-print recovery.
- Modify stock-mapped macros, QIDI Box commands, or slicer G-code.
- Add another setter macro around Klipper's native `SAVE_VARIABLE` command.
- Record the preference in the installer ownership ledger or remove it during uninstall.

## Decisions

### Read `tltg_keep_loaded_between_prints` directly from saved variables

Both optimized start and end helpers read `printer.save_variables.variables.tltg_keep_loaded_between_prints|default(0)|int == 1`. Only integer value `1` enables retention. Missing, zero, or other integer values select the non-retention path.

The existing `_tltg_optimized_globals.keep_loaded_between_prints` value is removed so there is one policy authority. Keeping it as a fallback was rejected because an absent saved variable would continue retention and conflict with the required stock-style default.

### Initialize the absent preference without taking ongoing ownership

Install and update add `tltg_keep_loaded_between_prints = 1` only when the key is absent. Any existing value, including `0`, is preserved. The write participates in the install rollback journal but is excluded from the installed-state ownership ledger, so uninstall leaves the preference intact.

Always forcing `1` was rejected because it would silently undo an operator's disabled setting during every update. Never initializing the key was rejected because installed users should retain the current optimized default while raw runtime absence remains fail-safe.

The README documents direct console operations:

```gcode
SAVE_VARIABLE VARIABLE=tltg_keep_loaded_between_prints VALUE=1
SAVE_VARIABLE VARIABLE=tltg_keep_loaded_between_prints VALUE=0
```

A dedicated setter macro was rejected because Klipper already provides the persistent interface and no additional runtime validation or safety transition is required.

### Apply the same preference at start and end

Print end retains state only when the saved preference equals `1` and the existing Box-enabled, valid-tool, synchronized-slot, and filament-present checks pass. Otherwise it clears retained metadata and invokes the existing optimized unload wrapper, which calls QIDI's cutter and unload commands.

Print start allows the retained-filament reuse branch only when the same preference equals `1` and all existing slot, synchronization, filament, material, and vendor checks pass. This prevents stale retained metadata from enabling reuse after an operator disables retention or upgrades with no preference configured.

### Keep slicer paths unchanged and installer mutation bounded

The preference is evaluated inside optimized Klipper macros, so existing sliced files and both slicer packs receive the behavior without G-code changes. Installer runtime uses the existing atomic saved-variable and rollback path only to create the missing default; it does not normalize or replace an existing value.

## Risks / Trade-offs

- [A failed install leaves a newly created preference] → Track `saved_variables.cfg` in the existing rollback journal before initialization.
- [Retained metadata remains from an older version] → Gate start reuse on the new preference and clear readiness during normal start preparation.
- [An operator stores an unsupported value] → Enable retention only for integer `1`; all other integer values fail toward cut-and-unload behavior.

## Migration Plan

Install or update creates `tltg_keep_loaded_between_prints = 1` when absent. Existing `0` values remain disabled, and existing `1` values remain enabled. Operators can switch modes with `SAVE_VARIABLE` without changing slicer G-code.

Rollback restores the pre-install `saved_variables.cfg` bytes if the transaction fails. Version rollback restores the prior optimized macro global and ignores the saved value. Uninstall leaves `saved_variables.cfg` intact.
