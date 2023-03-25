import logging
import math
import queue
import threading
import time

import GPIOMgr
import Util

DISABLED = 50       # Disabled but otherwise normal
IDLE = 100          # Enabled but idle
MOVING = 200        #
HOMING = 250        # Homing to find +- limits
ERROR = -10         # Generic error state
HARDKILL = -20      # Permanently disabled
UNKNOWN = -100      #
UNINITIALIZED = -50 #
STATES_STR = {DISABLED: 'DISABLED', IDLE: 'IDLE', MOVING: 'MOVING', HOMING: 'HOMING', ERROR: 'ERROR',
              HARDKILL: 'HARDKILL', UNKNOWN: 'UNKNOWN', UNINITIALIZED: 'UNINITIALIZED'}

ILOCK_DN = 100
ILOCK_UP = 110
ILOCK_ESTOP = 120
ILOCK_OK = 10
ILOCK_STR = {100: 'ILOCK_DN', 110: 'ILOCK_UP', 120: 'ILOCK_ESTOP'}


class Stepper:
    DIR_UP = 1
    DIR_DN = 0

    ESTOP = False

    position = -1
    state = UNKNOWN

    def __init__(self, uuid, name, fname, dr, st, en, sl, LUp, LDn, LUpState, LDnState, st_size, ptime, st_dtime, aen, adis,
                 jerk, vel, acc):
        self.logger = logging.getLogger(__name__+'.'+str(uuid))
        try:
            # Sanity checks
            assert (all(x in Util.BCM_PINS for x in [dr, st, en, sl, LUp, LDn]))
            assert (all(x in [0, 1] for x in [LUpState, LDnState]))
            assert (0 <= ptime < 1000)
            assert (0 <= st_dtime < 1000)
            for i in [jerk, vel, acc]:
                assert (0 <= i < 20000)

            # Basic parameters
            self.uuid = uuid
            self.name = name
            self.full_name = fname
            self.PIN_DIR = dr
            self.PIN_STEP = st
            self.PIN_ENABLE = en
            self.PIN_SLEEP = sl
            self.PIN_LIM_UP, self.LIM_UP_HIT = LUp, LUpState
            self.PIN_LIM_DN, self.LIM_DN_HIT = LDn, LDnState

            # Step size factor corresponds to 1/microsteps, with single steps at 256 level
            self.step_size = st_size

            # Converting pulse time to s (below 1ms is not possible without RT kernel or C bindings)
            self.pulse_time = ptime / 1000.0
            self.step_delay = st_dtime / 1000.0

            # Motion parameters
            self.jerk, self.vel, self.acc = jerk, vel, acc

            self.auto_enable = bool(aen)
            self.auto_disable = bool(adis)

            self.state = UNINITIALIZED
            self.homed = False
            self.thread_on = False

            # Event indicating new command
            self.stopevt = threading.Event()
            self.doneevt = threading.Event()
            self.queue = queue.Queue(maxsize=100)

            self.logger.info('NEW Stepper (%s) (uuid %s) (fname %s) with pins %d,%d,%d,%d,%d,%d (Dr,St,En,Sl,LUp,LDn)',
                             name, uuid, fname, dr, st, en, sl, LUp, LDn)
            self.logger.info('Motion params: (%f) (%f) (%f) (jerk, vel, acc)',
                             self.jerk, self.vel, self.acc)
        except:
            self.logger.exception("Failed to create stepper object")
            raise

    # Queries actual hardware for status (it can be non-default from previous runs for instance)
    def initialize(self, RPi=True):
        self.logger.info("Stepper %s - starting initialization", self.full_name)
        set1a = [self.PIN_DIR, self.PIN_STEP]
        set1b = [self.PIN_ENABLE, self.PIN_SLEEP]
        set2 = [self.PIN_LIM_UP, self.PIN_LIM_DN]
        # Immediately set first group low
        GPIOMgr.set_mode_outputs(set1a, GPIOMgr.GPIO.LOW)
        # Read mode control pins
        GPIOMgr.set_mode_inputs(set1b,GPIOMgr.GPIO.PUD_OFF)
        # Obtain current status
        if (RPi):
            self.direction = self._get_direction()
            self.position = 0
            self.enabled = self.is_enabled()
            self.awake = self.is_awake()
            if not self.awake or not self.enabled:
                self.logger.warning("New motor is either asleep or disabled")
        else:
            self.direction = 1
            self.position = 0
            self.enabled = 1
            self.awake = 1
            if not self.awake or not self.enabled:
                self.logger.warning("New motor is either asleep or disabled")
        # Now set other outputs high
        GPIOMgr.set_mode_outputs(set1b, GPIOMgr.GPIO.HIGH)
        # Enable limit pullups
        GPIOMgr.set_mode_inputs(set2, GPIOMgr.GPIO.PUD_UP)
        self.logger.info("Motor %s initialized - dir %s, en %s, awk %s",
                         self.name, self.direction, self.enabled, self.awake)

        # Create and start thread
        thread = threading.Thread(name='mt_thr_{}'.format(self.uuid), target=self.control_thread, args=())
        self.t = thread
        thread.setDaemon(False)
        thread.start()

        self.state = DISABLED

    # For steppers, we can reset live without any further actions
    def reinitialize(self):
        if not self.is_moving():
            self.initialize()

    def is_moving(self):
        return self.state == MOVING or self.state == HOMING
    
    def control_thread(self):
        """
        Independent thread responsible for executing motor commands
        :return:
        """
        self.logger.info('Motor %s control thread starting up', self.uuid)
        self.thread_on = True
        try:
            while self.thread_on:
                # Get next msg
                try:
                    msg = self.queue.get(block=True, timeout=0.05)
                except queue.Empty:
                    pass
                else:
                    self.logger.info('Thread command %s', msg)
                    if msg[0] == 'move':
                        ilock = self.check_interlocks(raise_exc=False)
                        direction = msg[1]
                        numsteps = msg[2]
                        force = msg[3]
                        if ilock != ILOCK_OK:
                            if force:
                                self.logger.warning('Forced move with active interlock %s - this is dangerous!', ilock)
                            else:
                                self.logger.warning('Interlock fail %s - move ignored!', ilock)
                                self.doneevt.set()
                                continue

                        # Acquire move lock to ensure only this motor will move
                        with GPIOMgr.movelock:
                            self.logger.info("Move %d steps in direction %d", numsteps, direction)
                            if direction != self.direction:
                                self.state = MOVING
                                self._set_direction(direction)
                                self.state = IDLE
                                self.logger.debug("Direction changed to %s", direction)
                            else:
                                self.logger.debug("Direction %s already correct", direction)

                            if numsteps == 0:
                                self.logger.debug("Not moving since step number is 0")
                            else:
                                if self.state == DISABLED:
                                    if self.auto_enable:
                                        self._enable_direct()
                                    else:
                                        self.logger.warning('not enabled, ignoring move command!')
                                        continue
                                self.state = MOVING
                                self.logger.debug("Doing %d steps", numsteps)
                                try:
                                    result = self._do_steps(numsteps, override=force)
                                except MoveException as e:
                                    self.logger.exception("Exception triggered during move!")
                                    self.error = -2
                                    result = -2
                                self.logger.info("Motion finished, result code: %s", result)
                                self.state = IDLE
                            if self.auto_disable:
                                self._disable_direct()
                            self.doneevt.set()
                    if msg[0] == 'home':
                        ilock = self.check_interlocks(raise_exc=False)
                        if ilock != ILOCK_OK:
                            self.logger.warning('Interlock %s FAIL - move ignored!', ILOCK_STR[ilock])
                            continue
                        direction = msg[1]

                        # Acquire move lock to ensure only this motor will move
                        with GPIOMgr.movelock:
                            self.logger.info("Home in direction %d", direction)
                            if direction != self.direction:
                                self.state = MOVING
                                self._set_direction(direction)
                                self.state = IDLE
                                self.logger.debug("Direction changed to %s", self.direction)
                            else:
                                self.logger.debug("Direction %s already correct", self.direction)

                            self.state = MOVING
                            self.logger.debug("Moving until interlock trigger")
                            initial_pos = self.position
                            try:
                                maxsteps = 3 * 80 * 3600 #3in*80tpi*3600spr
                                self._do_steps(maxsteps, vel=self.vel)
                            except MoveException as e:
                                delta_steps = self.position - initial_pos
                                self.logger.info('Limit hit after %d steps, backing off', delta_steps)
                            else:
                                raise MoveException("Did not hit limit over max number of steps!!!")
                            time.sleep(0.1)

                            initial_pos = self.position
                            try:
                                self._set_direction(direction ^ 1)
                                self.logger.debug("Direction changed to %s", self.direction)
                                self._do_steps(3600 * 10, jerk=0, vel=self.vel/10, acc=self.acc/5, override=True, stop_on_unlatch=True)
                            except MoveException as e:
                                delta_steps = self.position - initial_pos
                                self.logger.info('Limit removed after %d steps, this is new zero', delta_steps)
                                self.position = 0
                                self.homed = True
                            else:
                                self.logger.error("ilock release backoff failed!")
                                self.state = IDLE
                                self.doneevt.set()

                            self.logger.info("Homing finished")
                            self.state = IDLE
                            self.doneevt.set()
                    elif msg[0] == 'enable':
                        force = msg[1]
                        ilock = self.check_interlocks(raise_exc=False)
                        if ilock != ILOCK_OK and not force:
                            self.logger.warning('interlock %s FAIL, enable ignored!', ILOCK_STR[ilock])
                            continue
                        if self.error != 0:
                            if force:
                                self.logger.debug('error %s cleared)', self.error)
                                self.error = 0
                            else:
                                self.logger.warning('error code %s present, enable ignored (use force to clear)', self.error)
                                continue
                        # Acquire move lock
                        with GPIOMgr.movelock:
                            self._enable_direct()
                    elif msg[0] == 'disable':
                        ilock = self.check_interlocks(raise_exc=False)
                        if ilock != ILOCK_OK:
                            self.logger.warning('interlock %s FAIL, proceeding with disable anyways', ILOCK_STR[ilock])
                        # Acquire move lock
                        with GPIOMgr.movelock:
                            self._disable_direct()
                    else:
                        continue
            self.logger.debug('Thread %s stopping gracefully!', self.t.name)
        except (KeyboardInterrupt, SystemExit) as e:
            self.logger.info('Thread %s shutting down gracefully',self.t.name)
            GPIOMgr.shutdown()
            self.thread_on = False
        except Exception as e:
            self.logger.exception(e)
            self.thread_on = False

    def _do_steps(self, ns, jerk=None, vel=None, acc=None, override=False, stop_on_unlatch=False):
        # Busy wait smooth motion algorithm
        jerk = jerk or self.jerk
        vel = vel or self.vel
        acc = acc or self.acc
        time_to_full_speed = (vel-jerk)/acc
        steps_to_full_speed = int((vel*vel)/(2*acc))
        slowdown_step = int(ns - steps_to_full_speed + 1)
        dir_factor = 1 if self.direction == 1 else -1
        self.logger.debug('%f s to full speed', time_to_full_speed)
        self.logger.debug('%f steps to full speed', steps_to_full_speed)
        if ns < steps_to_full_speed*2:
            # Won't get to full speed, slow down halfway
            self.logger.debug('Wont get to full speed, modifying slowdown step')
            slowdown_step = ns/2 + 1
        self.logger.debug('%f is slowdown step', slowdown_step)

        ramping_up = True
        ramping_down = False
        current_speed = jerk
        initial_delay = math.sqrt(2.0/acc)
        current_delay = initial_delay*0.676
        min_delay = 1.0/vel
        self.logger.debug("Initial delay: %f ms", initial_delay*1000)
        self.logger.debug("Min delay: %f ms", min_delay * 1000)
        # We are using taylor series approximation to ideal ramp

        delays = []
        for i in range(1, ns+1):
            if ramping_up:
                #current_delay -= 2 * current_delay / (4 * i + 1)
                current_delay = initial_delay * (math.sqrt(i+1)-math.sqrt(i))
                if current_delay < min_delay:
                    self.logger.debug("Delay before end: %f ms", current_delay * 1000.0)
                    current_delay = min_delay
                    ramping_up = False
                    self.logger.debug("Rampup end (step %d)", i)
                if i == slowdown_step:
                    ramping_up = False
                    ramping_down = True
                    self.logger.debug("Turnaround before rampup end")
                    self.logger.debug("Rampdown start (step %d)", i)
            elif ramping_down:
                if i == ns:
                    delays.append(0)
                    break
                current_delay -= 2 * current_delay / (4 * (i - ns) + 1)
            else:
                if i == slowdown_step:
                    ramping_down = True
                    self.logger.debug("Rampdown start (step %d)", i)
            # self.logger.debug("Step %d, speed %f sps", i, current_speed*1000)
            delays.append(current_delay)

            if current_delay > initial_delay or current_delay < 0:
                self.logger.warning("Step %d - bad delay %f", i, current_delay)
        self.logger.debug('Precomputed delays list length: %d', len(delays))

        if override and stop_on_unlatch:
            initial_ilock = self.check_interlocks(raise_exc=False, silent=True)
            if initial_ilock == ILOCK_OK:
                self.logger.warning('Stop on ilock release requested but no interlock is active')
                return -2 # We are not latched
            else:
                self.logger.info('Awaiting release from ilock state %s', initial_ilock)

        for i in range(1, ns+1):
            if override:
                # Ensure we can only move away from current interlock
                r = self.check_interlocks(raise_exc=False, silent=True)
                if stop_on_unlatch and r == ILOCK_OK:
                    self.logger.debug('Ilock release detected from state %s to %s - stopping', initial_ilock, r)
                    raise MoveException('hi')
                elif r != ILOCK_OK:
                    if r == ILOCK_DN:
                        if not self.direction == self.DIR_UP:
                            pass # we will not move more in wrong direction
                            #self.logger.debug('Ilock release detected from state %s to %s - stopping', initial_ilock, r)
                    elif r == ILOCK_UP:
                        if not self.direction == self.DIR_DN:
                            pass # we will not move more in wrong direction
                            #self.logger.debug('Ilock release detected from state %s to %s - stopping', initial_ilock, r)
                    else:
                        self.logger.critical('ILOCK force logic failure!!!')
                        self.stopevt.set()
            else:
                self.check_interlocks(raise_exc=True)
            GPIOMgr.pulse_pin(self.PIN_STEP, 0)
            self.position += 1*dir_factor
            current_delay = delays[i-1]
            start = end = time.perf_counter()
            while (end - start) < current_delay:
                if self.stopevt.is_set():
                    # Stop command received - clear things out
                    self.logger.warning("Stop command detected!")
                    self.queue.queue.clear()
                    self.stopevt.clear()
                    return -1
                end = time.perf_counter()
            if i % 1000 == 0:
                self.logger.debug("%d/%d, delay %f ms (actual %f)", i, ns, current_delay * 1000,
                                               (end - start) * 1000)

        # for i in range(1,ns+1):
        #     self.check_interlocks()
        #     GPIOMgr.pulse_pin(self.PIN_STEP, 0)
        #     self.position += 1
        #     #current_delay = 1/current_speed
        #
        #     # if ramping:
        #     #     current_speed += acc/current_speed
        #     #     if current_speed > vel:
        #     #         current_speed = vel
        #     #         ramping = False
        #     #     if i == slowdown_step:
        #     #         acc = -acc
        #     # else:
        #     #     if i == slowdown_step:
        #     #         acc = -acc
        #     #         ramping = True
        #     if ramping_up:
        #         current_delay -= 2*current_delay/(4*i+1)
        #         #current_delay = initial_delay * (math.sqrt(i+1)-math.sqrt(i))
        #         if current_delay < min_delay:
        #             self.logger.debug("Delay before end: %f ms", current_delay*1000.0)
        #             current_delay = min_delay
        #             ramping_up = False
        #             self.logger.debug("Rampup end (step %d)", i)
        #         if i == slowdown_step:
        #             ramping_up = False
        #             ramping_down = True
        #             self.logger.debug("Turnaround before rampup end")
        #             self.logger.debug("Rampdown start (step %d)", i)
        #     elif ramping_down:
        #         if i == ns:
        #             break
        #         current_delay -= 2*current_delay/(4*(i-ns)+1)
        #     else:
        #         if i == slowdown_step:
        #             ramping_down = True
        #             self.logger.debug("Rampdown start")
        #     #self.logger.debug("Step %d, speed %f sps", i, current_speed*1000)
        #
        #     if current_delay > initial_delay or current_delay < 0:
        #         self.logger.warning("Step %d - bad delay %f", i, current_delay)
        #
        #     start = end = time.perf_counter()
        #     while (end-start) < current_delay:
        #         if self.stopevt.is_set():
        #             # Stop command received - clear things out
        #             self.logger.warning("Stop command detected!")
        #             self.cmdq.queue.clear()
        #             self.stopevt.clear()
        #             return -1
        #         end = time.perf_counter()
        #     if i %100 == 0: self.logger.debug("Step %d, delay %f ms (actual %f)", i, current_delay * 1000, (end-start)*1000)
        #     #self.logger.debug("Step %d of %d done", i+1, numsteps)
        return 0

    def check_interlocks(self, raise_exc=True, silent=False):
        """
        Checks if any of interlocks are active
        :return:
        """
        # First check ESTOP
        if self.ESTOP:
            if not silent: self.logger.warning("M %s - ESTOP interlock fail", self.uuid)
            if raise_exc:
                raise MoveException("ESTOP")
            else:
                return ILOCK_ESTOP
        # Then check both limits
        up = GPIOMgr.get_pin_value(self.PIN_LIM_UP)
        up2 = GPIOMgr.get_pin_value(self.PIN_LIM_UP)
        if up == up2 == self.LIM_UP_HIT:
            if not silent: self.logger.warning("M %s - LIM UP fail", self.uuid)
            if raise_exc:
                raise MoveException("UP")
            else:
                return ILOCK_UP
        dn = GPIOMgr.get_pin_value(self.PIN_LIM_DN)
        dn2 = GPIOMgr.get_pin_value(self.PIN_LIM_DN)
        if dn == dn2 == self.LIM_DN_HIT:
            if not silent:
                self.logger.warning("M %s - LIM DN state (%s)", self.uuid, self.LIM_DN_HIT)
                self.logger.warning("DN state %s", GPIOMgr.get_pin_value(self.PIN_LIM_DN))
                self.logger.warning("DN state %s", GPIOMgr.get_pin_value(self.PIN_LIM_DN))
                self.logger.warning("UP state %s", GPIOMgr.get_pin_value(self.PIN_LIM_UP))
            if raise_exc:
                raise MoveException("DN")
            else:
                return ILOCK_DN
        # Nothing else for now, but we should add other sanity checks...
        #self.logger.debug("M %s - interlock check OK", self.full_name)
        return ILOCK_OK

    # Checks if limit reached (indicated by LOW, closed circuit)
    def is_lim_reached(self, direction):
        if direction == self.DIR_DN:
            return GPIOMgr.get_pin_value(self.PIN_LIM_DN) == self.LIM_DN_HIT
        elif direction == Stepper.DIR_UP:
            return GPIOMgr.get_pin_value(self.PIN_LIM_UP) == self.LIM_UP_HIT
        else:
            raise ValueError("Wrong limit check direction")

    def _enable_direct(self):
        self.logger.info("M %s - enabling", self.uuid)
        GPIOMgr.set_pin_value(self.PIN_ENABLE, GPIOMgr.GPIO.LOW)
        self.state = IDLE
        self.logger.debug("Done!")

    def _disable_direct(self):
        self.logger.info("M %s - disabling", self.uuid)
        GPIOMgr.set_pin_value(self.PIN_ENABLE, GPIOMgr.GPIO.HIGH)
        self.state = DISABLED
        self.logger.debug("Done!")

    def move(self, dir, numsteps, block=False, force=False):
        """
        Performs motor steps
        :param dir:
        :param numsteps:
        :param block:
        :return:
        """
        # Final safety checks
        numsteps = int(numsteps)
        assert (0 <= numsteps < 100000)
        assert dir in [Stepper.DIR_UP, Stepper.DIR_DN]

        # If queue is full, we reject command
        try:
            if (self.is_moving() or not self.queue.empty()) and block:
                self.logger.warning('Blocking commands cannot be queued, ignoring!')
                return 'Failed' #unsupported queue and block at same time
            if (self.is_moving() or not self.queue.empty()) and force:
                self.logger.warning('Forced commands cannot be queued, queue will be flushed!')
                self.logger.warning('Currently in queue - %s', self.queue.qsize())
                self.queue.empty()
                #return 'Failed'

            if self.is_moving():
                self.logger.warning('Another move running - command will be queued')
                self.queue.put_nowait(['move', dir, numsteps, force])
                return 'Queued'
            if block:
                self.doneevt.clear()
                self.queue.put_nowait(['move', dir, numsteps, force])
                self.logger.debug('Awaiting move completion')
                self.doneevt.wait()
                if self.error != 0:
                    return 'Failed'
                else:
                    return 'Done'
            else:
                if self.is_moving():
                    self.logger.warning('Another move running - command will be queued')
                self.queue.put_nowait(['move', dir, numsteps, force])
                return 'Queued'
        except queue.Full:
            return 'Fail'

    def home(self, dir):
        """
        Performs motor homing sequence, by default towards dir 0 (LIM_DN)
        :param dir:
        :return:
        """
        # Final safety checks
        assert dir in [Stepper.DIR_UP, Stepper.DIR_DN]

        # If queue is full, we reject command
        if self.queue.empty():
            if not self.state == IDLE or self.is_moving():
                self.logger.error('Attempt to home in state %s - very bad!', self.state)
                return False
            self.doneevt.clear()
            self.queue.put_nowait(['home', dir])
            self.doneevt.wait()
            return True
        else:
            self.logger.warning('Attempt to home with queued commands!')
            return False

    def enable(self, force=False):
        """
        Queues enabling of the motor

        :return: Result of operation
        """
        # Sanity checks - should already be filtered by webserver...
        if self.state == IDLE:
            self.logger.warning('Attempt to enable IDLE motor - BAD')
            return "Rejected"

        # If queue is not empty, reject command (no queue for enabling)
        if self.queue.empty():
            try:
                self.queue.put_nowait(['enable', force])
                return "Queued"
            except queue.Full:
                return "Rejected"
        else:
            return False

    def disable(self):
        """
        Queues enabling of the motor

        :return: Result of operation
        """
        # Sanity checks - should already be filtered by webserver...
        if self.state != IDLE:
            self.logger.warning('Attempt to disable not idle motor - BAD')
            return False

        # If queue is not empty, reject command (no queue for enabling)
        if self.queue.empty():
            try:
                self.queue.put_nowait(['disable'])
                return True
            except queue.Full:
                return False
        else:
            return False

    def stop(self):
        """
        Sets stop event indicator for the running thread

        :return:
        """
        # Thread will detect this event and react
        if self.state == MOVING:
            self.stopevt.set()
            return True
        else:
            self.logger.warning('Attempt to stop non-moving motor')
            return False

    def dump_state(self):
        results = {
            'fname': self.full_name,
            'name': self.name,
            'uuid': self.uuid,
            'state': self.state,
            'statestr': STATES_STR[self.state],
            'threadon': self.thread_on,
            'limup': GPIOMgr.get_pin_value(self.PIN_LIM_UP) == self.LIM_UP_HIT,
            'limdn': GPIOMgr.get_pin_value(self.PIN_LIM_DN) == self.LIM_DN_HIT,
        }
        if self.state == UNKNOWN or self.state == UNINITIALIZED:
            return results
        else:
            results.update({
                'fname': self.full_name,
                'name': self.name,
                'uuid': self.uuid,
                'state': self.state,
                'statestr': STATES_STR[self.state],
                'threadon': self.thread_on,
                'pos': self.position,
                'dir': self.direction,
                'queuesize': self.queue.qsize(),
                'limup': GPIOMgr.get_pin_value(self.PIN_LIM_UP) == self.LIM_UP_HIT,
                'limdn': GPIOMgr.get_pin_value(self.PIN_LIM_DN) == self.LIM_DN_HIT,
                'jerk': self.jerk,
                'vel': self.vel,
                'acc': self.acc
             })
            return results

    def state_hr(self):
        return "{} ({})".format(self.state, STATES_STR[self.state])

    def shutdown(self):
        """
        Shuts down motor, waiting to ensure thread is done
        """
        self.logger.info('Shutdown initiated - stopping and cleaning up')
        if self.is_moving():
            self.stop()
        self.thread_on = False
        time.sleep(0.5)
        if self.t.is_alive():
            self.logger.error('Control thread %s did not shut down in time!', self.t.name)
            return False
        else:
            #
            return True

    def names(self):
        """Gets both names in 'id (friendly name)' format
        :return:
        """
        return '{} ({})'.format(self.uuid, self.full_name)

    # Checks actual pin value for current direction
    def _get_direction(self):
        return GPIOMgr.get_pin_value(self.PIN_DIR)

    # Setting direction pin
    def _set_direction(self, dir):
        GPIOMgr.set_pin_value(self.PIN_DIR, dir)
        self.direction = dir

    # Checks actual pin value for current direction
    def is_enabled(self):
        return int(not GPIOMgr.get_pin_value(self.PIN_ENABLE))

    # Checks actual pin value for current direction
    def is_awake(self):
        return int(not GPIOMgr.get_pin_value(self.PIN_SLEEP))



class MoveException(Exception):
    pass

class MotorException(Exception):
    pass