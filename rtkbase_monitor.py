#!/usr/bin/env python3
"""Local RTKBase telemetry only; never contacts RTK2go."""
import json
import os
import subprocess
import time
from pathlib import Path

import paho.mqtt.client as mqtt

CONFIG = Path.home() / '.rtkbase_mqtt'
PREFIX = 'rtkbase'
DEVICE = 'rtkbase_raspberry'
GNSS_IDS = {0: 'gps', 2: 'galileo', 3: 'beidou', 6: 'glonass'}


def config_read():
    values = {}
    for raw in CONFIG.read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if raw and not raw.startswith('#'):
            key, sep, value = raw.partition('=')
            if sep:
                values[key.strip()] = value.strip()
    return values


def temperature():
    try:
        return float(subprocess.check_output(['vcgencmd', 'measure_temp'], text=True, timeout=4).split('=')[1].replace("'C", ''))
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


def memory():
    info = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, _, value = line.partition(':')
        info[key] = int(value.strip().split()[0])
    return round(100 * (info['MemTotal'] - info['MemAvailable']) / info['MemTotal'], 1)


def disk():
    s = os.statvfs('/')
    return round(100 * (s.f_blocks - s.f_bavail) / s.f_blocks, 1)


def gnss():
    result = {'satellites_visible': None, 'satellites_used': None, 'hdop': None, 'pdop': None,
              'gnss_online': False}
    for name in GNSS_IDS.values():
        result[f'{name}_visible'] = None
        result[f'{name}_used'] = None
    try:
        # -n 20 captures SKY without interfering with RTKBase receiver settings.
        proc = subprocess.run(['gpspipe', '-w', '-n', '20'], text=True, capture_output=True, timeout=15)
        for line in proc.stdout.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get('class') != 'SKY':
                continue
            satellites = obj.get('satellites', [])
            result.update(satellites_visible=obj.get('nSat'), satellites_used=obj.get('uSat'),
                          hdop=obj.get('hdop'), pdop=obj.get('pdop'), gnss_online=True)
            for gnssid, name in GNSS_IDS.items():
                group = [sat for sat in satellites if sat.get('gnssid') == gnssid]
                result[f'{name}_visible'] = len(group)
                result[f'{name}_used'] = sum(bool(sat.get('used')) for sat in group)
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def ntrip_running():
    # Never print str2str process arguments: they may contain caster passwords.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / 'cmdline').read_bytes().replace(b'\x00', b' ')
            if b'str2str' in cmd and b'ntrips://' in cmd:
                return True
        except (OSError, PermissionError):
            continue
    return False



NUMERIC = {
    'cpu_temperature': ('CPU-lämpötila', '°C', 'temperature'),
    'cpu_load': ('CPU-kuorma (1 min)', None, None),
    'memory_usage': ('Muistin käyttö', '%', None),
    'disk_usage': ('Levytilan käyttö', '%', None),
    'satellites_visible': ('Havaitut satelliitit', None, None),
    'satellites_used': ('Käytetyt satelliitit', None, None),
    'hdop': ('HDOP', None, None), 'pdop': ('PDOP', None, None),
}
for constellation, label in [('gps', 'GPS'), ('galileo', 'Galileo'), ('beidou', 'BeiDou'), ('glonass', 'GLONASS')]:
    NUMERIC[f'{constellation}_visible'] = (f'{label}: havaitut', None, None)
    NUMERIC[f'{constellation}_used'] = (f'{label}: käytetyt', None, None)


def publish(client, topic, obj, retain=True):
    info = client.publish(topic, json.dumps(obj, ensure_ascii=False) if isinstance(obj, dict) else obj,
                          qos=1, retain=retain)
    info.wait_for_publish()
    if not info.is_published():
        raise RuntimeError(f'MQTT-julkaisu ei onnistunut: {topic}')


def discovery(client):
    device = {'identifiers': [DEVICE], 'name': 'RTKBase Raspberry Pi',
              'manufacturer': 'Raspberry Pi / RTKBase', 'model': 'ZED-F9P'}
    for key, (name, unit, kind) in NUMERIC.items():
        obj = {'name': name, 'unique_id': f'{DEVICE}_{key}', 'state_topic': f'{PREFIX}/state',
               'value_template': '{{ value_json.' + key + ' }}', 'device': device,
               'state_class': 'measurement', 'expire_after': 900}
        if unit:
            obj['unit_of_measurement'] = unit
        if kind:
            obj['device_class'] = kind
        publish(client, f'homeassistant/sensor/{DEVICE}/{key}/config', obj)
    for key, name in {'gnss_online': 'GNSS-data', 'ntrip_running': 'NTRIP-lähetysprosessi'}.items():
        obj = {'name': name, 'unique_id': f'{DEVICE}_{key}', 'state_topic': f'{PREFIX}/state',
               'value_template': "{{ 'ON' if value_json." + key + " else 'OFF' }}",
               'payload_on': 'ON', 'payload_off': 'OFF', 'device': device,
               'expire_after': 900}
        publish(client, f'homeassistant/binary_sensor/{DEVICE}/{key}/config', obj)
    obj = {'name': 'Viimeisin mittaus', 'unique_id': f'{DEVICE}_last_update',
           'state_topic': f'{PREFIX}/state', 'value_template': '{{ value_json.last_update }}',
           'device_class': 'timestamp', 'device': device}
    publish(client, f'homeassistant/sensor/{DEVICE}/last_update/config', obj)


def main():
    cfg = config_read()
    client = mqtt.Client(client_id=f'rtkbase-monitor-{os.getpid()}')
    client.username_pw_set(cfg['MQTT_USER'], cfg['MQTT_PASSWORD'])
    client.connect(cfg['MQTT_HOST'], int(cfg.get('MQTT_PORT', 1883)), 30)
    client.loop_start()
    try:
        # Remove obsolete v2 discovery entity; daily script owns the new entity.
        publish(client, f'homeassistant/sensor/{DEVICE}/rtk2go_probe/config', '')
        discovery(client)
        data = {'cpu_temperature': temperature(), 'cpu_load': round(os.getloadavg()[0], 2),
                'memory_usage': memory(), 'disk_usage': disk(),
                'ntrip_running': ntrip_running(), **gnss()}
        data['last_update'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        publish(client, f'{PREFIX}/state', data)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    finally:
        client.disconnect()
        client.loop_stop()


if __name__ == '__main__':
    main()
