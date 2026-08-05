# Third-Party Notices

Except for the third-party material identified below, repository-authored content is licensed under the GNU General Public License version 3 only (`GPL-3.0-only`) in [`LICENSE`](LICENSE).

`installer/system/qidiclient-static-gifs.tar.gz` was authored by the project maintainer and is covered by the repository license.

## GPL-3.0-only components

The full GPLv3 text is available in [`LICENSE`](LICENSE) and [`LICENSES/GPL-3.0-only.txt`](LICENSES/GPL-3.0-only.txt).

### Klipper

- Source: <https://github.com/Klipper3d/klipper>
- License: GPL-3.0-only
- Paths:
  - `installer/klipper/qidi/homing.py`
  - `installer/klipper/qidi/homing-sync-reset.py`
  - `installer/tests/fixtures/source-patches/01.01.06.04/homing-sync-reset.py`

The installer payloads are modified QIDI/Klipper homing sources. `homing.py` was modified for QIDI Max 4 homing behavior and timing on 2026-07-26. `homing-sync-reset.py` carries the same modifications against the alternate QIDI source variant dated 2026-07-28. Copyright and licensing notices in those files remain in effect.

### Fluidd base configuration

- Source: <https://github.com/fluidd-core/fluidd-config>
- License: GPL-3.0-only
- Path: `config/fluidd.cfg`

### Moonraker Timelapse

- Source: <https://github.com/mainsail-crew/moonraker-timelapse>
- License: GPL-3.0-only
- Paths:
  - `config/timelapse.cfg`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/timelapse.cfg`
  - `installer/tests/fixtures/runtime/base/config/timelapse.cfg`

### Klipper Macros

- Source: <https://github.com/jschuh/klipper-macros>
- License: GPL-3.0-only
- Roots:
  - `config/klipper-macros-qd/`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/klipper-macros-qd/`
  - `installer/tests/fixtures/runtime/base/config/klipper-macros-qd/`
- Applicable filenames where present:
  - `bed_mesh.cfg`
  - `beep.cfg`
  - `fans.cfg`
  - `filament.cfg`
  - `globals.cfg`
  - `heaters.cfg`
  - `idle.cfg`
  - `kinematics.cfg`
  - `park.cfg`
  - `pause_resume_cancel.cfg`
  - `start_end.cfg`
  - `state.cfg`
  - `status_events.cfg`
  - `velocity.cfg`

These files contain QIDI and project modifications to upstream macro material. Embedded copyright and licensing notices remain in effect. QIDI-specific files not listed above remain covered by the QIDI stock-material section.

### Klipper Adaptive Meshing and Purging

- Source: <https://github.com/kyleisah/Klipper-Adaptive-Meshing-Purging>
- License: GPL-3.0-only
- Paths:
  - `config/KAMP/`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/KAMP/`

### crowsnest configuration

- Source: <https://github.com/mainsail-crew/crowsnest>
- License: GPL-3.0-only
- Paths:
  - `config/crowsnest.conf`
  - `installer/stock/qidi-max4-defaults/firmwares/*/config/crowsnest.conf`

## Vendored Python dependencies

Vendored dependency versions and artifact hashes are recorded in `installer/runtime/vendor/DEPENDENCIES.yaml`. The corresponding source is under `installer/runtime/vendor/`.

| Component | Version | License | License text |
|---|---:|---|---|
| PyYAML | 6.0.2 | MIT | [`LICENSES/PyYAML-MIT.txt`](LICENSES/PyYAML-MIT.txt) |
| Rich | 13.9.4 | MIT | [`LICENSES/Rich-MIT.txt`](LICENSES/Rich-MIT.txt) |
| markdown-it-py | 3.0.0 | MIT | [`LICENSES/markdown-it-py-MIT.txt`](LICENSES/markdown-it-py-MIT.txt) |
| markdown-it | bundled by markdown-it-py | MIT | [`LICENSES/markdown-it-MIT.txt`](LICENSES/markdown-it-MIT.txt) |
| mdurl | 0.1.2 | MIT | [`LICENSES/mdurl-MIT.txt`](LICENSES/mdurl-MIT.txt) |
| Pygments | 2.19.2 | BSD-2-Clause | [`LICENSES/Pygments-BSD-2-Clause.txt`](LICENSES/Pygments-BSD-2-Clause.txt), [`LICENSES/Pygments-AUTHORS.txt`](LICENSES/Pygments-AUTHORS.txt) |
| typing_extensions | 4.12.2 | PSF-2.0 | [`LICENSES/typing_extensions-PSF-2.0.txt`](LICENSES/typing_extensions-PSF-2.0.txt) |

Additional notices retained inside the vendored source include:

- `installer/runtime/vendor/rich/_spinners.py`: cli-spinners, MIT.
- `installer/runtime/vendor/markdown_it/_punycode.py`: Mathias Bynens and Taneli Hukkinen, MIT.
- `installer/runtime/vendor/mdurl/_parse.py`: Joyent, Inc. and other Node contributors, MIT.
- `installer/runtime/vendor/pygments/lexers/robotframework.py`: Nokia Siemens Networks Oyj, Apache-2.0; full text in [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).
- `installer/runtime/vendor/pygments/styles/solarized.py`: Solarized, MIT; full text in [`LICENSES/Solarized-MIT.txt`](LICENSES/Solarized-MIT.txt).

## OpenSpec-generated workflow files

- Source: <https://github.com/Fission-AI/OpenSpec>
- License: MIT
- License text: [`LICENSES/OpenSpec-MIT.txt`](LICENSES/OpenSpec-MIT.txt)
- Paths:
  - `.pi/prompts/opsx-*.md`
  - `.pi/skills/openspec-*/SKILL.md`

## Additional project license

`.pi/skills/qidi-firmware-release-update/SKILL.md` is licensed under the MIT License declared in that file. The full text is available in [`LICENSES/QIDI-firmware-release-update-MIT.txt`](LICENSES/QIDI-firmware-release-update-MIT.txt).

## QIDI stock material

The following paths contain QIDI firmware configuration material:

- `config/`, excluding project-authored additions and the separately identified third-party components above.
- `installer/stock/qidi-max4-defaults/firmwares/*/config/`, excluding the separately identified third-party components above.

Snapshot source: <https://github.com/thelegendtubaguy/Qidi-Max4-Defaults>

No standalone license for the remaining QIDI-authored configuration material was found in the source snapshots. The repository license does not replace or override any QIDI rights in that material. Embedded copyright and license notices remain in effect.
