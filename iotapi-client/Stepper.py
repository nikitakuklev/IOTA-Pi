import logging, time
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
            assert (all(x in Util.BCM_PINS for x in [Dr, St, En, Sl, LUp, LDn]))
            assert (all(x in [0,1] for x in [LUpState, LDnState]))
            assert (1 <= ptime < 1000)
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
            # Pulse time in ms (below 1 is not possible without RT kernel + C bindings)
            self.pulsetime = ptime/1000.0

            self.state = UNINITIALIZED
            self.moving = False

            self.logger.info('NEW Stepper (%s) aka (%s) with pins %d,%d,%d,%d (Dr,St,En,Sl)',name,fname,Dr, St, En, Sl)
        except:
            self.logger.exception("Failed to create stepper object")
            raise

    # Queries actual hardware for status (it can be non-default from previous runs for instance)
    def initialize(self, RPi=True):
        if (RPi):
            self.logger.info("Stepper %s - starting initialization", self.fname)
            self.direction = self._get_direction()
            self.position = 0
            self.enabled = self.is_enabled()
            self.awake = self.is_awake()
            if not self.awake or not self.enabled:
                self.logger.warning("New motor is either asleep or disabled")
            self.state = IDLE
            self.logger.info("Stepper %s initialized - dir %s, en %s, awk %s",
                             self.fname, self.direction, self.enabled, self.awake)
        else:
            self.logger.info("Stepper %s - starting initialization", self.fname)
            self.direction = 1
            self.position = 0
            self.enabled = 1
            self.awake = 1
            if not self.awake or not self.enabled:
                self.logger.warning("New motor is either asleep or disabled")
            self.state = IDLE
            self.logger.info("Stepper %s initialized - dir %s, en %s, awk %s",
                             self.fname, self.direction, self.enabled, self.awake)

    # For steppers, we can reset live without any further actions
    def reinitialize(self):
        if not self.moving:
            self.initialize()

    def dumpState(self):
        results = {}
        if self.state == UNKNOWN or self.state == UNINITIALIZED:
            results = {
                'Fname': self.fname,
                'State': self.state,
                'StateStr': STATES_STR[self.state],
            }
        else:
            results = {
                'Fname': self.fname,
                 'State': self.state,
                 'StateStr': STATES_STR[self.state],
                 'Position': self.position,
                 'Direction': self.direction
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

    # The main command that should be called by other functions
    def move(self, dir, numsteps, block=False):
        if not self.checkInterlocks():
            self.logger.warning('Motor %s interlock fail - move ignored!', self.uuid)
            return False

        if self.moving:
            self.logger.warning('Motor %s is currently moving!',self.uuid)
            #raise SystemError("Move ordered while another in progress")

        # Final safety checks
        assert(0 <= numsteps < 1000)
        dir = int(dir)
        numsteps = int(numsteps)

        self.logger.info("Motor %s move - %d steps in direction %d", self.uuid, numsteps, dir)

        if dir == Stepper.DIR_UP or dir == Stepper.DIR_DN:
            if dir != self.direction:
                self._set_direction(dir)
                self.logger.debug("Direction changed")
            else:
                self.logger.debug("Direction already correct")
        else:
            raise ValueError("Invalid direction specified")
        if numsteps == 0:
            self.logger.debug("Not moving since step number is 0")
        else:
            self.logger.debug("Starting movement")
            self.moving = True
            self._do_steps(numsteps)
            self.moving = False

    # Checks actual pin value for current direction
    def _get_direction(self):
        return GPIOMgr.get_pin_value(self.PIN_DIR)

    # Checks actual pin value for current direction
    def is_enabled(self):
        return GPIOMgr.get_pin_value(self.PIN_ENABLE)

    # Checks actual pin value for current direction
    def is_awake(self):
        return GPIOMgr.get_pin_value(self.PIN_SLEEP)

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

    # Rate limited stepping
    def _do_steps(self,numsteps,stepdelay):
        self.logger.debug("Doing %d steps with delay of %d", numsteps, stepdelay)
        for i in range(numsteps):
            GPIOMgr.pulse_pin(self.PIN_STEP, self.pulsetime)
            time.sleep(stepdelay)
            self.logger.debug("Step %d of %d done", i, numsteps)


