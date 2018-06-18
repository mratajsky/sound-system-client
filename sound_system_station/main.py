import argparse
import configparser
import logging
import os

from .station import Station

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', type=str,
                        help='path to the config.ini file')
    args = parser.parse_args()
    inifile = args.config
    if inifile is None:
        # Try the relative path first to make it easier to use this
        # directly from the source package directory
        module_dir = os.path.dirname(os.path.realpath(__file__))
        inifile = os.path.join(os.path.dirname(module_dir), 'etc', 'config.ini')
        if not os.path.isfile(inifile):
            # Default path
            inifile = '/etc/config.ini'
    elif os.path.isdir(inifile):
        # Also accept directory containing the file
        inifile = os.path.join(inifile, 'config.ini')

    config = configparser.ConfigParser()
    with open(inifile) as handle:
        config.read_file(handle)

    cfglevel = config.get('basic', 'loglevel', fallback='info').upper()
    loglevel = getattr(logging, cfglevel.upper(), logging.INFO)
    logging.basicConfig(level=loglevel, format='%(asctime)s: %(message)s')
    logging.debug('Configuration read from %s', inifile)

    station = Station(config)
    station.start()

if __name__ == '__main__':
    run()
