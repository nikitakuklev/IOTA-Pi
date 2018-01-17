from flask import Flask, render_template, jsonify, request, logging, abort
import socket, collections, datetime
import GPIOMgr, Stepper, Util

app = Flask(__name__)

# Logger
logger = logging.getLogger(__name__)

def init_flask():
    app.run(host='0.0.0.0', port=8080, debug=True)


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
        return'127.0.0.1 (can\'t find public one!)'
    finally:
        s.close()


@app.route("/motors/", defaults={'motornum': -1})
@app.route("/motors/<motornum>/")
def web_motorview(motornum):
    """
    Create json for specified or all motors
    :return:
    """
    try:
        motornum = int(motornum)
    except:
        return 'Invalid motor number', 400
    results = {}
    #print(list(GPIOMgr.motors.keys()))
    logger.debug('Motor page accessed with parameter %s', motornum)
    if motornum == -1:
        for uuid, mt in GPIOMgr.motors.items():
            results[uuid] = mt.dumpState()
        return jsonify(results)
    elif motornum in GPIOMgr.motors.keys():
        results[motornum] = GPIOMgr.motors[motornum].dumpState()
        return jsonify(results)
    else:
        return 'Motor UUID not found', 400

@app.route("/move/", methods=['POST'])
#@app.route("/motors/move/<motornum>", methods=['POST'])
def web_motorcommand():
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
    mt = GPIOMgr.motors[mtnum]
    if 'dir' not in content or 'steps' not in content:
        logger.warning('No valid move parameters specified!')
        return 'No valid move parameters specified!', 400
    direction = int(content['dir'])
    steps = int(content['steps'])
    if not (0 <= steps < 1000) or direction not in [0,1]:
        logger.warning('Invalid move parameters specified!')
        return 'Invalid move parameters specified!', 400
    state = mt.state
    logger.info('Move %s steps in dir %s ordered for motor %s in state %s', steps, direction, mt.uuid, state)
    if mt.state != Stepper.IDLE:
        logger.warning('Motor %s in invalid state %s', mt.uuid, state)
    if 'block' in content and content['block']:
        moveok = mt.move(direction, steps, block=True)
    else:
        moveok = mt.move(direction, steps)
    return str(moveok)


@app.route("/stop/", methods=['POST'])
#@app.route("/motors/stop")
def web_motorabort():
    logger.info("Incoming abort command %s", request.data)
    content = request.get_json(force=False, silent=True)
    if not request.is_json or content is None:
        logger.warning('Did not receive valid json!')
        return 'Did not receive valid json!', 400
    if 'uuid' not in content:
        logger.warning('No motor specified - aborting all!')
        mt = None
    else:
        mtnum = content['uuid']
        if mtnum not in GPIOMgr.motors.keys():
            logger.warning('Nonexistent motor uuid specified!')
            return 'Nonexistent motor uuid specified!', 400
        mt = GPIOMgr.motors[mtnum]
    if mt:
        mts = [mt]
    else:
        mts = GPIOMgr.motors.values()
    for mt in mts:
        state = mt.state
        if state == Stepper.UNINITIALIZED:
            logger.warning('Attempt to stop uninitialized motor %s', mt.uuid)
        else:
            logger.info('STOP for motor %s in state %s', mt.uuid, state)
            mt.stop()
    return 'OK'

@app.route("/shutdown/", methods=['POST'])
def web_shutdown():
    sthelper()
    return 'Goodbye...'

def sthelper():
    GPIOMgr.shutdown()
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()

# Utility pages for debugging mostly
@app.route("/config/")
def web_configview():
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