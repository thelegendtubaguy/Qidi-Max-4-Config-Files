SET_PRINT_MAIN_STATUS MAIN_STATUS=print_end
G0 Z{min(max_print_height, max_layer_z + 3)} F600
OPTIMIZED_MOVE_TO_TRASH
OPTIMIZED_END_PRINT_FILAMENT_PREP T=[current_extruder]
OPTIMIZED_END_NOZZLE_COOLDOWN_START EXHAUST_SPEED=0

{if max_layer_z < max_print_height / 2}
G1 Z{min(max_print_height, max_print_height / 2 + 10)} F600
{else}
G1 Z{min(max_print_height, max_layer_z + 3)} F600
{endif}

OPTIMIZED_END_STAGED_NOZZLE_WIPE
PRINT_END
