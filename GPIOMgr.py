import collections, sys, os, logging, time, threading
import Util

logger = logging.getLogger(__name__)
motors = collections.OrderedDict()
config_raw = None
lockout = False # Currently used for blocking actual output changes during testing

movelock = threading.Lock()

# Test if we are on actual RPi
try:
    import RPi.GPIO as GPIO
    isRPi = True
except ImportError:
    isRPi = False

# Setup some constants
if (isRPi):
    VERSION = GPIO.VERSION
    RPI_REVISION = GPIO.RPI_REVISION
else:
    VERSION = '0.NOTRPI.0'
    RPI_REVISION = '-1'


# Adds motor to the master list, ensuring uniqueness of names and pins
# Note that motors are disabled on creation, so this can be done after Stepper object is made
def addMotor(mt):
    for mtprev in motors.values():
        if mtprev.uuid == mt.uuid or mtprev.name == mt.name or mtprev.full_name == mt.full_name:
            raise ValueError("Attempt to add a motor with repeat name attributes!")

    motors[mt.uuid] = mt
    logger.debug("Added motor %s (%s) to the control list", mt.uuid, mt.full_name)


# Runs actual initialization for all declared motors
def init_motors():
    if (isRPi):
        # First, set all pins to defaults
        GPIO.setmode(GPIO.BCM)
        for pin in Util.BCM_PINS:
            if GPIO.gpio_function(pin) == GPIO.IN:
                if pin in Util.BCM_PINS_PHIGH:
                    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                elif pin in Util.BCM_SPECIAL:
                    GPIO.setup(pin, GPIO.IN)
                else:
                    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            else:
                logger.warning('Pin %d not in input mode during init', pin)
                #raise AttributeError("Pins not inputs during motor init")

        # Run initialization
        for m in motors.values():
            m.initialize()
    else:
        # Run initialization
        for m in motors.values():
            m.initialize(RPi=False)


# Sanity check wrapper
def get_pin_value(pin):
    assert pin in Util.BCM_PINS
    if isRPi:
        return GPIO.input(pin)
    else:
        return -1


# Sanity check wrapper
def set_pin_value(pin, state):
    assert pin in Util.BCM_PINS
    if isRPi and not lockout:
        if GPIO.gpio_function(pin) == GPIO.OUT:
            if state == GPIO.LOW or state == GPIO.HIGH:
                GPIO.output(pin, state)
                #logger.debug('Pin %s to %s',pin,state)
            else:
                raise AttributeError("State %s is not valid for pin %d", state, pin)
        else:
            raise AttributeError("Pin %d is not in output mode", pin)
    else:
        pass


def set_mode_inputs(pins, pud):
    for pin in pins:
        assert pin in Util.BCM_PINS
        #assert GPIO.gpio_function(pin) == GPIO.IN
    if isRPi and not lockout:
        logger.info('Setting pins %s to pullup state %s',pins,pud)
        GPIO.setup(pins, GPIO.IN, pull_up_down = pud)
    else:
        logger.info('Fake setting pullups pins %s',pins)


def set_mode_outputs(pins, initial):
    for pin in pins:
        assert pin in Util.BCM_PINS
        #assert GPIO.gpio_function(pin) == GPIO.IN
    if isRPi and not lockout:
        logger.info('Setting pins %s to outputs, initial %s',pins,initial)
        if initial is not None:
            GPIO.setup(pins, GPIO.OUT, initial=initial)
        else:
            GPIO.setup(pins, GPIO.OUT)
    else:
        logger.info('Fake setting pullups pins %s',pins)


def toggle_pin_value(pin):
    assert pin in Util.BCM_PINS
    if isRPi and not lockout:
        if GPIO.gpio_function(pin) == GPIO.OUT:
            state = GPIO.input(pin)
            if state == GPIO.LOW:
                GPIO.output(pin, GPIO.HIGH)
            else:
                GPIO.output(pin, GPIO.LOW)
        else:
            raise AttributeError("Pin %d is not in output mode", pin)
    else:
        pass


# Pulsing pin (typically step pin) for specified time period
def pulse_pin(pin, tm):
    set_pin_value(pin, GPIO.HIGH)
    if tm > 0: time.sleep(tm)
    set_pin_value(pin, GPIO.LOW)


def test1():
    if (isRPi):
        #import RPi.GPIO as GPIO
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(18, GPIO.OUT)
            GPIO.output(18, GPIO.LOW)

            r1 = GPIO.getmode(18)
            r2 = GPIO.input(18)
            r3 = GPIO.LOW
            print('???', r1, r2, r3)
            logger.info('bla')
        except KeyboardInterrupt:
            print("KB_INT - shutting down")
            GPIO.cleanup()
            try:
                sys.exit(0)
            except SystemExit:
                os._exit(0)

        finally:
            GPIO.cleanup()


def gpio_summary():
    if isRPi:
        GPIO.setmode(GPIO.BCM)
        summary = collections.OrderedDict()
        for idx, port in enumerate(Util.BCM_PINS):
            usage = GPIO.gpio_function(port)
            try:
                value = GPIO.input(port)
            except:
                value = '-'
            summary[port] = {'pin_bcm': port, 'pin_pcb': Util.PCB_PINS[idx], 'state': Util.PORT_FUNC[usage],
                             'value': value}
        return summary
    else:
        summary = collections.OrderedDict()
        for idx, port in enumerate(Util.BCM_PINS):
            usage = -1
            value = '(NOT RPI)'
            summary[port] = {'pin_bcm': port, 'pin_pcb': Util.PCB_PINS[idx], 'state': Util.PORT_FUNC[usage],
                             'value': value}
        return summary


def shutdown():
    try:
        logger.info('Shutting down motors')
        for mt in motors.values():
            mt.shutdown()
        logger.info('GPIO cleanup on shutdown')
        GPIO.cleanup()
        logger.info('Finally, setting not-enable pins high')
        GPIO.setmode(GPIO.BCM)
        set_mode_outputs([m.PIN_ENABLE for m in motors.values()], GPIO.HIGH)
    except Exception:
        logger.fatal("Shudown failure, exiting dirty...", exc_info=True)
