# Helper code for implementing homing operations
#
# Copyright (C) 2016-2021  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math

HOMING_START_DELAY = 0.001
ENDSTOP_SAMPLE_TIME = .000015
ENDSTOP_SAMPLE_COUNT = 4

# Return a completion that completes when all completions in a list complete
def multi_complete(printer, completions):
    if len(completions) == 1:
        return completions[0]
    # Build completion that waits for all completions
    reactor = printer.get_reactor()
    cp = reactor.register_callback(lambda e: [c.wait() for c in completions])
    # If any completion indicates an error, then exit main completion early
    for c in completions:
        reactor.register_callback(
            lambda e, c=c: cp.complete(1) if c.wait() else 0)
    return cp

# Tracking of stepper positions during a homing/probing move
class StepperPosition:
    def __init__(self, stepper, endstop_name):
        self.stepper = stepper
        self.endstop_name = endstop_name
        self.stepper_name = stepper.get_name()
        self.start_pos = stepper.get_mcu_position()
        self.halt_pos = self.trig_pos = None
    def note_home_end(self, trigger_time):
        self.halt_pos = self.stepper.get_mcu_position()
        self.trig_pos = self.stepper.get_past_mcu_position(trigger_time)

# Implementation of homing/probing moves
class HomingMove:
    def __init__(self, printer, endstops, toolhead=None):
        self.printer = printer
        self.endstops = endstops
        if toolhead is None:
            toolhead = printer.lookup_object('toolhead')
        self.toolhead = toolhead
        self.stepper_positions = []
    def get_mcu_endstops(self):
        return [es for es, name in self.endstops]
    def _calc_endstop_rate(self, mcu_endstop, movepos, speed):
        startpos = self.toolhead.get_position()
        axes_d = [mp - sp for mp, sp in zip(movepos, startpos)]
        move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
        move_t = move_d / speed
        max_steps = max([(abs(s.calc_position_from_coord(startpos)
                              - s.calc_position_from_coord(movepos))
                          / s.get_step_dist())
                         for s in mcu_endstop.get_steppers()])
        if max_steps <= 0.:
            return .001
        return move_t / max_steps
    def calc_toolhead_pos(self, kin_spos, offsets):
        kin_spos = dict(kin_spos)
        kin = self.toolhead.get_kinematics()
        for stepper in kin.get_steppers():
            sname = stepper.get_name()
            kin_spos[sname] += offsets.get(sname, 0) * stepper.get_step_dist()
        thpos = self.toolhead.get_position()
        return list(kin.calc_position(kin_spos))[:3] + thpos[3:]
    def homing_move(self, movepos, speed, probe_pos=False,
                    triggered=True, check_triggered=True):
        # Notify start of homing/probing move
        self.printer.send_event("homing:homing_move_begin", self)

        # Note start location
        self.toolhead.flush_step_generation()   # 记录初始位置
        kin = self.toolhead.get_kinematics()
        kin_spos = {s.get_name(): s.get_commanded_position()
                    for s in kin.get_steppers()}
        
        # 首次记录更新当前的坐标
        self.stepper_positions = [ StepperPosition(s, name)
                                   for es, name in self.endstops
                                   for s in es.get_steppers() ]
        # Start endstop checking
        print_time = self.toolhead.get_last_move_time()
        endstop_triggers = []
        #初始化限位开关
        # self.endstops[0][0].endstop_sync_reset()
        sync_obj = self.endstops[0][0]
        class_name = sync_obj.__class__.__name__
        
        # 尝试查找底层 MCU 对象
        target_obj = sync_obj
        if not hasattr(target_obj, 'endstop_sync_reset') and hasattr(target_obj, 'mcu_endstop'):
            target_obj = target_obj.mcu_endstop
        # 执行带有保险的重置
        if hasattr(target_obj, 'endstop_sync_reset'):
            logging.info("DEBUG: Sync Reset executing via class: %s (Target: %s)" % 
                         (class_name, target_obj.__class__.__name__))
            target_obj.endstop_sync_reset()
        else:
            logging.error("DEBUG: Sync Reset skipped! No method found in class: %s" % class_name)

        for mcu_endstop, name in self.endstops:
            rest_time = self._calc_endstop_rate(mcu_endstop, movepos, speed)
            # logging.info("WEIGHT_HOME:%s, %s" % (mcu_endstop, rest_time))
            wait = mcu_endstop.home_start(print_time, ENDSTOP_SAMPLE_TIME,
                                          ENDSTOP_SAMPLE_COUNT, rest_time,
                                          triggered=triggered)
            endstop_triggers.append(wait)
        all_endstop_trigger = multi_complete(self.printer, endstop_triggers)
        self.toolhead.dwell(HOMING_START_DELAY)
        # Issue move
        error = None
        try:
            self.toolhead.drip_move(movepos, speed, all_endstop_trigger) #执行移动
        except self.printer.command_error as e:
            error = "Error during homing move: %s" % (str(e),)
        # Wait for endstops to trigger 等待限位开关触发
        trigger_times = {}
        move_end_print_time = self.toolhead.get_last_move_time()
        for mcu_endstop, name in self.endstops:
            try:
                trigger_time = mcu_endstop.home_wait(move_end_print_time)
            except self.printer.command_error as e:
                if error is None:
                    error = "Error during homing %s: %s" % (name, str(e))
                continue
            if trigger_time > 0.:
                trigger_times[name] = trigger_time
            elif check_triggered and error is None:
                error = "No trigger on %s after full movement" % (name,)
        # Determine stepper halt positions 确定步进器停止位置
        self.toolhead.flush_step_generation()
        
        # 计算并设置工具头的位置
        for sp in self.stepper_positions:
            tt = trigger_times.get(sp.endstop_name, move_end_print_time)
            sp.note_home_end(tt)
        if probe_pos:
            halt_steps = {sp.stepper_name: sp.halt_pos - sp.start_pos
                          for sp in self.stepper_positions}
            trig_steps = {sp.stepper_name: sp.trig_pos - sp.start_pos
                          for sp in self.stepper_positions}
            haltpos = trigpos = self.calc_toolhead_pos(kin_spos, trig_steps)
            if trig_steps != halt_steps:
                haltpos = self.calc_toolhead_pos(kin_spos, halt_steps)
        else:
            haltpos = trigpos = movepos
            over_steps = {sp.stepper_name: sp.halt_pos - sp.trig_pos
                          for sp in self.stepper_positions}
            if any(over_steps.values()):
                self.toolhead.set_position(movepos)
                halt_kin_spos = {s.get_name(): s.get_commanded_position()
                                 for s in kin.get_steppers()}
                haltpos = self.calc_toolhead_pos(halt_kin_spos, over_steps)
        self.toolhead.set_position(haltpos)

        # Signal homing/probing move complete
        try:
            self.printer.send_event("homing:homing_move_end", self)
        except self.printer.command_error as e:
            if error is None:
                error = str(e)
        if error is not None:
            raise self.printer.command_error(error)
        return trigpos
    def probing_xy_move(self, movepos, speed, 
                    triggered=True, check_triggered=True):
        # Notify start of homing/probing move
        self.printer.send_event("homing:homing_move_begin", self)
        # Note start location
        self.toolhead.flush_step_generation()   # 记录初始位置
        kin = self.toolhead.get_kinematics()
        kin_spos = {s.get_name(): s.get_commanded_position()
                    for s in kin.get_steppers()}

        # 首次记录更新当前的坐标
        self.stepper_positions = [ StepperPosition(s, s.get_name())
                                   for s in kin.get_steppers() ]

        # Start endstop checking
        print_time = self.toolhead.get_last_move_time()
        endstop_triggers = []
        #初始化限位开关
        for mcu_endstop, name in self.endstops:
            rest_time = self._calc_endstop_rate(mcu_endstop, movepos, speed)
            # logging.info("WEIGHT_HOME:%s, %s" % (mcu_endstop, rest_time))
            wait = mcu_endstop.home_start(print_time, ENDSTOP_SAMPLE_TIME,
                                          ENDSTOP_SAMPLE_COUNT, rest_time,
                                          triggered=triggered)
            endstop_triggers.append(wait)
        all_endstop_trigger = multi_complete(self.printer, endstop_triggers)

        #self.toolhead.dwell(HOMING_START_DELAY)
        self.toolhead.dwell(0.05)
        # Issue move
        error = None
        try:
            self.toolhead.drip_move(movepos, speed, all_endstop_trigger) #执行移动
        except self.printer.command_error as e:
            error = "Error during homing move: %s" % (str(e),)
        # Wait for endstops to trigger 等待限位开关触发
        trigger_times = {}
        move_end_print_time = self.toolhead.get_last_move_time()
        for mcu_endstop, name in self.endstops:
            try:
                trigger_time = mcu_endstop.home_wait(move_end_print_time)
            except self.printer.command_error as e:
                if error is None:
                    error = "Error during homing %s: %s" % (name, str(e))
                continue
            if trigger_time > 0.:
                trigger_times[name] = trigger_time
            elif check_triggered and error is None:
                error = "No trigger on %s after full movement" % (name,)
        # Determine stepper halt positions 确定步进器停止位置

        self.toolhead.flush_step_generation()
        # 计算并设置工具头的位置
        for sp in self.stepper_positions:
            tt = trigger_times.get('probe', move_end_print_time)
            sp.note_home_end(tt)
        halt_steps = {sp.stepper_name: sp.halt_pos - sp.start_pos
                        for sp in self.stepper_positions}
        trig_steps = {sp.stepper_name: sp.trig_pos - sp.start_pos
                        for sp in self.stepper_positions}
        logging.info(f"WEIGHT:trig_steps:{trig_steps}, halt_steps:{halt_steps}")
        haltpos = trigpos = self.calc_toolhead_pos(kin_spos, trig_steps)

        if trig_steps != halt_steps:
            haltpos = self.calc_toolhead_pos(kin_spos, halt_steps)
        self.toolhead.set_position(haltpos)

        # Signal homing/probing move complete
        try:
            self.printer.send_event("homing:homing_move_end", self)
        except self.printer.command_error as e:
            if error is None:
                error = str(e)
        if error is not None:
            raise self.printer.command_error(error)
        # return haltpos
        return trigpos
    def check_no_movement(self):
        if self.printer.get_start_args().get('debuginput') is not None:
            return None
        for sp in self.stepper_positions:
            if sp.start_pos == sp.trig_pos:
                return sp.endstop_name
        return None
    
    def home_move(self, movepos, speed):
        self.toolhead.dwell(HOMING_START_DELAY)
         # Issue move
        error = None
        try:
            self.toolhead.move(movepos, speed)
        except self.printer.command_error as e:
            error = "Error during homing move: %s" % (str(e),)

# State tracking of homing requests
class Homing:
    def __init__(self, printer):
        self.printer = printer
        self.toolhead = printer.lookup_object('toolhead')
        self.changed_axes = []
        self.trigger_mcu_pos = {}
        self.adjust_pos = {}
        self.gcode = self.printer.lookup_object('gcode')
    def set_axes(self, axes):
        self.changed_axes = axes
    def get_axes(self):
        return self.changed_axes
    def get_trigger_position(self, stepper_name):
        return self.trigger_mcu_pos[stepper_name]
    def set_stepper_adjustment(self, stepper_name, adjustment):
        self.adjust_pos[stepper_name] = adjustment

    # 防止未定义的坐标，以当前坐标填充None的值
    def _fill_coord(self, coord):
        # Fill in any None entries in 'coord' with current toolhead position
        thcoord = list(self.toolhead.get_position())
        for i in range(len(coord)):
            if coord[i] is not None:
                thcoord[i] = coord[i]
        return thcoord
    def set_homed_position(self, pos):
        self.toolhead.set_position(self._fill_coord(pos))

    # 另外重写二次回零的过程
    def home_second_genera(self, rails, forcepos, movepos, hi, endstops, mcu_endstop):
        startpos = self._fill_coord(forcepos)
        homepos = self._fill_coord(movepos)
        axes_d = [hp - sp for hp, sp in zip(homepos, startpos)]
        move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
        retract_r = min(1., hi.retract_dist / move_d)
        retractpos = [hp - ad * retract_r
                        for hp, ad in zip(homepos, axes_d)]
        self.toolhead.move(retractpos, hi.retract_speed)
        self.toolhead.dwell(1) # 延时1s

        startpos = [rp - ad * retract_r
                        for rp, ad in zip(retractpos, axes_d)]
        self.toolhead.set_position(startpos)
        hmove = HomingMove(self.printer, endstops)
        probexy = self.toolhead.get_position()[:2]
        retry_count = 0
        #进入probe探点模式
        while 1:
            epos = hmove.homing_move(homepos, hi.second_homing_speed, True)
            self.toolhead.manual_move(probexy + [epos[2] + 1.5], hi.second_homing_speed)
            epos1 = hmove.homing_move(homepos, hi.second_homing_speed, True)
            self.toolhead.manual_move(probexy + [epos[2] + 1.5], hi.second_homing_speed)
            z_diff = abs(epos[2] - epos1[2])
            # 讲道理这里需要重新设置一下坐标，这样甚至可以不需要再来一次homing second, 避免
            # 第二次出现意外导致回零不正常，但是需要做一下验证！！！！，暂时保留！
            logging.info("WEIGHT: second homing epos:%s, %s, %.3f", epos, epos1, z_diff)
            if z_diff < 0.03:
                break
            retry_count = retry_count + 1
            if retry_count > 2:
                logging.info("WEIGHT:run home_second_genera retry!")
                # 重新做一次清零
                for mcu_endstop1, name in endstops:
                    mcu_endstop1.home_zero() 
        #正常走回零确定坐标
        hmove.homing_move(homepos, hi.second_homing_speed)

    # 闭环电机-XY二次回零/回零校准
    def xy_home_second_genera(self, rails, forcepos, movepos, hi, endstops, mcu_endstop):
        # logging.info("Test: custom_second_home: V2.1.4")
        startpos = self._fill_coord(forcepos)
        homepos = self._fill_coord(movepos)
        hmove = HomingMove(self.printer, endstops)

        if (hi.positive_dir):
            lift_dir = -1
        else:
            lift_dir = 1

        # 进入回零模式操作
        x_script = "M400\nG4 P100\nSET_HOMING_STATE STEPPER=x VALUE=1\nG4 P100\nSET_HOMING_STATE STEPPER=y VALUE=1\nG4 P100\nSET_HOMING_MODE STEPPER=x VALUE=1\nG4 P100"
        y_script = "M400\nG4 P100\nSET_HOMING_STATE STEPPER=x VALUE=1\nG4 P100\nSET_HOMING_STATE STEPPER=y VALUE=1\nG4 P100\nSET_HOMING_MODE STEPPER=y VALUE=1\nG4 P100"
        # 进入工作模式操作
        x_recover_script = "M400\nSET_HOMING_STATE STEPPER=y VALUE=2\nG4 P50\nSET_HOMING_STATE STEPPER=x VALUE=2\nG4 P50\nSET_HOMING_MODE STEPPER=y VALUE=2\nG4 P50\nSET_HOMING_MODE STEPPER=x VALUE=2\nG4 P50"
        y_recover_script = "M400\nSET_HOMING_STATE STEPPER=y VALUE=2\nG4 P50\nSET_HOMING_STATE STEPPER=x VALUE=2\nG4 P50\nSET_HOMING_MODE STEPPER=y VALUE=2\nG4 P50\nSET_HOMING_MODE STEPPER=x VALUE=2\nG4 P50"

        if rails[0].get_name() == "stepper_x":
            axis = 0
            script = x_script
            recover_script = x_recover_script
        elif rails[0].get_name() == "stepper_y":
            axis = 1
            script = y_script
            recover_script = y_recover_script
        else:
            axis = 2
            script = ""

        curpos = self.toolhead.get_position()
        homepos_axis = self._fill_coord(curpos)
        homepos_axis[axis] = homepos[axis]
        logging.info(f"homepos_axis={homepos_axis}")

        retry_count = 0
        while True:
            retry_count += 1
            self.toolhead.set_position(startpos)

            # 第一次触发
            if (axis == 0 or axis == 1): 
                self.printer.send_event("homing:home_rails_end", self, rails)
                self.printer.send_event("homing:home_rails_begin", self, rails)
                self.gcode.run_script_from_command(script)

            epos = hmove.homing_move(homepos_axis, hi.speed, True)
            # logging.info("epos=%.4f", epos[axis])
            self.toolhead.set_position(homepos_axis)

            # 新增：设置为工作模式
            if (axis == 0 or axis == 1): 
                self.gcode.run_script_from_command(recover_script)

            # 中间增加清零
            for mcu_endstop1, _ in endstops:
                mcu_endstop1.home_zero()

            # 抬起
            retract_dist = lift_dir * hi.retract_dist
            lift = self._fill_coord(homepos_axis)
            lift[axis] += retract_dist
            self.toolhead.manual_move(lift, hi.retract_speed)
            self.gcode.run_script_from_command("M400")

            self.toolhead.set_position(startpos)

            # 第二次触发
            if (axis == 0 or axis == 1): 
                self.printer.send_event("homing:home_rails_end", self, rails)
                self.printer.send_event("homing:home_rails_begin", self, rails)
                self.gcode.run_script_from_command(script)

            epos1 = hmove.homing_move(homepos_axis, hi.second_homing_speed, True)
            # logging.info("epos1=%.4f", epos1[axis])
            self.toolhead.set_position(homepos_axis)

            # 新增：设置为工作模式
            if (axis == 0 or axis == 1): 
                self.gcode.run_script_from_command(recover_script)

            # 偏差判断
            second_homing_dist = startpos[axis] - epos1[axis]
            axis_diff = abs(retract_dist - second_homing_dist)
            logging.info("SECOND_HOME[%d]: axis=%d retract_dist=%.4f second_homing_dist=%.4f diff=%.4f", retry_count, axis, retract_dist, second_homing_dist, axis_diff)
            if axis_diff <= hi.tolerance:
                logging.info(f"SECOND_HOME[{retry_count}]: axis_diff[{axis_diff}] < second_homing_tolerance[{hi.tolerance}], success")
                break

            # 再次抬起
            lift = self._fill_coord(homepos_axis)
            lift[axis] += retract_dist
            self.toolhead.manual_move(lift, hi.retract_speed)
            self.gcode.run_script_from_command("M400")

            for mcu_endstop1, _ in endstops:
                mcu_endstop1.home_zero()
            if retry_count >= hi.retries:
                raise self.printer.command_error("SECOND_HOME: retries over limit[%d], fail" % (hi.retries))

        if (axis == 0 or axis == 1): 
            self.toolhead.set_position(homepos_axis)
        # 正常走回零确定坐标
        else:
            hmove.homing_move(homepos_axis, hi.second_homing_speed)

    def home_rails(self, rails, forcepos, movepos):
        
        #####################################################################################
        # forcepos：是输入要移动的坐标值，一般是最大行程的1.5倍
        # movepos: 这里存放对应轴的限位触发值，由配置：position_endstop决定
        # homing_axes: 当前移动的轴， X:0, Y:1, Z:2 E:3 .....
        # startpos: 设置当前的坐标作为起始坐标，实际上和forcepos对应轴的值是一致的，
        # 但是由于执行了_fill_coord，所以forcepos里面None的值，都被填充成当前的实际
        # toolhead的坐标值。
        # hi.speed：配置的回零速度
        # hi.retract_speed：配置的二次回零的速度
        #####################################################################################

        # Notify of upcoming homing operation 这里发送通知，实际是执行回调，全局查找"homing:home_rails_begin"
        self.printer.send_event("homing:home_rails_begin", self, rails)
        # logging.info("MKS_HOMIMG_DEBUG:forcepos = %s" % forcepos)
        # logging.info("MKS_HOMIMG_DEBUG:movepos = %s" % movepos)
        # Alter kinematics class to think printer is at forcepos
        homing_axes = [axis for axis in range(3) if forcepos[axis] is not None]
        # logging.info("MKS_HOMIMG_DEBUG:homing_axes = %s" % homing_axes)
        startpos = self._fill_coord(forcepos)
        # logging.info("MKS_HOMIMG_DEBUG:startpos = %s" % startpos)
        homepos = self._fill_coord(movepos)
        # logging.info("MKS_HOMIMG_DEBUG:homepos = %s" % homepos)
        self.toolhead.set_position(startpos, homing_axes=homing_axes)
    
        # Perform first home
        endstops = [es for rail in rails for es in rail.get_endstops()]
        self.toolhead.dwell(
            .25 if rails[0].get_name() in ("stepper_x", "stepper_y")
            else 1)
        for mcu_endstop, name in endstops:
            mcu_endstop.home_zero() 
        hi = rails[0].get_homing_info()
        hmove = HomingMove(self.printer, endstops)

        # 如果是XY轴闭环，并且打开了“二次回零”，才触发
        # TODO: 可用homing_axes代替？
        if rails[0].get_name() == "stepper_x":
            axis = 0
        elif rails[0].get_name() == "stepper_y":
            axis = 1
        else:
            axis = 2

        if (axis != 2 and hi.retract_dist != 0):
            self.xy_home_second_genera(rails, forcepos, movepos, hi, endstops, mcu_endstop)
        else:
            hmove.homing_move(homepos, hi.speed)

            # Perform second home
            if hi.retract_dist:
                self.home_second_genera(rails, forcepos, movepos, hi, endstops, mcu_endstop)

                '''
                # Retract
                startpos = self._fill_coord(forcepos)
                homepos = self._fill_coord(movepos)
                axes_d = [hp - sp for hp, sp in zip(homepos, startpos)]
                move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
                retract_r = min(1., hi.retract_dist / move_d)
                retractpos = [hp - ad * retract_r
                            for hp, ad in zip(homepos, axes_d)]
                self.toolhead.move(retractpos, hi.retract_speed)
                self.toolhead.dwell(1)
                # Home again
                startpos = [rp - ad * retract_r
                            for rp, ad in zip(retractpos, axes_d)]
                self.toolhead.set_position(startpos)
                hmove = HomingMove(self.printer, endstops)
                hmove.homing_move(homepos, hi.second_homing_speed)

                # debug remove..   # fix-wangchong
                # if hmove.check_no_movement() is not None:
                #     raise self.printer.command_error(
                #         "Endstop %s still triggered after retract"
                #         % (hmove.check_no_movement(),))

                # 这里应该要重试
                # if hmove.check_no_movement() is not None:

                for i in range(3):
                    startpos = self._fill_coord(forcepos)
                    homepos = self._fill_coord(movepos)
                    startpos = [rp - ad * retract_r
                            for rp, ad in zip(retractpos, axes_d)]
                    axes_d = [hp - sp for hp, sp in zip(homepos, startpos)]
                    move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
                    retract_r = min(1., hi.retract_dist / move_d)
                    retractpos = [hp - ad * retract_r
                                for hp, ad in zip(homepos, axes_d)]
                    self.toolhead.move(retractpos, hi.retract_speed)
                    # self.toolhead.dwell(1)
                    startpos = [rp - ad * retract_r
                                for rp, ad in zip(retractpos, axes_d)]
                    self.toolhead.set_position(startpos)
                    hmove = HomingMove(self.printer, endstops)
                    hmove.homing_move(homepos, hi.second_homing_speed)
                    RES = hmove.check_no_movement()

                    # 检查有问题，回到没问题为止
                    RES = hmove.check_no_movement()
                    COUNT = 0
                    while RES is not None and COUNT != 0:
                        startpos = self._fill_coord(forcepos)
                        homepos = self._fill_coord(movepos)
                        startpos = [rp - ad * retract_r
                                for rp, ad in zip(retractpos, axes_d)]
                        axes_d = [hp - sp for hp, sp in zip(homepos, startpos)]
                        move_d = math.sqrt(sum([d*d for d in axes_d[:3]]))
                        retract_r = min(1., hi.retract_dist / move_d)
                        retractpos = [hp - ad * retract_r
                                    for hp, ad in zip(homepos, axes_d)]
                        self.toolhead.move(retractpos, hi.retract_speed)
                        self.toolhead.dwell(1)
                        if COUNT == 2:
                            mcu_endstop.home_zero()
                            COUNT = 0
                        self.toolhead.dwell(1)
                        startpos = [rp - ad * retract_r
                                    for rp, ad in zip(retractpos, axes_d)]
                        self.toolhead.set_position(startpos)
                        hmove = HomingMove(self.printer, endstops)
                        hmove.homing_move(homepos, hi.second_homing_speed)
                        RES = hmove.check_no_movement()
                        COUNT = COUNT +1
                '''

        # Signal home operation complete
        self.toolhead.flush_step_generation()
        self.trigger_mcu_pos = {sp.stepper_name: sp.trig_pos
                                for sp in hmove.stepper_positions}
        self.adjust_pos = {}
        self.printer.send_event("homing:home_rails_end", self, rails)
        if any(self.adjust_pos.values()):
            # Apply any homing offsets
            kin = self.toolhead.get_kinematics()
            homepos = self.toolhead.get_position()
            kin_spos = {s.get_name(): (s.get_commanded_position()
                                       + self.adjust_pos.get(s.get_name(), 0.))
                        for s in kin.get_steppers()}
            newpos = kin.calc_position(kin_spos)
            for axis in homing_axes:
                homepos[axis] = newpos[axis]
            self.toolhead.set_position(homepos)

class PrinterHoming:
    def __init__(self, config):
        self.printer = config.get_printer()

        # Register g-code commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('G28', self.cmd_G28)
        gcode.register_command('G99', self.cmd_G99)
    def manual_home(self, toolhead, endstops, pos, speed,
                    triggered, check_triggered):
        hmove = HomingMove(self.printer, endstops, toolhead)
        try:
            hmove.homing_move(pos, speed, triggered=triggered,
                              check_triggered=check_triggered)
        except self.printer.command_error:
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    "Homing failed due to printer shutdown")
            raise
    def probing_move(self, mcu_probe, pos, speed):
        endstops = [(mcu_probe, "probe")]
        
        hmove = HomingMove(self.printer, endstops)
        logging.info("WEIGHT-pos: %s" % pos)
        try:
            epos = hmove.homing_move(pos, speed, probe_pos=True)
        except self.printer.command_error:
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    "Probing failed due to printer shutdown")
            raise
        # if hmove.check_no_movement() is not None:
            # try:
            #     epos = hmove.homing_move(pos, speed, probe_pos=True)
            # except self.printer.command_error:
            #     if self.printer.is_shutdown():
            #         raise self.printer.command_error(
            #             "Probing failed due to printer shutdown")
            # raise self.printer.command_error(  # fix-wangchong
            #     "Probe triggered prior to movement")
        return epos
    def probing_xy(self, mcu_probe, pos, speed):
        endstops = [(mcu_probe, "probe")]
        
        hmove = HomingMove(self.printer, endstops)
        logging.info("WEIGHT-xy-pos: %s" % pos)
        try:
            epos = hmove.probing_xy_move(pos, speed)
        except self.printer.command_error:
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    "Probing failed due to printer shutdown")
            raise
        return epos
    def cmd_G28(self, gcmd):
        # Move to origin
        axes = []
        for pos, axis in enumerate('XYZ'):
            if gcmd.get(axis, None) is not None:
                axes.append(pos)
        if not axes:
            axes = [0, 1, 2]
        homing_state = Homing(self.printer)
        homing_state.set_axes(axes)
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        try:
            kin.home(homing_state)
        except self.printer.command_error:
            if self.printer.is_shutdown():
                raise self.printer.command_error(
                    "Homing failed due to printer shutdown")
            self.printer.lookup_object('stepper_enable').motor_off()
            raise
    def cmd_G99(self, gcmd):
        self.toolhead = self.printer.lookup_object('toolhead')
        
        movepos = [10, 10, 5]
        
        self.toolhead.move(movepos, 100)
        
        gcmd.respond_info("run g99 command")

def load_config(config):
    return PrinterHoming(config)
