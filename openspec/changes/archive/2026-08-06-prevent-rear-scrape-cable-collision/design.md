## Context

Fresh-Box and external-spool print starts duplicate the same rear-bed scrape sequence in `installer/klipper/tltg-optimized-macros/filament.cfg`. Both move from the chute directly to X188/Y395, which can leave the articulated cable-chain mount rotated toward the rear enclosure. Controlled dry runs established that a forward move followed by a full-width X380 traverse rotates the mount away from the enclosure before the toolhead returns to the scrape area.

The active Z-home helper already owns XY travel speed and independent X/Y randomization through optimized-only globals. The change requires new values rather than new homing logic.

## Goals / Non-Goals

**Goals:**

- Use the validated cable-chain orientation path for every optimized rear-bed scrape.
- Keep the X380-to-X188 return at 400 mm/s and use the final chute-approach feed rate only for the rearward Y move into the scrape start.
- Keep scrape motion within Y392–Y395.
- Remove duplicate optimized scrape motion.
- Set Z-home center travel to 750 mm/s and independent X/Y randomization to ±10 mm.
- Publish the behavior as installer version `26.08.06.1`.

**Non-Goals:**

- Change stock-mapped `config/klipper-macros-qd/qd_macro.cfg` behavior.
- Redefine vendor `CLEAR_OOZE` or `CLEAR_FLUSH` commands.
- Change retained-filament reuse, which does not perform rear-bed scraping.

## Decisions

### Centralize optimized rear-bed scraping

A private optimized macro will own the orientation approach, scrape motion, and exit. Both `OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE` and the fresh-Box start branch will call it after their final chute cleanup.

Keeping two edited copies was rejected because path and coordinate drift would recreate different collision behavior between filament sources.

### Force cable-chain orientation before entering the scrape area

From the final chute position, the helper will:

1. Move 50 mm forward in Y, traverse to X380, and return to X188 at `F24000` (400 mm/s).
2. Move rearward to Y392 at the final chute-approach feed rate.
3. Lower to the existing scrape Z and execute the existing X/circular scrape pattern with Y travel changed to +3/-3 mm.

The separate forward and full-width X moves were selected over a diagonal approach because physical testing showed the shorter diagonal did not rotate the cable-chain mount reliably.

### Share the final chute-approach speed

The optimized-only globals will define the final chute-approach feed rate. `OPTIMIZED_MOVE_TO_TRASH` and the rear-scrape helper's rearward Y move will consume the same value, preventing the final push into the scrape position from diverging from the chute approach.

### Preserve vendor cleanup ownership

The implementation will continue calling vendor `CLEAR_OOZE` and `CLEAR_FLUSH` where the Box stack is available. `CLEAR_OOZE` already contains nozzle-wiper and silicone-finger-brush motion; the optimized installer will not reproduce or override that vendor sequence.

### Adjust Z-home globals without changing homing control flow

`move_to_z_travel_speed_xy` will become `45000` mm/min and `z_home_randomize_radius` will become `10`. `_OPTIMIZED_MOVE_TO_Z_HOME_POINT` will continue drawing X and Y offsets independently around X195/Y195.

## Risks / Trade-offs

- **X380 leaves 12 mm to the configured X maximum** → Keep the waypoint explicit and cover it with macro contract tests.
- **The 400 mm/s orientation move increases motion energy** → Retain the existing 10,000 mm/s² travel acceleration and perform the move with the bed/nozzle clear before lowering to scrape Z.
- **Vendor cleanup can leave a different X coordinate** → Anchor the orientation with absolute Y and X waypoints rather than depending on the cleanup endpoint.
- **Installer upgrades could partially apply versioned files** → Use the existing version bump and guarded installer metadata workflow, then run installer core and known-version validation.
