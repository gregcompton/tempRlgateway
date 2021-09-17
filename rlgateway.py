import json
import signal
from datetime import datetime
import time
import os
from uuid import getnode as get_mac

startup_time = time.time()

logfile = ('/opt/hyper/base/bootlogs/booted-%s.log' % str(int(startup_time)))
if os.path.exists('/opt/hyper/base/bootlogs'):
    pass
else:
    os.makedirs('/opt/hyper/base/bootlogs')
m = ('rebooted at: %s\n' % str(datetime.fromtimestamp(startup_time)))
print(m)
bootlog = open(logfile, "a")
bootlog.write(m)
bootlog.close()

# checks and instructions for new installations
libraries = True
try:
    import schedule  # using this instead of time, timer
except Exception as e:
    print(e)
    print("install 'schedule' using: pip install schedule OR pip3 install schedule\n")
    libraries = False

try:
    import requests
except Exception as e:
    print(e)
    print("install 'requests' using: pip install requests OR pip3 install requests\n")
    libraries = False

try:
    from digi.xbee.devices import XBeeDevice, RemoteXBeeDevice, XBee64BitAddress
except Exception as e:
    print(e)
    print("install digi-xbee using: pip install digi-xbee or pip3 install digi-xbee\n")
    print("DIGI-XBEE LIBRARY REQUIRES A MANUAL UPDATE, IN ORDER TO USE OTA FIRMWARE UPDATING")
    print("digi/xbee/models/mode.py - in method def calculate_api_output_mode_value(cls, protocol, options): line 230")
    print("COMMENT (or delete): return sum(op.code for op in options if lambda option: option != cls.EXPLICIT)")
    print("ADD: return sum(op.code for op in options if op < cls.UNSUPPORTED_ZDO_PASSTHRU)\n")
    libraries = False

#todo: figure out how to implement downloading firmware files to the local gateway
# then updating this dictionary with the correct values for the firmware files
ota_updates = {'ATMOS': {'path': 'D:\TestDir\ATMOS-v0.23-2021-01-20-file_system_image.fs.ota', 'version': 0.2},
               'THERMOCOUPLE': {'path': 'D:\TestDir\PROBE-v0.12-2021-01-19-file_system_image.fs.ota', 'version': 0.12}}

firmware_update_in_progress = False


# Configure gateway settings per configuration file
with open('/opt/hyper/base/rl_config.json', 'r+') as config_file:  # comment out for working in Greg's dev environment
# with open('rl_config.json', 'r+') as config_file:  # uncomment for working in Greg's dev environment
    config = json.load(config_file)
    if 'baseID' not in config:
        config['baseID'] = hex(get_mac())[2:]
        config_file.seek(0)
        json.dump(config, config_file, indent=2)
        config_file.truncate()
        print('baseID added to rlconfig.json: ', config['baseID'])
    else:
        print('baseID already in rlconfig.json: ', config['baseID'])
    config_file.close()

gateway_settings = {'syncTime': config['syncTime'],
                    'base_id': config['baseID'],
                    'RESTURL': config['RESTURL'],
                    'port': config['port'],
                    'baud_rate': config['baud_rate']
                    }

sensor_readings_list = {}
# firmware_update_list = {}  # todo: probably delete. it's not necessary anymore
settings_reply_list = {}
sensor_device_list = {}
sensor_readings_file = "/opt/hyper/base/sensor_readings.txt"


#variables for backlog data dump
bulk_request_list = {}
bulk_write_in_progress = False  # default is False
# last_write = time.time()
bulk_device = ""  # id of the device that is currently authorized to bulk_write

local_xbee = XBeeDevice(gateway_settings['port'], gateway_settings['baud_rate'])


def stupid_func():
    print("this is just to instantiate schedules")


job = schedule.every(gateway_settings['syncTime']).seconds.do(stupid_func)
abandon = schedule.every(60).seconds.do(stupid_func)
schedule.cancel_job(abandon)  # we won't need this until a bulk write begins


def transmit(xbee64bitaddr, payload):
    remote_device = RemoteXBeeDevice(local_xbee, xbee64bitaddr)
    local_xbee.send_data_async(remote_device, payload)  # should this be async?


def keyboard_interrupt_handler(sig):
    # signal may not work if running the program in an IDE (e.g. PyCharm)
    print("KeyboardInterrupt (ID: {}) has been caught. Cleaning up...".format(sig))
    exit(0)


def check_radio_configuration():
    # Critical radio settings are CE, NJ, and AP
    # This method assumes that AP == 2 (API MODE)
    ce = local_xbee.get_parameter('CE')
    nj = local_xbee.get_parameter('NJ')

    print("-----------------")

    if ce != b'\01':
        print("Radio Device Role not set properly. Changing to 'Form Network'")
        local_xbee.set_parameter("CE", b'\x01')
        print('Old device role: ', ce)
        ce = local_xbee.get_parameter('CE')
        print('New Device Role: ', ce)

    if nj != b'\xff':
        print("Radio Node Join Time not set properly. Changing to 'FF'")
        local_xbee.set_parameter("NJ", b'\xff')
        print('Old node join time: ', nj)
        nj = local_xbee.get_parameter('NJ')
        print('New Node Join Time: ', nj)

    print('Radio is configured properly.')
    print("-----------------")


def configure_coordinator_radio():
    # If a radio is installed with "Factory Defaults" this method sets the radio to "Risk Limiter Defaults".
    # Factory defaults sets AP == 1 (AT Transparent Mode) so Command Mode must be used.
    print(" +--------------------------------------------------+")
    print(" |              Configure Coordinator Radio         |")
    print(" +--------------------------------------------------+\n")
    print("Port: ", gateway_settings['port'])
    print("Baud: ", gateway_settings['baud_rate'])

    import serial
    ser = serial.Serial()
    ser.baudrate = gateway_settings['baud_rate']
    ser.port = gateway_settings['port']
    ser.open()

    if ser.is_open:
        print("Serial port is open")
        print(ser)

        print("Entering Command mode")
        ser.write(bytearray('+++', 'utf-8'))
        time.sleep(2)
        r = ser.read_all()
        print(r)

        # display current defaults
        print("-----------------")
        print("Current defaults")
        ser.write(bytearray('ATAP\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("AP: ", str(r))

        ser.write(bytearray('ATCE\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("CE: ", str(r))

        ser.write(bytearray('ATNI\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("NJ: ", str(r))

        ser.write(bytearray('ATNJ\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("NJ: ", str(r))

        # set new defaults
        print("-----------------")
        print("Set new defaults")

        print("Set radio to 'API MODE WITH ESCAPES' - Sending custom default ATAP02 command")
        new_id_command = "AT%F\rATAP02\r"
        ser.write(bytearray(new_id_command, 'utf-8'))
        time.sleep(1)

        print("Set radio to 'Form Network' - Sending custom default ATCE01 command")
        new_id_command = "AT%F\rATCE01\r"
        ser.write(bytearray(new_id_command, 'utf-8'))
        time.sleep(1)

        print("Set radio name to 'Coordinator' - Sending custom default ATNI command")
        new_id_command = "AT%F\rATNICoordinator\r"
        ser.write(bytearray(new_id_command, 'utf-8'))

        print("Set radio node join time to 'FF' - Sending custom default ATNJ command")
        new_id_command = "AT%F\rATNJFF\r"
        ser.write(bytearray(new_id_command, 'utf-8'))
        time.sleep(1)

        print("Applying changes")
        ser.write(bytearray('ATAC\r', 'utf-8'))
        time.sleep(1)

        print("Writing changes")
        ser.write(bytearray('ATWR\r', 'utf-8'))
        time.sleep(1)

        r = ser.read_all()  # clear serial

        # display new custom defaults
        print("-----------------")
        print("New Custom defaults")
        ser.write(bytearray('ATAP\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("AP: ", str(r))

        ser.write(bytearray('ATCE\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("CE: ", str(r))

        ser.write(bytearray('ATNI\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("NJ: ", str(r))

        ser.write(bytearray('ATNJ\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()
        print("NJ: ", str(r))

        print("Exiting Command Mode")
        ser.write(bytearray('ATCN\r', 'utf-8'))
        time.sleep(1)
        r = ser.read_all()

        print("Closing serial connection")
        ser.close()
        print("Is serial port open: ", ser.is_open)

        print("------Set Custom Defaults Complete-------------------")
        # print("pausing 5 seconds to allow radio to reboot")
        # time.sleep(5)

        return


def handle_rx_packet(xbee_message):
    print("----------------------------------")

    # check_firmware_update_list(xbee_message)  # todo: probably delete. it's not necessary anymore

    timestamp = xbee_message.timestamp
    payload = xbee_message.data.decode()

    if payload[0] == '{' and payload[len(payload) - 1] == '}':  # check for leading/trailing {} to test for json
        handle_payload_json(xbee_message, timestamp)
        return
    handle_payload_string(xbee_message, timestamp)


def handle_payload_string(xbee_message, timestamp):
    print("RECEIVED from %s (String)>> %s >> %s" % (
        xbee_message.remote_device.get_64bit_addr(), str(datetime.fromtimestamp(timestamp)),
        xbee_message.data.decode()))

    global bulk_request_list
    global bulk_write_in_progress
    global bulk_device
    # global last_write
    global job
    global abandon

    payload = xbee_message.data.decode()
    remote = xbee_message.remote_device.get_64bit_addr()

    if payload == "bulk_write_start_request":
        print("bulk_write_start_request received from: %s" % remote)

        if remote not in bulk_request_list:
            bulk_request_list.update({remote: time.time()})
            print('%s added to bulk request list. Total in list: %d' % (remote, len(bulk_request_list)))
        else:
            print('%s already in bulk request list. Total devices in list: %d' % (remote, len(bulk_request_list)))

        if not bulk_write_in_progress or str(remote) == bulk_device:
            print("bulk_write is not in progress")
            bulk_write_in_progress = True
            bulk_device = str(remote)
            print("transmit 'go' to %s" % remote)
            transmit(remote, "go")

            try:
                # last_write = time.time()  # this might be deleted due to addition of abandon scheduled job
                print("scheduling abandon_bulk_write for every 1 minute")
                # global abandon
                abandon = schedule.every(1).minutes.do(abandon_bulk_write)  # change to 1 minute for production

                # global job
                schedule.cancel_job(job)  # stop cloud sync tasks while backlog uploading
                print("Cloud Sync tasks cancelled")
            except Exception as e:
                print("Cloud sync tasks are probably already cancelled")
            return
        else:
            transmit(remote, "hold")  # should this be an async transmit?
            print('bulk_write is in progress for %s. Telling %s to hold.' % (bulk_device, remote))

            # # The below might be deleted, due to addition of abandon scheduled job
            # if time.time() - last_write > 90:
            #     print("bulk_device %s has stopped transmitting. something has happened release the hold" % bulk_device)
            #     bulk_write_in_progress = False
            #     bulk_device = ""
            #     print("Re-enabling cloud sync tasks")
            #     job = schedule.every(gateway_settings['syncTime']).seconds.do(do_cloud_server_tasks)
            #     schedule.run_all()  # re-enable cloud sync tasks
            return

    if payload == "Complete":
        print("bulk_write Complete. End of bulk write from %s" % remote)
        bulk_write_in_progress = False
        bulk_request_list.pop(remote)
        print('%s removed from bulk request list. Total devices in list: %d' % (remote, len(bulk_request_list)))
        print("Re-enabling cloud sync tasks and canceling abandon bulk write scheduled task")
        # global job
        job = schedule.every(gateway_settings['syncTime']).seconds.do(do_cloud_server_tasks)
        # global abandon
        schedule.cancel_job(abandon)
        do_cloud_server_tasks()
        # schedule.run_all()  # re-enable cloud sync tasks
        return


def abandon_bulk_write():
    global bulk_write_in_progress
    global bulk_device
    global job

    print('%s has not sent a reading for bulk writing for a while. Abandon and set bulk_write_in_progress to False.'
          % bulk_device)
    print("bulk_device %s has stopped transmitting. something has happened. Release the hold" % bulk_device)
    bulk_write_in_progress = False
    bulk_device = ""
    schedule.cancel_job(abandon)
    print("Re-enabling cloud sync tasks")
    job = schedule.every(gateway_settings['syncTime']).seconds.do(do_cloud_server_tasks)
    schedule.run_all()  # re-enable cloud sync tasks


    # bulk_write_in_progress = False
    # bulk_device = ""
    # print("Re-enabling cloud sync tasks")
    # abandon = schedule.every(gateway_settings['syncTime']).seconds.do(do_cloud_server_tasks)
    # schedule.run_all()  # re-enable cloud sync tasks


def handle_payload_json(xbee_message, timestamp):
    print("RECEIVED from %s (JSON)>> %s >> %s" % (
        xbee_message.remote_device.get_64bit_addr(), str(datetime.fromtimestamp(timestamp)),
        xbee_message.data.decode()))

    global bulk_request_listb
    global bulk_write_in_progress
    global bulk_device
    # global last_write

    payload = xbee_message.data.decode()
    remote = xbee_message.remote_device.get_64bit_addr()

    try:
        json_payload = json.loads(payload)
    except Exception as e:
        print("there was a problem loading payload as json.", e)
        print(payload)
        return

    if bulk_write_in_progress and str(remote) == bulk_device:
        # if we get here it's time to write the payload to a file
        # convert payload to json in order to add a timestamp, used in later calculations
        try:
            if 'tempC' in json_payload or 't' in json_payload:
                pass
            else:
                print('tempC is not in the payload. This is probably a settings reply. Ignore it during bulk write')
                return

            name = str(remote)

            if 'interval' in json_payload: interval = json_payload['interval']
            elif 'i' in json_payload:
                interval = json_payload['i']
            else:
                print("There was a problem computing the backlog timestamp. using the message timestamp")
                return

            if 'backlogindex' in json_payload: bindex = json_payload['backlogIndex']
            elif 'bi' in json_payload:
                bindex = json_payload['bi']
            else:
                print("There was a problem computing the backlog timestamp. using the message timestamp")
                return

            timestamp = bulk_request_list[remote] - interval * bindex

            pload = {}
            if 'interval' in json_payload:
                pload.update({'timestamp': timestamp, 'name': name})
            elif 'i' in json_payload:
                pload.update({'tempC': json_payload['t'],
                         'fw': json_payload['f'],
                         'interval': json_payload['i'],
                         'bP': json_payload['b']
                         })

                types = {1: "THERMOCOUPLE", 2: "ATMOS", 3: "DOOR"}
                pload.update({'type': types[json_payload['d']]})

                ptypes = {1: "NORMAL", 2: "BACKLOG"}
                pload.update({'ptype': ptypes[json_payload['p']]})

                pload.update({'timestamp': timestamp, 'name': name})
                print("updated timestamp: ", pload)

            # payload = json.dumps(json_payload)
        except Exception as e:
            print("There was a problem adding timestamp to bulk_request json entry for ", name, e)
            print(pload)
            return

        #todo: add try - except to validate the json, before adding it to the list or writing to the file
        try:
            test1 = json.dumps(pload)
            test_json = json.loads(test1)
            sensor_readings_list[timestamp] = pload  # add reading to list todo: make sure the change from payload to json_payload worked
            write_reading_to_file(pload)
            # last_write = time.time()  # this might be deleted due to abandoned scheduled job

            if 'backlogIndex' in pload or 'bi' in pload:
                # reset timer
                global abandon
                schedule.cancel_job(abandon)
                abandon = schedule.every(1).minutes.do(abandon_bulk_write)  # change to 1 minute for production

        except Exception as e:
            print("invalid json paylod in bulk_write_request", e)
        return

    if 'fwUpdateCheck' in json_payload and not firmware_update_in_progress:
        # print('inside fwUpdateCheck test')
        check_fw_version(xbee_message)
        return

    # if 'payloadType' in json_payload:  # for old fw
    if ('ptype' in json_payload) or ('payloadType' in json_payload) or ('p' in json_payload):  # for new fw
        # print('inside ptype test')
        handle_readings_payload(xbee_message, timestamp, json_payload)
        return

    if 'update' in json_payload:
        print('OTA updates are currently disabled')
        return

        # #todo: make sure this is tested thoroughly before deployment to production
        # print('Calling update remote filesystem method')
        # # print('METHOD IS CURRENTLY COMMENTED. MOVING ON')
        # device_type = json_payload["type"]
        # update_remote_filesystem(xbee_message.remote_device, device_type)
        # return

    # this check needs to be after payloadType check, otherwise it will "hijack" standard payloads
    if 'interval' in json_payload or 'pollingInterval' in json_payload:
        # print('inside interval test')
        json_payload.update({'timestamp': timestamp, 'name': str(xbee_message.remote_device.get_64bit_addr())})
        settings_reply_list[timestamp] = json_payload
        print('settings_reply_list updated: ', json_payload)
        # print_settings_reply_list()  #debug: remove for production to avoid race conditions


def handle_readings_payload(xbee_message, ts, payload):
    if not bulk_write_in_progress:
        try:
            db = int.from_bytes(local_xbee.get_parameter("DB"), byteorder='big', signed=True)
        except Exception as e:
            print(e)
            print('Could not get dB parameter. Setting to 99')
            db = 99
    else:
        db =99

    name = str(xbee_message.remote_device.get_64bit_addr())

    if name in sensor_device_list:
        # print(name, " is in sensor_device list") # debug remove for production
        if 'interval' in payload:
            device_interval = payload['interval']
        elif 'i' in payload:
            device_interval = payload['i']

        server_interval = sensor_device_list[name]['interval']
        # print("Intervals(device: %d, server: %d)" % (device_interval, server_interval))  #debug: remove for production
        if device_interval != server_interval:
            print("%s Interval %d does not match server %d. Sending update command"
                  % (name, device_interval, server_interval))
            remote_xbee = RemoteXBeeDevice(local_xbee, XBee64BitAddress.from_hex_string(name))
            local_xbee.send_data_async(remote_xbee, json.dumps({'interval': server_interval}))
    else:
        print(name, " is NOT in sensor_device list")

    pload = {}
    if 'p' in payload:
        pload = {'tempC': payload['t'],
                 'fw': payload['f'],
                 'interval': payload['i'],
                 'bP': payload['b']
                 }
        if payload['d'] == 1: pload.update({'type': 'THERMOCOUPLE'})
        elif payload['d'] ==2: pload.update({'type': 'ATMOS', 'humidity': payload['h']})
        elif payload['d'] ==3: pload.update({'type': 'DOOR'})

        if payload['p'] == 1: pload.update({'ptype': "NORMAL"})
        elif payload['p'] ==2: pload.update({'ptype': 'BACKLOG'})

    else:
        pload = payload

    pload.update({'timestamp': ts, 'dB': db, 'name': name})

    sensor_readings_list[ts] = pload  # add reading to list
    write_reading_to_file(pload)

    # print_sensor_readings_list()  #debug - comment out for production to avoid race condition with multiple sensors


def write_reading_to_file(payload):
    f = open(sensor_readings_file, "a")
    f.write(json.dumps(payload) + "\n")
    f.close()


def restore_readings_from_backup_file():
    print("****************************************************************")
    print("Checking backup sensor log for readings")
    print("Reading lines from file....")
    # sensor_readings_from_backup = {}  # debug

    try:
        f = open(sensor_readings_file, "r")
        lines = f.readlines()
    except Exception as e:
        lines = ""
        print(e)
        print("len(lines) = ", len(lines))

    if len(lines) == 0:
        print("There are no readings in the sensor backup log.")
    else:
        print("Number of entries in sensor backup file: ", len(lines))
        print("Adding %d backup sensor readings to the sensor_readings_list" % len(lines))
        readings_processed = 0
        for line in lines:
            try:
                json_line = json.loads(line)
                sensor_readings_list[json_line['timestamp']] = json_line
                readings_processed += 1
            except Exception as e:
                print("skipping line - not json format: %s" % line)
                print(e)
            if readings_processed == 250:
                print("***********************************************")
                print("******** Processing %d backup readings ********" % readings_processed)
                upload_data_to_cloud()
                readings_processed = 0

        print("***********************************************")
        print("******** Processing %d backup readings ********" % readings_processed)
        upload_data_to_cloud()
        print("Check of backup sensor log complete. Processed %d backup readings." % len(lines))

    print("****************************************************************")


def clear_readings_from_backup_file():
    f = open(sensor_readings_file, "w")
    f.write("")
    f.close()


def check_fw_version(xbee_message):
    payload = json.loads(xbee_message.data.decode())
    device_fw = payload['fwUpdateCheck']

    # # todo: re-implement this later
    # if 'type' in payload:
    #     device_type = payload['type']
    # elif 't' in payload:
    #     if payload['t'] == 1: device_type = "THERMOCOUPLE"
    #     device_type = payload['t']
    #
    # if device_fw < ota_updates[device_type]['version']:
    #     print('%s firmware v%.2f is out of date.' % (device_type, device_fw))
    #     remote_xbee = xbee_message.remote_device
    #     local_xbee.send_data_async(remote_xbee, json.dumps({"fwUpdateCheck": True}))
    # else:
    #     print("firmware is up to date: ", device_fw)
    #     remote_xbee = xbee_message.remote_device
    #     local_xbee.send_data_async(remote_xbee, json.dumps({"fwUpdateCheck": False}))

    return
    # if payload['fw'] < ota_updates[device_type]['version']:
    #     print('%s firmware v%.2f is out of date.' % (device_type, payload['fw']))
    #     add64 = xbee_message.remote_device.get_64bit_addr()
    #     if add64 in firmware_update_list:
    #         print(add64, "is already in the firmware_update_list")
    #     else:
    #         firmware_update_list.update({add64: {"device": xbee_message.remote_device, "type": device_type}})
    #         print(add64, "ADDED to the firmware_update_list")
    #
    #     print_firmware_update_list()
    #
    #     global firmware_update_in_progress
    #     if not firmware_update_in_progress:
    #         print("INITIATING FILESYSTEM UPDATE for ", add64, datetime.now())
    #         firmware_update_in_progress = True
    #         remote_xbee = xbee_message.remote_device
    #         local_xbee.send_data_async(remote_xbee, json.dumps({"update": 30}))


def print_sensor_readings_list():
    print('Current sensor_readings_list:')
    for entry in sensor_readings_list:
        print("\t", sensor_readings_list[entry])


# def print_firmware_update_list():  # todo: probably delete. it's not necessary anymore
#     print('Current firmware_update_list:')
#     for entry in firmware_update_list:
#         print("\t", entry)


def print_settings_reply_list():
    print('Current settings_reply_list:')
    for entry in settings_reply_list:
        print("\t", settings_reply_list[entry])


# def handle_firmware_update_list(add64, remove=True):  # todo: probably delete. it's not necessary anymore
#     global firmware_update_in_progress
#
#     if remove and add64 in firmware_update_list:
#         print("Removing %s from firmware_update_list" % add64)
#         del firmware_update_list[add64]
#
#     if len(firmware_update_list) > 0:
#         print_firmware_update_list()
#         print("Ready to update another device")
#
#         # todo: probably delete. it's not necessary anymore
#         # key = list(firmware_update_list.keys())[0]
#         # remote_xbee = firmware_update_list.get(key)['device']
#         # print("INITIATING FILESYSTEM UPDATE for ", key)
#         # print("----------------------------------")
#         # local_xbee.send_data_async(remote_xbee, json.dumps({'update': 30}))
#         # firmware_update_in_progress = True
#
#     else:
#         print("firmware_update_list is empty")
#         print("----------------------------------")
#     firmware_update_in_progress = False


# def check_firmware_update_list(xbee_message):  # todo: probably delete this. It's not necessary
#     add64 = xbee_message.remote_device.get_64bit_addr()
#
#     global firmware_update_in_progress
#     if add64 in firmware_update_list and not firmware_update_in_progress:
#         print("INITIATING FILESYSTEM UPDATE for ", add64)
#         print("----------------------------------")
#         local_xbee.send_data_async(xbee_message.remote_device, json.dumps({'update': 30}))
#         firmware_update_in_progress = True


def update_remote_filesystem(remote_xbee, device_type):
    global firmware_update_in_progress

    firmware_update_in_progress = True
    # add64 = remote_xbee.get_64bit_addr()
    path = ota_updates.get(device_type)['path']
    print(path)

    print("**Disabling data_receive_callback and cloud sync for the duration of the update")
    local_xbee.del_data_received_callback(handle_rx_packet)
    global job
    schedule.cancel_job(job)

    print(
        "Begin updating remote device filesystem for %s. "
        "Data readings from other devices may be lost." % remote_xbee.get_64bit_addr())

    remote_xbee.update_filesystem_image(path, progress_callback=update_filesystem_progress_callback)

    print("Filesystem updated successfully!")
    # handle_firmware_update_list(remote_xbee.get_64bit_addr())  # todo: probably delete. it's not necessary anymore

    remote_xbee.reset()

    print("%s >> Re-enabling data_received_callback and cloud sync" % str(datetime.now()))
    local_xbee.add_data_received_callback(handle_rx_packet)
    job = schedule.every(gateway_settings['syncTime']).seconds.do(do_cloud_server_tasks)
    schedule.run_all()
    firmware_update_in_progress = False


def update_filesystem_progress_callback(task, percent):
    print("%s: %d%%" % (task, percent))


# ************************ BEGIN Cloud Server Communications Methods ************************************

def do_cloud_server_tasks():
    print('\n*****************************************************************************')
    print("*********** BEGIN CLOUD SYNC TASKS %s ***********" % str(datetime.now()))
    update_basestation()
    upload_data_to_cloud()
    update_sensor_devices()
    print("*********** END CLOUD SYNC TASKS %s ***********" % str(datetime.now()))
    print('*****************************************************************************\n')


schedule.cancel_job(job)
job = schedule.every(gateway_settings['syncTime']).seconds.do(do_cloud_server_tasks)


def update_basestation():
    print("----------------------------------")
    print('GET Basestation settings from the cloud server')

    base_id = gateway_settings['base_id']
    RESTURL = gateway_settings['RESTURL']

    try:
        new_url = RESTURL + "/gateways/" + base_id
        print("trying to get data from: ", new_url)
        r = requests.get(new_url)
        if r.status_code == 200 and len(r.text) > 0 and r.json()['reporting_interval'] > 0:
            new_sync_time = int(r.json()['reporting_interval']) * 60
            current_sync_time = gateway_settings['syncTime']
            if new_sync_time != current_sync_time:
                gateway_settings.update({'syncTime': new_sync_time})
                global job
                schedule.cancel_job(job)
                job = schedule.every(new_sync_time).seconds.do(do_cloud_server_tasks)
                print("SUCCESS! Gateway syncTime changed from %d to %d seconds" % (current_sync_time, new_sync_time))
            else:
                print("SUCCESS! Gateway syncTime is up to date: ", new_sync_time)
        else:
            print("Server request failed. Status code: ", r.status_code)
            print("full server response: ", r)
    except Exception as ex:
        print("Could not get gateway data")
        print(ex)
        print(str(ex))


def update_sensor_devices():
    print("----------------------------------")
    print("GET sensor settings from the cloud server")

    base_id = gateway_settings['base_id']
    RESTURL = gateway_settings['RESTURL']

    try:
        sensor_url = RESTURL + "/gateways/" + base_id + "/sensors"
        r = requests.get(sensor_url)
        if r.status_code == 200 and len(r.text) > 0:
            xnet = local_xbee.get_network()
            sensors = r.json()
            for sensor in sensors:
                # print("Sensor ID: %s \t Reporting Interval: %s" % (sensor['id'], sensor['reporting_interval']))
                # print("Reporting Interval: %s" % sensor['reporting_interval'])
                new_reporting_interval = sensor['reporting_interval']
                name = sensor['id']

                # ensure any sensor with data sent from the cloud server is on the local xbee network
                # Initialize remote device from 64 bit addr(ID of sensor in cloud)
                remote_xbee = RemoteXBeeDevice(local_xbee, XBee64BitAddress.from_hex_string(name))
                # Manually add it to 'network' if not already present
                xnet.add_remote(remote_xbee)

                if name not in sensor_device_list:
                    # add it to the list
                    sensor_device_list[name] = {'name': name, 'interval': new_reporting_interval}
                    print("%s added to sensor_device_list. Interval: %d" % (name, new_reporting_interval))
                    local_xbee.send_data_async(remote_xbee,
                                               json.dumps({'interval': new_reporting_interval}))
                else:
                    if new_reporting_interval != sensor_device_list[name]['interval']:
                        sensor_device_list.update({name: {"interval": new_reporting_interval}})
                        print("%s reporting_interval updated to %d" % (name, new_reporting_interval))
                        local_xbee.send_data_async(remote_xbee,
                                                   json.dumps({'interval': new_reporting_interval}))
                    else:
                        print("%s is up to date" % name)

            # Start the discovery process to look for more sensors
            xnet.start_discovery_process()
            print("SUCCESS! sensor settings updated")
    except Exception as e:
        print('ERROR: Could not GET sensor settings')
        print('ERROR: ', e)


def upload_data_to_cloud():
    print("----------------------------------")
    print("POST to cloud server")

    RESTURL = gateway_settings['RESTURL']

    readings = sensor_readings_list.copy()
    replies = settings_reply_list.copy()
    payload = {"gateway": gateway_settings, "readings": readings, "settings_replies": replies}
    json_payload = json.dumps(payload)

    # serial_to_cellular(json_payload)  # todo: for testing cellular

    # print payload for debug
    for entry in payload:
        print(entry, len(payload[entry]))
        # # Uncomment for loop to print the payload to console.
        # if len(payload[entry]) > 0:
            # for key in payload[entry]:
            #     print("\t", key, payload[entry][key])

    try:
        print(RESTURL +"/data")
        r = requests.post(RESTURL + "/data", data=json_payload, headers={'Content-Type': 'application/json'})
    except Exception as e:
        print("Something went wrong with the POST: ", e)
        print("Abandoning remaining POST operations. Will try again later")
        return 99

    apilogfile = ("%sAPI_POST-%s.json" % (log_folder(), str(int(time.time()))))
    f = open(apilogfile, "a")
    f.write(json_payload)
    f.close()

    global startup_time
    log_filename = ("API_LOG_List-%s.json" % str(int(startup_time)))
    log_list = ("%s%s" % (log_folder(), log_filename))
    f = open(log_list, "a")
    f.write("%s\n" % apilogfile)
    f.close()

    if r.status_code == 200:
        print("SUCCESS! data uploaded. See %s for details" % apilogfile)
        for reading in readings:
            del sensor_readings_list[reading]
        for reply in replies:
            del settings_reply_list[reply]
        clear_readings_from_backup_file()
    else:
        print("ERROR: Unable to upload data. Status code: ", r.status_code)


# ************************ END Cloud Server Communications Methods ************************************

def log_folder():
    # folder_name = "/opt/hyper/base/apilogs"
    folder_path = "/opt/hyper/base/apilogs"
    if os.path.exists(folder_path):
        pass
    else:
        os.makedirs(folder_path)

    path = folder_path + '/'
    return path


def main():
    print("STARTING RISK LIMITER GATEWAY")

    # Ensure radio is configured properly and functional
    try:
        print("Opening local_xbee...")
        local_xbee.open()
        print("Success!  Checking configuration.")
        check_radio_configuration()

    except Exception as e:
        print("Can't open local_xbee")
        print("ERROR MESSAGE: ", e)

        if "open port" in str(e):
            print("Check that the radio is plugged in and try again.")
            exit(3)

        if "operating mode" in str(e):
            print("Radio is not in API Mode. Attempting to reset now ")
            try:
                configure_coordinator_radio()
                print("Opening local_xbee...")
                local_xbee.open()
                print("Success!")
            except Exception as e:
                print("Radio configuration failed. Exiting program.")
                exit(2)
        else:
            print("Unhandled exception. Exiting program now....need to write another error handler?")
            exit(1)

    print("Gateway syncTime has been scheduled for every %d seconds" % gateway_settings['syncTime'])

    restore_readings_from_backup_file()

    local_xbee.add_data_received_callback(handle_rx_packet)

    # signal may not work if running the program in an IDE (e.g. PyCharm)
    signal.signal(signal.SIGINT, keyboard_interrupt_handler)

    print("Waiting for data...", datetime.now())

    while True:
        schedule.run_pending()


if __name__ == '__main__':
    main()
