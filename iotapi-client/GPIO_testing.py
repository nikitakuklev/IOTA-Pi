import argparse
import logging.config
import json
import sys

import Util, GPIOMgr
import Webserver
from Stepper import Stepper



logger = logging.getLogger(__name__)

def load_config(config_path):
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            if config['compatible_with'] != Util.VERSION_MAJOR:
                logger.fatal("Incompatible config specified - aborting")
                sys.exit(3)
            if config.get('motors'):
                for k,motor in config['motors'].items():
                    mt = Stepper(k,motor['friendly_name'],motor['pin_direction'],motor['pin_step'],
                                 motor['pin_enable'],motor['pin_sleep'],motor['pin_lim_up'],motor['pin_lim_dn'],
                                 motor['lim_up_state'],motor['lim_dn_state'],motor['step_pulse_time'])
                    GPIOMgr.addMotor(mt)
            else:
                logger.warning('No motors found in config file!')
    except SystemExit:
        raise
    except Exception as e:
        logger.exception("Exception processing config file - aborting")
        sys.exit(4)

def main():
    parser = argparse.ArgumentParser(description="IOTAPi client software")
    parser.add_argument("config", help="config file relative path")
    parser.add_argument("-q","--quiet", help="disables all stdout logging (not file logging)", action="store_true")
    args = parser.parse_args()

    Util.init_logger(args.quiet)
    logger.debug("Loggers initialized")
    logger.debug("IOTAPI-client version %d.%d starting up",Util.VERSION_MAJOR,Util.VERSION_MINOR)

    logger.debug("Loading config")
    load_config(args.config)

    logger.info("Initializing motors")
    GPIOMgr.init_motors()

    Webserver.init_flask()
    logger.debug("Webserver initialized")
    logger.info("Startup complete")

if __name__ == '__main__':
    main()




