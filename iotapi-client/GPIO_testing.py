import RPi.GPIO as GPIO
import logging, logging.config, collections, socket
import sys, os
from flask import Flask, render_template, jsonify, request

logger = logging.getLogger(__name__)

def init_logger():
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
                "propagate": "no"
            }
        },

        "root": {
            "level": "DEBUG",
            "handlers": ["console", "info_file_handler", "error_file_handler"]
        }
    })

app = Flask(__name__)

def init_flask():
    app.run(host='0.0.0.0', port=8080, debug=True)

@app.route("/")
def web_main():

    hname = socket.gethostname()
    fqdn = socket.getfqdn(hname)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1 (can\'t find public one!)'
    finally:
        s.close()
    value_dict = {'SW version': '0.1',
                  'RPi.GPIO version': GPIO.VERSION,
                  'Hostname': hname,
                  'FQDN': fqdn,
                  'IP': IP,
                  'RasPi model': GPIO.RPI_REVISION,
    }
    templateData = {
        'value_dict': collections.OrderedDict(value_dict),
        'pin_summary': gpio_summary()
    }
    return render_template('main.html', **templateData)

@app.route("/motors/")
def web_motorviewall():
    value_dict = {'Motor': 1,
                  'Position': 5,
                  'State': 'IDLE',
    }
    return jsonify([value_dict,value_dict])

@app.route("/motors/<motornum>/")
def web_motorview(motornum):
    value_dict = {'Motor': motornum,
                  'Position': 5,
                  'State': 'IDLE',
    }
    return jsonify(value_dict)

@app.route("/motors/move", methods=['POST'])
def web_motorcommand():
    logger.debug("Incoming move command")
    if not request.is_json:
        raise ValueError("Did not receive valid json")
    content = request.get_json()
    logger.debug(content)
    assert('motornum' in content.keys())
    motornum = content['motornum']
    direction = content['dir']
    stepamt = content['steps']

    return "POST OK"

def gpio_summary():
    GPIO.setmode(GPIO.BCM)
    ports_board = [3,5,7,8,10,11,12,13,15,16,18,19,21,22,23,24,26,29,31,32,33,35,36,37,38,40]
    ports = [2,3,4,17,27,22,10,9,11,5,6,13,19,26,14,15,18,23,24,25,8,7,12,16,20,21]
    port_use = {0: "GPIO.OUT", 1: "GPIO.IN", 40: "GPIO.SERIAL", 41: "GPIO.SPI", 42: "GPIO.I2C",
                43: "GPIO.HARD_PWM", -1: "GPIO.UNKNOWN"}
    summary = collections.OrderedDict()
    for idx, port in enumerate(ports):
        usage = GPIO.gpio_function(port)
        try:
            value = GPIO.input(port)
        except:
            value = '-'
        summary[port] = {'pin_bcm': port, 'pin_pcb':ports_board[idx],'state':port_use[usage],'value':value}
    return summary

def main():
    try:
        init_logger()
        init_flask()
        logger = logging.getLogger(__name__)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(18,GPIO.OUT)
        GPIO.output(18,GPIO.LOW)

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

if __name__ == '__main__':
    main()