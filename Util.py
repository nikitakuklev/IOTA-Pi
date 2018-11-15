import logging

# CONSTANTS
# BCM RasPi3B pins that are valid for control assignment
BCM_PINS = [2,3,4,17,27,22,10,9,11,5,6,13,19,26,14,15,18,23,24,25,8,7,12,16,20,21]
# 2,3 have physical 1.8kOhms pullups
BCM_SPECIAL = [2,3]
BCM_PINS_PHIGH = [4,5,6,7,8] #these are pulled up on cold boot, also BANK1 (28+) omitted
# For some reason, 15 came up as HIGH too on boot
PCB_PINS = [3, 5, 7, 8, 10, 11, 12, 13, 15, 16, 18, 19, 21, 22, 23, 24, 26, 29, 31, 32, 33, 35, 36, 37, 38, 40]
PORT_FUNC = {0: "GPIO.OUT", 1: "GPIO.IN", 40: "GPIO.SERIAL",
             41: "GPIO.SPI", 42: "GPIO.I2C",
             43: "GPIO.HARD_PWM", -1: "GPIO.UNKNOWN"}

VERSION_MAJOR = 0
VERSION_MINOR = 3

# Logger
logger = logging.getLogger(__name__)


# Function to check if module is available - used in debugging configs
def module_exists(module_name):
    try:
        __import__(module_name)
    except ImportError:
        return False
    else:
        return True


# Initialize logging configuration with console+rotating logs
def init_logger(is_quiet):
    if not is_quiet:
        logging.config.dictConfig({
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "simple": {
                    "format": "%(asctime)s - %(levelname)s:%(lineno)d:%(name)s:%(threadName)s - %(message)s"
                }
            },

            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": "DEBUG",
                    "formatter": "simple",
                    "stream": "ext://sys.stdout"
                },

                "info_file_handler": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "INFO",
                    "formatter": "simple",
                    "filename": "info.log",
                    "maxBytes": 1048576,
                    "backupCount": 20,
                    "encoding": "utf8"
                },

                "error_file_handler": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "ERROR",
                    "formatter": "simple",
                    "filename": "errors.log",
                    "maxBytes": 1048576,
                    "backupCount": 20,
                    "encoding": "utf8"
                }
            },

            "loggers": {
                "my_module": {
                    "level": "ERROR",
                    "handlers": ["console"],
                    "propagate": "yes"
                }
            },

            "root": {
                "level": "DEBUG",
                "handlers": ["console", "info_file_handler", "error_file_handler"]
            }
        })
    else:
        logging.config.dictConfig({
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "simple": {
                    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                }
            },

            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": "DEBUG",
                    "formatter": "simple",
                    "stream": "ext://sys.stdout"
                },

                "info_file_handler": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "INFO",
                    "formatter": "simple",
                    "filename": "info.log",
                    "maxBytes": 1048576,
                    "backupCount": 20,
                    "encoding": "utf8"
                },

                "error_file_handler": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "ERROR",
                    "formatter": "simple",
                    "filename": "errors.log",
                    "maxBytes": 1048576,
                    "backupCount": 20,
                    "encoding": "utf8"
                }
            },

            "loggers": {
                "my_module": {
                    "level": "ERROR",
                    "handlers": ["console"],
                    "propagate": "yes"
                }
            },

            "root": {
                "level": "DEBUG",
                "handlers": ["info_file_handler", "error_file_handler"]
            }
        })
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
