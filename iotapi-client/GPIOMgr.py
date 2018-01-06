import collections, sys, os, logging, time
import Util

logger = logging.getLogger(__name__)
motors = {}
lockout = True # Currently used for blocking actual output changes during testing

# Test if we are on actual RPi
try:
    import RPi.GPIO as GPIO
except ImportError:
    isRPi =  False
else:
    isRPi =  True

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
        if mtprev.name == mt.name or mtprev.fname == mt.fname:
            raise ValueError("Attempt to add a motor with same name attribute (%s)(%s)!")

    motors[mt.name] = mt
    logger.debug("Added motor %s to the control list", mt.name)

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
            else:
                raise AttributeError("State %s is not valid for pin %d", state, pin)
        else:
            raise AttributeError("Pin %d is not in output mode", pin)
    else:
        pass

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
    time.sleep(tm)
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
    if (isRPi):
        import RPi.GPIO as GPIO
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