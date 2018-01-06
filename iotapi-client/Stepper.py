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
    # name = ""
    # fname = ""
    #
    # PIN_ENABLE = 0
    # PIN_SLEEP = 0
    # PIN_DIR = 0
    # PIN_STEP = 0

    DIR_UP = 1
    DIR_DN = 0

    position = -1
    state = UNKNOWN

    def __init__(self, name, fname, Dr, St, En, Sl, ptime):
        self.logger = logging.getLogger(__name__)
        try:
            assert (Dr in Util.BCM_PINS)
            assert (St in Util.BCM_PINS)
            assert (En in Util.BCM_PINS)
            assert (Sl in Util.BCM_PINS)
            assert (1 <= ptime < 1000)
            self.name = name
            self.fname = fname
            self.PIN_DIR = Dr
            self.PIN_STEP = St
            self.PIN_ENABLE = En
            self.PIN_SLEEP = Sl
            # Pulse time in ms (below 1 is not possible without RT kernel + C bindings)
            self.pulsetime = ptime/1000.0

            self.state = UNINITIALIZED

            self.logger.info('New stepper (%s) aka (%s) with pins %d,%d,%d,%d (Dr,St,En,Sl)',name,fname,Dr, St, En, Sl)
        except:
            self.logger.exception("Failed to create stepper object")
            raise

    # Queries actual hardware for status (it can be non-default from previous runs for instance)
    def initialize(self):
        self.direction = self._get_direction()
        self.position = 0
        self.enabled = self.is_enabled()
        self.awake = self.is_awake()
        self.state = IDLE
        self.logger.info("Stepper %s initialized!",self.fname)

    # The main command that should be called by other functions
    def move(self, dir, numsteps):
        self.logger.info("%s move - %d steps in direction %d", self.name, numsteps, dir)
        numsteps = int(numsteps)
        assert(0 <= numsteps < 1000)
        if dir == Stepper.DIR_UP or dir == Stepper.DIR_DN:
            if dir != self.direction:
                self._set_direction(dir)
                self.logger.debug("Direction changed")
            else:
                self.logger.debug("Direction already correct")
        else:
            raise ValueError("Invalid direction specified")
        if (numsteps == 0):
            self.logger.debug("Not moving since step number is 0")
        else:
            self.logger.debug("Starting movement")
            self._do_steps(numsteps)

    # Checks actual pin value for current direction
    def _get_direction(self):
        return GPIOMgr.get_pin_value(self.PIN_DIR)

    # Checks actual pin value for current direction
    def is_enabled(self):
        return GPIOMgr.get_pin_value(self.PIN_ENABLE)

    # Checks actual pin value for current direction
    def is_awake(self):
        return GPIOMgr.get_pin_value(self.PIN_SLEEP)

    # Setting direction pin
    def _set_direction(self, dir):
        GPIOMgr.set_pin_value(self.PIN_DIR, dir)

    def _do_steps(self,numsteps):
        for i in range(numsteps):
            GPIOMgr.pulse_pin(self.PIN_STEP)
            self.logger.debug("Step %d of %d", i, numsteps)


