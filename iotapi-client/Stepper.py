import logging, time, threading, queue
import Util
import GPIOMgr

DISABLED = 50
IDLE = 100
MOVING = 200
ERROR = 300
UNKNOWN = -100
UNINITIALIZED = -50
STATES_STR = {50: 'DISABLED', 100: 'IDLE', 200: 'MOVING', 300: 'ERROR', -100: 'UNKNOWN', -50: 'UNINITIALIZED'}


class Stepper:
    DIR_UP = 1
    DIR_DN = 0

    ESTOP = False

    position = -1
    state = UNKNOWN

    def __init__(self, uuid, name, fname, Dr, St, En, Sl, LUp, LDn, LUpState, LDnState, st_size, ptime, st_dtime, aen, adis):
        self.logger = logging.getLogger(__name__+'.'+str(uuid))
        try:
            #Sanity checks
            assert (all(x in Util.BCM_PINS for x in [Dr, St, En, Sl, LUp, LDn]))
            assert (all(x in [0,1] for x in [LUpState, LDnState]))
            assert (1 <= ptime < 1000)

            # Basic parameters
            self.uuid = uuid
            self.name = name
            self.full_name = fname
            self.PIN_DIR = Dr
            self.PIN_STEP = St
            self.PIN_ENABLE = En
            self.PIN_SLEEP = Sl
            self.PIN_LIM_UP = LUp
            self.PIN_LIM_DN = LDn
            self.LIM_UP_HIT = LUpState
            self.LIM_DN_HIT = LDnState
            self.step_size = st_size
            # Converting pulse time to s (below 1ms is not possible without RT kernel or C bindings)
            self.pulse_time = ptime / 1000.0
            self.step_delay = st_dtime / 1000.0

            self.auto_enable = bool(aen)
            self.auto_disable = bool(adis)

            self.state = UNINITIALIZED
            self.moving = False
            self.thread_on = False

            # Event indicating new command
            self.stopevt = threading.Event()
            self.doneevt = threading.Event()
            self.cmdq = queue.Queue(maxsize=100)

            self.logger.info('NEW Stepper (%s) aka (%s) with pins %d,%d,%d,%d,%d,%d (Dr,St,En,Sl,LUp,LDn)',
                             name,fname,Dr, St, En, Sl, LUp, LDn)
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
                         self.full_name, self.direction, self.enabled, self.awake)

        # Create and start thread
        thread = threading.Thread(name='mt_thr_{}'.format(self.uuid), target=self.control_thread, args=())
        self.t = thread
        thread.setDaemon(False)
        thread.start()

        self.state = DISABLED

    # For steppers, we can reset live without any further actions
    def reinitialize(self):
        if not self.moving:
            self.initialize()


    def control_thread(self):
        """
        Independent thread responsible for executing motor commands
        :return:
        """
        self.logger.info('Thread %s starting up', self.uuid)
        self.thread_on = True
        try:
            while self.thread_on:
                # Get next msg
                try:
                    msg = self.cmdq.get(block=True, timeout=0.05)
                except queue.Empty:
                    pass
                else:
                    self.logger.info('M %s - thread command %s', self.uuid, msg)
                    if msg[0] == 'move':
                        if self.check_interlocks():
                            self.logger.warning('Motor %s interlock fail - move ignored!', self.uuid)
                            continue
                        dir = msg[1]
                        numsteps = msg[2]

                        # Acquire move lock to ensure only this motor will move
                        with GPIOMgr.movelock:
                            self.logger.info("M %s - move %d steps in direction %d", self.uuid, numsteps, dir)
                            if dir != self.direction:
                                self.state = MOVING
                                self._set_direction(dir)
                                self.state = IDLE
                                self.logger.debug("Direction changed to %s", dir)
                            else:
                                self.logger.debug("Direction already correct")

                            if numsteps == 0:
                                self.logger.debug("Not moving since step number is 0")
                            else:
                                if self.state == DISABLED:
                                    if self.auto_enable:
                                        self._enable_direct()
                                    else:
                                        self.logger.warning('M %s - not enabled, ignoring move command!', self.uuid)
                                        continue
                                self.state = MOVING
                                self.logger.debug("Doing %d steps with delay of %d ms", numsteps, self.step_delay)
                                try:
                                    result = self._do_steps(numsteps, self.step_delay)
                                except MoveException as e:
                                    self.logger.warning("Interlock |%s| triggered during move!", e)
                                    result = -2
                                self.logger.info("Motion finished, result code: %s", result)
                                self.state = IDLE
                            if self.auto_disable:
                                self._disable_direct()
                            self.doneevt.set()
                    elif msg[0] == 'enable':
                        if not self.check_interlocks():
                            self.logger.warning('M %s - interlock fail, enable ignored!', self.uuid)
                            continue

                        # Acquire move lock
                        with GPIOMgr.movelock:
                            self._enable_direct()
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


    def _do_steps(self, numsteps, stepdelay):
        # Rate limited stepping
        for i in range(numsteps):
            self.check_interlocks()
            GPIOMgr.pulse_pin(self.PIN_STEP, self.pulse_time)
            self.position += 1
            self.check_interlocks()
            self.logger.debug("Step %d of %d done", i+1, numsteps)
            # time.sleep(stepdelay)
            # We await stop for the full delay period
            if self.stopevt.wait(timeout=stepdelay / 1000.0):
                # Stop command received - clear things out
                self.logger.debug("Stop command detected!")
                self.cmdq.queue.clear()
                self.stopevt.clear()
                return -1
        return 0

    def check_interlocks(self, raise_exc=False):
        """
        Checks if any of interlocks are active
        :return:
        """
        # First check ESTOP
        if self.ESTOP:
            self.logger.warning("M %s - ESTOP interlock fail", self.full_name)
            if raise_exc:
                raise MoveException("ESTOP")
            else:
                return 1
        # Then check both limits
        if self.is_lim_reached(self.DIR_UP):
            self.logger.warning("M %s - LIM UP fail", self.full_name)
            if raise_exc:
                raise MoveException("UP")
            else:
                return 2
        if self.is_lim_reached(self.DIR_DN):
            self.logger.warning("M %s - LIM DN fail", self.full_name)
            if raise_exc:
                raise MoveException("DN")
            else:
                return 3
        # Nothing else for now, but we should add other sanity checks...
        self.logger.debug("M %s - interlock check OK", self.full_name)
        return 0

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


    def move(self, dir, numsteps, block=False):
        """
        Performs motor steps
        :param dir:
        :param numsteps:
        :param block:
        :return:
        """
        # Final safety checks
        numsteps = int(numsteps)
        assert (0 <= numsteps < 1000)
        assert dir in [Stepper.DIR_UP, Stepper.DIR_DN]

        # If queue is full, we reject command
        try:
            if self.moving and block:
                return False #unsupported queue and block at same time
            if self.moving:
                self.logger.warning('Another move running - this command will be queued')
                self.cmdq.put_nowait(['move', dir, numsteps])
            elif block:
                self.doneevt.clear()
                self.cmdq.put_nowait(['move', dir, numsteps])
                self.doneevt.wait()
            else:
                self.cmdq.put_nowait(['move', dir, numsteps])
        except queue.Full:
            return False
        else:
            return True


    def enable(self):
        """
        Queues enabling of the motor

        :return: Result of operation
        """
        # Sanity checks - should already be filtered by webserver...
        if self.state == IDLE:
            self.logger.warning('Attempt to enable IDLE motor - BAD')
            return False

        # If queue is not empty, reject command (no queue for enabling)
        if self.cmdq.empty():
            try:
                self.cmdq.put_nowait(['enable'])
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
        results = {}
        if self.state == UNKNOWN or self.state == UNINITIALIZED:
            results = {
                'Fname': self.full_name,
                'State': self.state,
                'StateStr': STATES_STR[self.state],
                'ThreadOn': self.thread_on
            }
        else:
            results = {
                'Fname': self.full_name,
                'State': self.state,
                'StateStr': STATES_STR[self.state],
                'Position': self.position,
                'Direction': self.direction,
                'ThreadOn': self.thread_on,
                'QueueSize': self.cmdq.qsize()
             }
        return results
#
    def shutdown(self):
        """
        Shuts down motor, waiting to ensure thread is done
        """
        self.logger.info('M %s (%s) shutting down!', self.uuid, self.full_name)
        if self.moving:
            self.stop()
        self.thread_on = False
        time.sleep(0.6)
        if self.t.is_alive():
            self.logger.error('M %s - thread %s did not shut down in time!', self.uuid, self.t.name)
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

    # Checks if limit reached (indicated by LOW, closed circuit)
    def is_lim_reached(self, direction):
        if direction == self.DIR_DN:
            return GPIOMgr.get_pin_value(self.PIN_LIM_DN) == self.LIM_DN_HIT
        elif direction == self.DIR_UP:
            return GPIOMgr.get_pin_value(self.PIN_LIM_UP) == self.LIM_UP_HIT
        else:
            raise ValueError("Wrong limit check direction")

class MoveException(Exception):
    pass