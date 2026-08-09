# Qidi Max 4 Optimized

Opinionated and Optimized Klipper macros and slicer machine GCode for the QIDI Max 4.

> [!NOTE]
> If you want to help support content like this, consider subscribing over on [YouTube](https://youtube.com/@TubaMakes)!

> [!CAUTION]
> I refuse to be responsible for your printer.  PLEASE watch prints start ensuring the printer doesn't do something horrible like gouge your bed and be ready to cut power at a moment's notice.

## tl;dr
1. SSH into your printer `qidi@<your printer's ip>`.
2. Run this on your printer:
```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/Qidi-Max-4-Optimized/releases/latest/download/install-latest.sh)"
```
3. Follow the prompts
4. Use Orca and subscribe to the OrcaCloud bundle shared [here](https://cloud.orcaslicer.com/b/4c4b3b74c745).  If you're not using Orca >= 2.4.0, see [the section on slicer configs](#slicer-machine-gcode-updates).
5. Slice using the printer profile `Qidi X-Max 4 0.4 nozzle - TLTG Optimized GCode`
6. Optional: Make a copy and customize the machine profile to your liking.


### Installer Dry-Run
If you'd rather do a dry-run before committing to a full install, you can run this:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/Qidi-Max-4-Optimized/releases/latest/download/install-latest.sh)" -- --dry-run
```

### Automatic Updates

The installer asks whether to enable hourly automatic optimized config updates before asking whether to restart Klipper. Auto-updates use a system-level systemd timer. Enabling auto-updates requires sudo once to install `/etc/systemd/system/tltg-optimized-auto-update.service` and `/etc/systemd/system/tltg-optimized-auto-update.timer`; the installer uses QIDI's public default sudo password (`qiditech`) unless the environment variable `TLTG_OPTIMIZED_SUDO_PASSWORD` is set, then prompts for a password if the initial sudo attempt fails.

If those systemd units already exist from a dev installer, a release install refreshes them to the current `~/tltg-optimized-macros` bundle, clears auto-update URL environment overrides, seeds the latest release checksum when GitHub is reachable, and re-enables/restarts the timer.

Each hourly run checks the latest GitHub release checksum, skips while the printer is printing or paused, and then runs the normal installer with preflight checks and auto-approval.

Disable auto-updates:

```bash
~/tltg-optimized-macros/auto-update.sh --disable-systemd
```

Run one auto-update check manually:

```bash
~/tltg-optimized-macros/auto-update.sh --run
```

### QIDI Box temperature from Fluidd

The installer adds `TLTG_SET_BOX_TEMP`, a macro for setting the QIDI Box heater target because Qidi's Fluidd config is incapable of setting `heater_box1` correctly.

Use:

```gcode
TLTG_SET_BOX_TEMP BOX=1 TARGET=45
```

Use `TARGET=0` to turn the box heater off:

```gcode
TLTG_SET_BOX_TEMP BOX=1 TARGET=0
```

The macro appears in Fluidd's Macros panel after install and Klipper restart. If the panel is not visible, edit the Fluidd layout and enable the Macros panel.

![Fluidd TLTG_SET_BOX_TEMP macro](.github/images/fluidd-tltg-set-box-temp-macro.png)

### Helpful Klipper tools

After install and Klipper restart, the optimized macro set includes:

```gcode
TLTG_PROBE_ACCURACY_CENTER
TLTG_CORNER_BED_SCREW_CHECK
SCREWS_TILT_CALCULATE
TLTG_RESET_TOOL_MAPPINGS
```

`TLTG_PROBE_ACCURACY_CENTER [SAMPLES=20]` homes, moves to `X195 Y195 Z10`, and runs Klipper `PROBE_ACCURACY`.

`TLTG_CORNER_BED_SCREW_CHECK` homes, runs `Z_TILT_ADJUST`, and runs `SCREWS_TILT_CALCULATE`.

`TLTG_RESET_TOOL_MAPPINGS` restores QIDI Box tool-to-slot identity mappings while the printer is idle. It is rejected while printing or paused.

### Print-start bed mesh

Optimized print start calibrates a fresh adaptive KAMP mesh by default. To reuse an existing Klipper bed-mesh profile for every optimized start, save its exact name from the Klipper console:

```gcode
SAVE_VARIABLE VARIABLE=tltg_start_bed_mesh_profile VALUE='"default"'
```

Restore fresh adaptive calibration by saving an empty value:

```gcode
SAVE_VARIABLE VARIABLE=tltg_start_bed_mesh_profile VALUE='""'
```

The setting applies to existing sliced files and both slicer packs. The console reports whether start preparation is loading the named profile or calibrating a fresh adaptive mesh. A configured profile must already exist; Klipper stops print preparation if it cannot load the name.

### Filament runout sensor

```gcode
TLTG_FILAMENT_SENSOR ENABLE=0
TLTG_FILAMENT_SENSOR ENABLE=1
```

`ENABLE=0` keeps toolhead filament-sensor events and external-spool runout status reporting active while suppressing automatic external-spool pausing. `ENABLE=1` restores automatic external-spool pausing. The setting resets enabled after a Klipper restart. QIDI Box runout handling remains enabled in both modes.

> [!WARNING]
> With `ENABLE=0`, ignore filament-runout warnings on the printer screen. Do not interact with those warnings; the print continues and the warning remains only as sensor-event status.

The console identifies the toolhead sensor trip and the active pause policy.

![Filament sensor console output](.github/images/tltg-filament-sensor-console.png)

### Slicer Machine GCode Updates

> [!TIP]
> If you're using Orca >= 2.4.0, use the OrcaCloud [shared bundle you can subscribe to](https://cloud.orcaslicer.com/b/4c4b3b74c745)!  It will let you get future updates in your slicer easily and you don't have to manually copy/paste anything!

You will need to manually copy the machine GCode to your slicer of choice to take advantage of the optimized path.  The stock print path remains in place for backwards compatibility, safety, and general user happiness :)

Use the pack that matches your slicer. The two packs are functionally aligned, but their placeholder syntax is different due to variable type differences.
   - OrcaSlicer: `orcaslicer_gcode/`
   - QIDI Studio: `qidistudio_gcode/`

Use the pack that matches your slicer. The two packs are functionally aligned, but their placeholder syntax is different.

## Uninstall

If `~/tltg-optimized-macros/` is still present on the printer:

```bash
~/tltg-optimized-macros/install.sh --uninstall --plain
```

You can also run uninstall by fetching the latest script directly from the web:

```bash
/bin/bash -c "$(curl -fsSL https://github.com/thelegendtubaguy/Qidi-Max-4-Optimized/releases/latest/download/install-latest.sh)" -- --uninstall
```

## If something goes wrong

Read the installer output first. The installer stops before writing when firmware detection, preflight, printer state, or free-space checks fail.

Installer-created backup `.zip` files are stored under `/home/qidi/printer_data/` with `tltg-optimized-macros-before-optimize-...zip` and `tltg-optimized-macros-before-uninstall-...zip` labels.

You can restore interactively when SSH'd into the printer.

```bash
cd ~/tltg-optimized-macros && ./restore.sh
```

Restore a specific backup:

```bash
cd ~/tltg-optimized-macros && ./restore.sh --backup /home/qidi/printer_data/<backup-name>.zip
```

If restore completed and the recovery sentinel is still present, clear it with:

```bash
cd ~/tltg-optimized-macros && ./install.sh --clear-recovery-sentinel
```

## Testing

See [TESTING](TESTING.md) to help validate releases.

## Development

For development documentation, see [DEVELOPMENT](DEVELOPMENT.md).

## License

Repository-authored content is licensed under [GPLv3](LICENSE). Third-party components and license texts are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
