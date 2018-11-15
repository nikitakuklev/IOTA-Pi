import argparse
import logging.config
import json
import sys
import os
import signal

import Util, GPIOMgr
import Webserver
from Stepper import Stepper

logger = logging.getLogger(__name__)
num_responses = 0

def load_config(config_path):
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            if config['compatible_with'] != Util.VERSION_MAJOR:
                logger.fatal("Incompatible config specified - aborting")
                sys.exit(3)
            if config.get('motors'):
                for k, motor in sorted(config['motors'].items(), key=lambda t: t[1]['uuid']):
                    mt = Stepper(motor['uuid'],k,motor['friendly_name'],motor['pin_direction'],motor['pin_step'],
                                 motor['pin_enable'],motor['pin_sleep'],motor['pin_lim_up'],motor['pin_lim_dn'],
                                 motor['lim_up_state'],motor['lim_dn_state'],motor['step_size'],
                                 motor['step_pulse_time'],motor['step_delay_time'],
                                 motor['autoenable'],motor['autodisable'],
                                 motor['jerk'], motor['velocity'], motor['acceleration'])
                    GPIOMgr.addMotor(mt)
            else:
                logger.warning('No motors found in config file!')
            GPIOMgr.config_raw = config
    except SystemExit:
        raise
    except Exception as e:
        logger.exception("Exception processing config file - aborting")
        sys.exit(4)

def main():
    try:
        parser = argparse.ArgumentParser(description="IOTAPi client software")
        parser.add_argument("config", help="config file relative path")
        parser.add_argument("-q","--quiet", help="disables all stdout logging (not file logging)", action="store_true")
        args = parser.parse_args()

        #signal.signal(signal.SIGINT, shutdown)
        #signal.signal(signal.SIGTERM, shutdown)

        Util.init_logger(args.quiet)
        logger.debug("Loggers initialized")
        logger.debug("IOTAPI-client version %d.%d starting up",Util.VERSION_MAJOR,Util.VERSION_MINOR)
        logger.debug("CWD: %s", os.getcwd())

        #pid = os.getpid()
        #new_niceness = os.setpriority(os.PRIO_PROCESS, pid, -10)
        #logger.debug("Niceness set to %d", new_niceness)

        logger.debug("Loading config")
        load_config(args.config)

        logger.info("Initializing motors")
        GPIOMgr.init_motors()

        logger.debug("Starting webserver")
        Webserver.init_flask()

        logger.info("Webserver app done, shutting down other things")
        shutdown(-1, None)

        logger.info("Goodbye...")

    except Exception as e:
        logger.exception(e)
        GPIOMgr.shutdown()


def shutdown(signum, frame):
    # TODO - probably fake local request to flask to get shutdown function with context
    logger.info('Received signal %s - shutting down', signum)
    GPIOMgr.shutdown()

if __name__ == '__main__':
    main()




