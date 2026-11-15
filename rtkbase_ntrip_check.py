#!/usr/bin/env python3
"""One-shot, read-only RTK2go RTCM stream check; schedule ONCE daily.

Makes at most one TCP connection per invocation. Does not fetch sourcetable,
retry, send source credentials, or modify GNSS/RTKBase settings.
"""
import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

CONFIG = Path.home() / '.rtkbase_mqtt'
DEVICE = 'rtkbase_raspberry'
PREFIX = 'rtkbase'


def config_read():
    values = {}
    for raw in CONFIG.read_text(encoding='utf-8').splitlines():
        raw = raw.strip()
        if raw and not raw.startswith('#'):
            key, sep, value = raw.partition('=')
            if sep:
                values[key.strip()] = value.strip()
    return values


def probe(host, port, mount, timeout=12):
    """Check NTRIP v1 response and at least one complete RTCM3 frame.

    Returns (status, byte_count). RTCM CRC is not validated; framing alone
    establishes plausible RTCM data, not end-to-end correction quality.
    """
    mount = mount.lstrip('/')
    if not mount or any(c in mount for c in '\r\n '):
        return 'configuration_error', 0
    deadline = time.monotonic() + timeout
    received = 0
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            request = (f'GET /{mount} HTTP/1.0\r\n'
                       'User-Agent: NTRIP RTKBaseMonitor/1.0\r\n'
                       'Accept: */*\r\n'
                       'Connection: close\r\n\r\n')
            sock.sendall(request.encode('ascii'))
            buf = b''
            # NTRIP v1 returns "ICY 200 OK\r\n" with no HTTP headers.
            # NTRIP v2 can return normal HTTP headers.
            while b'\r\n' not in buf and time.monotonic() < deadline and len(buf) < 4096:
                sock.settimeout(max(0.2, deadline - time.monotonic()))
                part = sock.recv(4096)
                if not part:
                    return 'unreachable', 0
                buf += part
            first, sep, rest = buf.partition(b'\r\n')
            if not sep:
                return 'invalid_response', 0
            if first.startswith(b'ICY 200'):
                body = rest
            elif first.startswith(b'HTTP/'):
                if b' 200' not in first:
                    return 'rejected', 0
                while b'\r\n\r\n' not in rest and time.monotonic() < deadline and len(rest) < 8192:
                    sock.settimeout(max(0.2, deadline - time.monotonic()))
                    part = sock.recv(4096)
                    if not part:
                        return 'invalid_response', 0
                    rest += part
                _, sep, body = rest.partition(b'\r\n\r\n')
                if not sep:
                    return 'invalid_response', 0
            else:
                return 'rejected', 0

            while time.monotonic() < deadline:
                received = len(body)
                # RTCM3: D3, six reserved bits (zero), 10-bit payload length,
                # payload, and 3-byte CRC. Need a full frame, not just D3.
                for i in range(max(0, len(body) - 5)):
                    if body[i] != 0xD3 or i + 3 > len(body):
                        continue
                    if body[i + 1] & 0xFC:
                        continue
                    payload_length = ((body[i + 1] & 0x03) << 8) | body[i + 2]
                    if 0 < payload_length <= 1023 and i + 6 + payload_length <= len(body):
                        return 'ok', received
                sock.settimeout(max(0.2, deadline - time.monotonic()))
                part = sock.recv(4096)
                if not part:
                    break
                body = (body + part)[-8192:]
            return 'no_data', received
    except socket.timeout:
        return 'no_data' if received else 'unreachable', received
    except OSError:
        return 'unreachable', received


def publish(client, topic, value):
    payload = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
    info = client.publish(topic, payload, qos=1, retain=True)
    info.wait_for_publish()  # Compatible with older Raspberry Pi paho-mqtt.
    if not info.is_published():
        raise RuntimeError('MQTT publish failed: ' + topic)


def main():
    cfg = config_read()
    host = cfg.get('NTRIP_HOST', 'rtk2go.com')
    port = int(cfg.get('NTRIP_PORT', 2101))
    mount = cfg.get('NTRIP_MOUNT', 'FIN_PORI_LINNA')
    status, count = probe(host, port, mount)
    data = {'status': status, 'bytes_received': count,
            'checked_at': datetime.now().astimezone().isoformat(timespec='seconds')}
    client = mqtt.Client(client_id=f'rtkbase-daily-check-{os.getpid()}')
    client.username_pw_set(cfg['MQTT_USER'], cfg['MQTT_PASSWORD'])
    client.connect(cfg['MQTT_HOST'], int(cfg.get('MQTT_PORT', 1883)), 30)
    client.loop_start()
    try:
        device = {'identifiers': [DEVICE], 'name': 'RTKBase Raspberry Pi',
                  'manufacturer': 'Raspberry Pi / RTKBase', 'model': 'ZED-F9P'}
        configs = {
            'rtk2go_daily_status': {
                'name': 'RTK2go: päivittäinen tarkistus',
                'value_template': '{{ value_json.status }}'},
            'rtk2go_daily_checked_at': {
                'name': 'RTK2go: tarkistettu',
                'value_template': '{{ value_json.checked_at }}',
                'device_class': 'timestamp'},
            'rtk2go_daily_bytes': {
                'name': 'RTK2go: vastaanotetut tavut',
                'value_template': '{{ value_json.bytes_received }}',
                'state_class': 'measurement'},
        }
        for key, attrs in configs.items():
            conf = {'unique_id': f'{DEVICE}_{key}', 'state_topic': f'{PREFIX}/daily_ntrip',
                    'device': device, **attrs}
            publish(client, f'homeassistant/sensor/{DEVICE}/{key}/config', conf)
        publish(client, f'{PREFIX}/daily_ntrip', data)
    finally:
        client.disconnect()
        client.loop_stop()
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
