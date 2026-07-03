;===== PRINT_PHASE_INIT =====
SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]
SET_PRINT_MAIN_STATUS MAIN_STATUS=print_start
M220 S100
M221 S100
DISABLE_ALL_SENSOR
M1002 R1
M107
CLEAR_PAUSE
M140 S[bed_temperature_initial_layer_single]
M141 S[chamber_temperatures]
G29.0
OPTIMIZED_PRINT_START_HOME

;===== BOX_PREPAR =====
OPTIMIZED_START_PRINT_FILAMENT_PREP EXTRUDER=[initial_no_support_extruder] FIRSTLAYERTEMP=[nozzle_temperature_initial_layer] PURGETEMP={nozzle_temperature_range_high[initial_tool]} BEDTEMP=[bed_temperature_initial_layer_single] CHAMBER=[chamber_temperatures]

;===== PRINT_START =====
; Select the initial tool before the front prime line.
T[initial_tool]
; Set bed target temperature (do not wait).
M140 S[bed_temperature_initial_layer_single]
; Set chamber target temperature (do not wait).
M141 S[chamber_temperatures]
; Use absolute coordinates for the front prime line.
G90
; Move to the adaptive front prime start point when the first layer leaves room.
G1 Z5 F1200
{if first_layer_print_min[1] - 10 >= print_bed_min[1]}
{if first_layer_print_min[0]+45 <= print_bed_max[0]}
G1 X{first_layer_print_min[0]+45} Y{first_layer_print_min[1]-10} F20000
{else}
G1 X218 Y0 F20000
{endif}
{else}
G1 X218 Y0 F20000
{endif}
; Wait for nozzle to be fully back at first-layer temperature at the prime start.
M109 S[nozzle_temperature_initial_layer]
; Use relative extrusion for the prime line.
M83
; Reset extruder position before priming.
G92 E0
; Draw a fat front prime line to consume high-temp ooze from the final heat-up.
G1 Z0.5 F900
G1 Z{initial_layer_print_height} F1200
G1 E6 F300
M106 S200
{if first_layer_print_min[1] - 10 >= print_bed_min[1]}
{if first_layer_print_min[0]+45 <= print_bed_max[0]}
G1 X{first_layer_print_min[0]+5} E20 F1200
G1 F6000
G1 X{first_layer_print_min[0]} E0.8
{else}
G1 X178 E20 F1200
G1 F6000
G1 X173 E0.8
{endif}
{else}
G1 X178 E20 F1200
G1 F6000
G1 X173 E0.8
{endif}
; Lift off after the tapered finish.
G1 Z1 F1200
; Turn the part cooling fan back off after the prime line.
M106 S0
; Reset extruder position for the print proper.
G92 E0
; Restore absolute extrusion mode for sliced moves.
M82
; Mark printer status as actively printing after startup completes.
SET_PRINT_MAIN_STATUS MAIN_STATUS=printing
