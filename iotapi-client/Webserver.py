from flask import Flask, render_template, jsonify, request, logging
import socket, collections
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
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1 (can\'t find public one!)'
    finally:
        s.close()
    value_dict = {'SW version': '{}.{}'.format(Util.VERSION_MAJOR,Util.VERSION_MINOR),
                  'RPi.GPIO version': GPIOMgr.VERSION,
                  'Hostname': hname,
                  'FQDN': fqdn,
                  'IP': IP,
                  'RasPi model': GPIOMgr.RPI_REVISION,
    }
    templateData = {
        'value_dict': collections.OrderedDict(value_dict),
        'pin_summary': GPIOMgr.gpio_summary()
    }
    return render_template('main.html', **templateData)

@app.route("/motors/")
def web_motorviewall():
    results = {}
    for name,mt in GPIOMgr.motors.items():
        if mt.state == Stepper.UNKNOWN or mt.state == Stepper.UNINITIALIZED:
            results[name] = {
                          'Fname': mt.fname,
                          'State': mt.state,
                          'StateStr': Stepper.STATES_STR[mt.state],
            }
        else:
            results[name] = {'Fname': mt.fname,
                          'State': mt.state,
                          'StateStr': Stepper.STATES_STR[mt.state],
                          'Position': mt.position,
                          'Direction': mt.direction
            }
        #results.append(value_dict)
    return jsonify(results)



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

@app.errorhandler(404)
def page_not_found(e):
    return '', 404