import collections
import datetime
import logging
import socket

from flask import Flask, render_template, jsonify, request
from werkzeug.serving import WSGIRequestHandler

import GPIOMgr
import Main
import Stepper
import Util

app = Flask(__name__)

# Logger
logger = logging.getLogger("IOTAPI-worker")


def init_flask():
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    app.run(host='0.0.0.0', port=8080, use_reloader=False, debug=False, threaded=True)


@app.route("/")
def web_main():
    hname = socket.gethostname()
    fqdn = socket.getfqdn(hname)
    IP = getExternalIP()
    value_dict = {'SW version': '{}.{}'.format(Util.VERSION_MAJOR, Util.VERSION_MINOR),
                  'RPi.GPIO version': GPIOMgr.VERSION,
                  'Hostname': hname,
                  'FQDN': fqdn,
                  'IP': IP,
                  'RasPi model': GPIOMgr.RPI_REVISION,
                  'Time generated': datetime.datetime.now().strftime('%c')
                  }
    templateData = {
        'value_dict': collections.OrderedDict(value_dict),
        'pin_summary': GPIOMgr.gpio_summary()
    }
    return render_template('main.html', **templateData)


def getExternalIP():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except:
        return '127.0.0.1 (can\'t find public one!)'
    finally:
        s.close()


@app.route("/motors/", defaults={'motornum': -1})
@app.route("/motors/<motornum>/")
def web_motorview(motornum):
    """
    Create json for specified or all motors
    :return:
    """
    if Main.num_responses % 500 == 0:
        logger.debug('Upd %d - motor page accessed with parameter %s', Main.num_responses, motornum)
    Main.num_responses += 1
    try:
        motornum = int(motornum)
    except:
        return 'Invalid motor number', 400
    results = {}
    # print(list(GPIOMgr.motors.keys()))
    if motornum == -1:
        for uuid, mt in GPIOMgr.motors.items():
            results[mt.uuid] = mt.dump_state()
        return jsonify(results)
    elif motornum in GPIOMgr.motors.keys():
        results[motornum] = GPIOMgr.motors[motornum].dump_state()
        return jsonify(results)
    else:
        return 'Motor UUID not found', 400


@app.route("/move/", methods=['POST'])
def web_motor_command_move():
    logger.debug("Incoming move command %s", request.data)
    content = request.get_json(force=False, silent=True)
    if not request.is_json or content is None:
        logger.warning('Did not receive valid json!')
        return 'Did not receive valid json!', 400
    if 'uuid' not in content:
        logger.warning('No motor specified!')
        return 'No motor specified!', 400
    mtnum = content['uuid']
    if mtnum not in GPIOMgr.motors.keys():
        logger.warning('Nonexistent motor uuid specified!')
        return 'Nonexistent motor uuid specified!', 400
    motor = GPIOMgr.motors[mtnum]
    if 'dir' not in content or 'steps' not in content:
        logger.warning('No valid move parameters specified!')
        return 'No valid move parameters specified!', 400
    direction = int(content['dir'])
    steps = int(content['steps'])
    if not (0 <= steps < 1000000) or direction not in [0, 1]:
        logger.warning('Invalid move parameters specified!')
        return 'Invalid move parameters specified!', 400
    logger.info('M %s - move %s steps in dir %s ordered in state %s',
                motor.uuid, steps, direction, motor.state_hr())
    if not (motor.state == Stepper.IDLE or motor.state == Stepper.MOVING):
        logger.warning('M %s - in bad state %s', motor.uuid, motor.state_hr())
        return 'Motor {} in bad state {}'.format(motor.uuid, motor.state_hr()), 500
    else:
        block=False
        force=False
        if 'block' in content and str(content['block']) == '1':
            logger.debug('M %s - this will be a blocking move', motor.uuid)
            block = True
        if 'force' in content and str(content['force']) == '1':
            logger.debug('M %s - this will be a FORCED move', motor.uuid)
            force = True
        return motor.move(direction, steps, block, force)


@app.route("/config/motion", methods=['POST'])
def web_config_motion():
    logger.debug("Motion config command %s", request.data)
    content = request.get_json(force=False, silent=True)
    if not request.is_json or content is None:
        logger.warning('Did not receive valid json!')
        return 'Did not receive valid json!', 400
    if 'uuid' not in content:
        logger.warning('No motor specified!')
        return 'No motor specified!', 400
    mt_num = content['uuid']
    if mt_num not in GPIOMgr.motors.keys():
        logger.warning('Nonexistent motor uuid specified!')
        return 'Nonexistent motor uuid specified!', 400
    motor = GPIOMgr.motors[mt_num]
    if 'jerk' not in content:
        jerk = motor.jerk
    else:
        try:
            jerk = int(content['jerk'])
            assert 0 <= jerk <= 10000
        except:
            return 'Bad jerk parameter specified', 400

    if 'vel' not in content:
        vel = motor.vel
    else:
        try:
            vel = int(content['vel'])
            assert 0 < vel <= 10000
        except:
            return 'Bad vel parameter specified', 400

    if 'acc' not in content:
        acc = motor.acc
    else:
        try:
            acc = int(content['acc'])
            assert 0 < acc <= 10000
        except:
            return 'Bad acc parameter specified', 400

    logger.info('Motor %s - motion parameter change to %d %d %d (j/v/a)',
                motor.state_hr(), jerk, vel, acc)

    if not (motor.state == Stepper.IDLE or motor.state == Stepper.DISABLED):
        logger.warning('Motor %s in bad state %s', motor.uuid, motor.state_hr())
        return 'Motor {} in bad state {}'.format(motor.uuid, motor.state_hr()), 500
    else:
        motor.jerk = jerk
        motor.vel = vel
        motor.acc = acc
        return 'OK'


@app.route("/enable/", methods=['POST'])
def web_motor_command_enable():
    logger.debug("Incoming enable command: %s", request.data)
    content = request.get_json(force=False, silent=True)
    if not request.is_json or content is None:
        logger.warning('Did not receive valid json!')
        return 'Did not receive valid json!', 400
    if 'uuid' not in content:
        logger.warning('No motor specified!')
        return 'No motor specified!', 400
    mtnum = content['uuid']
    if mtnum not in GPIOMgr.motors.keys():
        logger.warning('Nonexistent motor uuid specified!')
        return 'Nonexistent motor uuid specified!', 400
    motor = GPIOMgr.motors[mtnum]
    logger.info('M %s - enable ordered in state %s', motor.uuid, motor.state_hr())
    if not motor.state == Stepper.DISABLED:
        logger.warning('M %s in bad state %s', motor.uuid, motor.state_hr())
        return 'Motor {} in bad state {}'.format(motor.uuid, motor.state_hr()), 500
    else:
        if 'force' in content and str(content['force']) == '1':
            return motor.enable(force=True)
        else:
            return motor.enable(force=False)


@app.route("/disable/", methods=['POST'])
def web_motor_command_disable():
    logger.debug("Incoming disable command: %s", request.data)
    content = request.get_json(force=False, silent=True)
    if not request.is_json or content is None:
        logger.warning('Did not receive valid json!')
        return 'Did not receive valid json!', 400
    if 'uuid' not in content:
        logger.warning('No motor specified!')
        return 'No motor specified!', 400
    mtnum = content['uuid']
    if mtnum not in GPIOMgr.motors.keys():
        logger.warning('Nonexistent motor uuid specified!')
        return 'Nonexistent motor uuid specified!', 400
    motor = GPIOMgr.motors[mtnum]
    logger.info('M %s - disable ordered in state %s', motor.uuid, motor.state_hr())
    if not motor.state == Stepper.IDLE:
        logger.warning('M %s - in bad state %s', motor.uuid, motor.state_hr())
        return 'Motor {} in bad state {}'.format(motor.uuid, motor.state_hr()), 500
    else:
        return 'OK' if motor.disable() else 'FAIL'


@app.route("/stop/", methods=['POST'], strict_slashes=False)
def web_motor_abort():
    """
    Stops current and all queued actions, for all motors unless specified otherwise
    :return:
    """
    logger.info("Incoming abort command: %s", request.data)
    content = request.get_json(force=False, silent=True)
    if not request.is_json or content is None:
        logger.warning('Did not receive valid json - aborting all')
        mt = None
    else:
        if 'uuid' not in content:
            logger.warning('No motor specified - aborting all!')
            mt = None
        else:
            mtnum = content['uuid']
            if mtnum not in GPIOMgr.motors.keys():
                logger.warning('Nonexistent motor uuid specified!')
                return 'Nonexistent motor uuid specified!', 400
            mt = GPIOMgr.motors[mtnum]
    mts = [mt] if mt else GPIOMgr.motors.values()
    results = {}
    for mt in mts:
        state = mt.state
        if state == Stepper.UNINITIALIZED:
            logger.warning('M %s - attempt to stop while UNINITIALIZED', mt.uuid)
            results[mt.uuid] = 'Failed, uninitialized!'
        elif state == Stepper.IDLE:
            logger.warning('M %s - attempt to stop while IDLE', mt.uuid)
            results[mt.uuid] = 'Failed, already idle!'
        elif state == Stepper.DISABLED:
            logger.warning('M %s - attempt to stop while DISABLED', mt.uuid)
            results[mt.uuid] = 'Failed, already disabled!'
        else:
            logger.info('STOP for motor %s in state %s', mt.uuid, state_hr())
            results[mt.uuid] = 'OK' if mt.stop() else 'FAIL'
    return jsonify(results)


@app.route("/shutdown/", methods=['POST'], strict_slashes=False)
def web_shutdown():
    shutdown()
    return 'Goodbye...'


def shutdown():
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


# Utility pages for debugging mostly
@app.route("/config/")
def web_dump_config():
    """
    Spit out current config
    """
    return jsonify(GPIOMgr.config_raw)


@app.errorhandler(404)
def page_not_found(e):
    """
    Return raw error code only

    :param e:
    :return:
    """
    return '', 404


@app.errorhandler(400)
def bad_request(e):
    """
    Return raw error code only

    :param e:
    :return:
    """
    return '', 400
