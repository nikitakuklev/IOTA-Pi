import logging, time, threading, queue
import Util
import GPIOMgr

IDLE = 100
MOVING = 200
ERROR = 300
UNKNOWN = -100
UNINITIALIZED = -50
STATES_STR = {100: 'IDLE', 200: 'MOVING', 300: 'ERROR', -100: 'UNKNOWN', -50: 'UNINITIALIZED'}


class Stepper:
    DIR_UP = 1
    DIR_DN = 0

    ESTOP = False

    position = -1
    state = UNKNOWN

    def __init__(self, uuid, name, fname, Dr, St, En, Sl, LUp, LDn, LUpState, LDnState, ptime):
        self.logger = logging.getLogger(__name__)
        try:
            #Sanity checks
            assert (all(x in Util.BCM_PINS for x in [Dr, St, En, Sl, LUp, LDn]))
            assert (all(x in [0,1] for x in [LUpState, LDnState]))
            assert (1 <= ptime < 1000)

            # Basic parameters
            self.uuid = uuid
            self.name = name
            self.fname = fname
            self.PIN_DIR = Dr
            self.PIN_STEP = St
            self.PIN_ENABLE = En
            self.PIN_SLEEP = Sl
            self.PIN_LIM_UP = LUp
            self.PIN_LIM_DN = LDn
            self.LIM_UP_HIT = LUpState
            self.LIM_DN_HIT = LDnState
            # Converting pulse time to s (below 1ms is not possible without RT kernel or C bindings)
            self.pulsetime = ptime/1000.0

            self.state = UNINITIALIZED
            self.moving = False
            self.threadon = False

            # Event indicating new command
            self.ev = threading.Event()
            self.cmdq = queue.Queue(maxsize=100)

            self.logger.info('NEW Stepper (%s) aka (%s) with pins %d,%d,%d,%d,%d,%d (Dr,St,En,Sl,LUp,LDn)',
                             name,fname,Dr, St, En, Sl, LUp, LDn)
        except:
            self.logger.exception("Failed to create stepper object")
            raise

    # Queries actual hardware for status (it can be non-default from previous runs for instance)
    def initialize(self, RPi=True):
        self.logger.info("Stepper %s - starting initialization", self.fname)
        set1 = [self.PIN_DIR, self.PIN_STEP, self.PIN_ENABLE, self.PIN_SLEEP]
        set2 = [self.PIN_LIM_UP, self.PIN_LIM_DN]
        # First read set 1
        GPIOMgr.set_mode_inputs(set1,GPIOMgr.GPIO.PUD_OFF)
        # Obtain current status
        if (RPi):
            self.direction = self._get_direction()
            self.position = 0
            self.enabled = self.is_enabled()
            self.awake = self.is_awake()
            if not self.awake or not self.enabled:
                self.logger.warning("New motor is either asleep or disabled")
            self.state = IDLE
        else:
            self.direction = 1
            self.position = 0
            self.enabled = 1
            self.awake = 1
            if not self.awake or not self.enabled:
                self.logger.warning("New motor is either asleep or disabled")
            self.state = IDLE
        # Now set proper outputs
        GPIOMgr.set_mode_outputs(set1)
        # Enable limit pullups
        GPIOMgr.set_mode_inputs(set2, GPIOMgr.GPIO.PUD_UP)
        self.logger.info("Motor %s initialized - dir %s, en %s, awk %s",
                             self.fname, self.direction, self.enabled, self.awake)

        # Create and start thread
        thread = threading.Thread(name='mt_thr_{}'.format(self.uuid), target=self.control_thread, args=())
        self.t = thread
        thread.setDaemon(False)
        thread.start()

    # For steppers, we can reset live without any further actions
    def reinitialize(self):
        if not self.moving:
            self.initialize()


    def control_thread(self):
        """
        Individual thread responsible for executing commands
        :return:
        """
        self.logger.info('Thread %s starting up', self.uuid)
        self.threadon = True
        try:
            while(self.threadon):
                # Get next msg
                try:
                    msg = self.cmdq.get(block=True, timeout=0.5)
                except queue.Empty:
                    pass
                else:
                    self.logger.info('Motor %s thread got command %s', self.uuid, msg)
                    if msg[0] == 'move':
                        if not self.checkInterlocks():
                            self.logger.warning('Motor %s interlock fail - move ignored!', self.uuid)
                            continue
                        dir = msg[1]
                        numsteps = msg[2]

                        # Acquire move lock to ensure only this motor will move
                        with GPIOMgr.movelock:
                            self.logger.info("Motor %s move - %d steps in direction %d", self.uuid, numsteps, dir)
                            self.status = MOVING
                            if dir != self.direction:
                                self._set_direction(dir)
                                self.logger.debug("Direction changed to %s", dir)
                            else:
                                self.logger.debug("Direction already correct")
                            self.status = IDLE

                            if numsteps == 0:
                                self.logger.debug("Not moving since step number is 0")
                            else:
                                stepdelay = 500
                                self.moving = True
                                self.status = MOVING
                                self.logger.debug("Doing %d steps with delay of %d ms", numsteps, stepdelay)
                                self._do_steps(numsteps,stepdelay)
                                self.moving = False
                                self.status = IDLE
                                self.logger.debug("Motion finished")
                    else:
                        continue
        except KeyboardInterrupt as e:
            self.logger.info('Thread %s KINT, shutting down',self.t.name)
            GPIOMgr.shutdown()
            self.threadon = False
        except Exception as e:
            self.logger.exception(e)
            self.threadon = False

    # # Rate limited stepping
    def _do_steps(self,numsteps,stepdelay):
        # Rate limited stepping
        for i in range(numsteps):
            GPIOMgr.pulse_pin(self.PIN_STEP, self.pulsetime)
            self.position += 1
            self.logger.debug("Step %d of %d done", i+1, numsteps)
            # time.sleep(stepdelay)
            # We await stop for the full delay period
            if self.ev.wait(timeout=stepdelay / 1000.0):
                # Stop command received - clear things out
                self.logger.debug("Stop command detected!")
                self.cmdq.queue.clear()
                break

    # The main command that should be called by other functions
    def move(self, dir, numsteps, block=False):
        # Final safety checks
        numsteps = int(numsteps)
        assert (0 <= numsteps < 1000)
        assert dir in [Stepper.DIR_UP, Stepper.DIR_DN]

        # If queue is full, we reject command
        try:
            self.cmdq.put_nowait(['move', dir, numsteps])
        except queue.Full:
            return False
        else:
            return True

    # def move(self, dir, numsteps, block=False):
    #     if not self.checkInterlocks():
    #         self.logger.warning('Motor %s interlock fail - move ignored!', self.uuid)
    #         return False
    #
    #     if self.moving:
    #         self.logger.warning('Motor %s is currently moving!', self.uuid)
    #         # raise SystemError("Move ordered while another in progress")
    #
    #     # Final safety checks
    #     assert (0 <= numsteps < 1000)
    #     dir = int(dir)
    #     numsteps = int(numsteps)
    #
    #     self.logger.info("Motor %s move - %d steps in direction %d", self.uuid, numsteps, dir)
    #
    #     if dir == Stepper.DIR_UP or dir == Stepper.DIR_DN:
    #         if dir != self.direction:
    #             self._set_direction(dir)
    #             self.logger.debug("Direction changed")
    #         else:
    #             self.logger.debug("Direction already correct")
    #     else:
    #         raise ValueError("Invalid direction specified")
    #     if numsteps == 0:
    #         self.logger.debug("Not moving since step number is 0")
    #     else:
    #         self.logger.debug("Starting movement")
    #         self.moving = True
    #         self._do_steps(numsteps)
    #         self.moving = False

    def stop(self):
        # Thread will detect this event and react
        self.ev.set()


    def dumpState(self):
        results = {}
        if self.state == UNKNOWN or self.state == UNINITIALIZED:
            results = {
                'Fname': self.fname,
                'State': self.state,
                'StateStr': STATES_STR[self.state],
                'ThreadOn': self.threadon
            }
        else:
            results = {
                'Fname': self.fname,
                'State': self.state,
                'StateStr': STATES_STR[self.state],
                'Position': self.position,
                'Direction': self.direction,
                'ThreadOn': self.threadon,
                'QueueSize': self.cmdq.qsize()
             }
        return results


    # Checks if there are any interlocks active
    def checkInterlocks(self):
        # First check ESTOP
        if self.ESTOP:
            self.logger.warning("Stepper %s - ESTOP interlock fail", self.fname)
            return False
        # Then check both limits
        if self.is_lim_reached(self.DIR_UP):
            self.logger.warning("Stepper %s - LIM UP fail", self.fname)
            return False
        if self.is_lim_reached(self.DIR_DN):
            self.logger.warning("Stepper %s - LIM DN fail", self.fname)
            return False
        # Nothing else for now, but we should add other sanity checks...
        self.logger.debug("Stepper %s - interlock check OK", self.fname)
        return True


    def shutdown(self):
        if self.moving:
            self.stop()
        self.threadon = False
        time.sleep(1.1)
        if self.t.is_alive():
            self.logger.error('Thread %s did not shut down in time!', self.t.name)
            return False
        else:
            return True

    # Checks actual pin value for current direction
    def _get_direction(self):
        return GPIOMgr.get_pin_value(self.PIN_DIR)

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

    # Setting direction pin
    def _set_direction(self, dir):
        GPIOMgr.set_pin_value(self.PIN_DIR, dir)
        self.direction = dir


