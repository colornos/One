import multiprocessing
import sys
import pygatt.backends
import logging
from configparser import ConfigParser
import time
import subprocess
from struct import *
from binascii import hexlify
import os
import threading
from time import sleep
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522

GPIO.setwarnings(False)

def run_script1():
    Char_glucose = '00002A18-0000-1000-8000-00805f9b34fb'  # glucose data
    RACP_CHARACTERISTIC = '00002a52-0000-1000-8000-00805f9b34fb'

    def sanitize_timestamp(timestamp):
        retTS = time.time()
        return retTS

    def decode_glucose(handle, values):
    data = unpack('<BHBBHBBBH', bytes(values))
        retDict = {}
        retDict["sequence_number"] = data[1]
        retDict["year"] = data[2]
        retDict["month"] = data[3]
        retDict["day"] = data[4]
        retDict["hours"] = data[5]
        retDict["minutes"] = data[6]
        retDict["seconds"] = data[7]
        retDict["glucose"] = data[8] / 10.0  # Assuming the value is in 1/10th increments, adjust if necessary
        retDict["type_sample_location"] = data[9]
        retDict["sensor_status"] = data[10]
        # Populate retDict with relevant data fields from 'data'
        return retDict

    def processIndication(handle, values):
        if handle == handle_glucose:
            result = decode_glucose(handle, values)
            if result not in glucose_data:
                log.info(str(result))
                glucose_data.append(result)
            else:
                log.info('Duplicate glucose_data record')
        else:
            log.debug('Unhandled Indication encountered')

    def wait_for_device(devname):
        found = False
        while not found:
            try:
                found = adapter.filtered_scan(devname)
            except pygatt.exceptions.BLEError:
                adapter.reset()
        return

    def connect_device(address):
        device_connected = False
        tries = 3
        device = None
        while not device_connected and tries > 0:
            try:
                device = adapter.connect(address, 8, addresstype)
                device_connected = True
            except pygatt.exceptions.NotConnectedError:
                tries -= 1
        return device

    def init_ble_mode():
        p = subprocess.Popen("sudo btmgmt le on", stdout=subprocess.PIPE,
                            shell=True)
        (output, err) = p.communicate()
        if not err:
            log.info(output)
            return True
        else:
            log.info(err)
            return False

    config = ConfigParser()
    config.read('One.ini')
    path = "plugins/"
    plugins = {}

    numeric_level = getattr(logging,
                            config.get('Program', 'loglevel').upper(),
                            None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % loglevel)
    logging.basicConfig(level=numeric_level,
                        format='%(asctime)s %(levelname)-8s %(funcName)s %(message)s',
                        datefmt='%a, %d %b %Y %H:%M:%S',
                        filename=config.get('Program', 'logfile'),
                        filemode='w')
    log = logging.getLogger(__name__)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(funcName)s %(message)s')
    ch.setFormatter(formatter)
    log.addHandler(ch)

    if config.has_option('Program', 'plugins'):
        config_plugins = config.get('Program', 'plugins').split(',')
        config_plugins = [plugin.strip(' ') for plugin in config_plugins]
        log.info('Configured plugins: %s' % ', '.join(config_plugins))

        sys.path.insert(0, path)
        for plugin in config_plugins:
            log.info('Loading plugin: %s' % plugin)
            mod = __import__(plugin)
            plugins[plugin] = mod.Plugin()
        log.info('All plugins loaded.')
    else:
        log.info('No plugins configured.')
    sys.path.pop(0)

    ble_address = config.get('GLUC', 'ble_address')
    device_name = config.get('GLUC', 'device_name')
    device_model = config.get('GLUC', 'device_model')

    if device_model == 'ONE':
        addresstype = pygatt.BLEAddressType.public
        time_offset = 0
    else:
        addresstype = pygatt.BLEAddressType.random
        time_offset = 0

    log.info('One Started')
    if not init_ble_mode():
        sys.exit()

    adapter = pygatt.backends.GATTToolBackend()
    adapter.start()

    while True:
        wait_for_device(device_name)
        device = connect_device(ble_address)
        if device:
            glucose_data = []
            handle_glucose = device.get_handle(Char_glucose)
            handle_racp = device.get_handle(RACP_CHARACTERISTIC)
            continue_comms = True

            try:
                device.subscribe(Char_glucose,
                                 callback=processIndication,
                                 indication=True)
            except pygatt.exceptions.NotConnectedError:
                continue_comms = False

            if continue_comms:
                # Request the last glucose measurement
                racp_command = bytearray([0x01, 0x06])
                device.char_write(handle_racp, racp_command)

                log.info('Waiting for notifications for another 30 seconds')
                time.sleep(30)
                try:
                    device.disconnect()
                except pygatt.exceptions.NotConnectedError:
                    log.info('Could not disconnect...')

                log.info('Done receiving data from glucose sensor')
                if glucose_data:
                    glucose_data_sorted = sorted(glucose_data, key=lambda k: k['timestamp'], reverse=True)

                    for plugin in plugins.values():
                        plugin.execute(config, glucose_data_sorted)
                else:
                    log.error('Data received')
