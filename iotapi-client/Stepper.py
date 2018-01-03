import logging
import Util

class Stepper:
    PIN_ENABLE = 0
    PIN_SLEEP = 0
    PIN_DIR = 0
    PIN_STEP = 0

    logger = logging.getLogger(__name__)

    position = 0
    step_size = 1.0

    def __init__(self, Dr, St, En, Sl):
        try:
            assert (Dr in Util.BCM_PINS)
            assert (St in Util.BCM_PINS)
            assert (En in Util.BCM_PINS)
            assert (Sl in Util.BCM_PINS)
            self.PIN_DIR = Dr
            self.PIN_STEP = St
            self.PIN_ENABLE = En
            self.PIN_SLEEP = Sl
        except e:
            logger.exception()


