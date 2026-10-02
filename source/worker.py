"""Portable collector/notifier scheduling without host systemd or Docker socket."""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
import storage
from configuration import notifications_enabled, public_base_url

STOP = threading.Event()


def request_stamp():
    try:
        value = storage.read_json(storage.CACHE_DIR / 'refresh-request.json')['requested_at']
        return value if isinstance(value, str) else ''
    except (OSError, KeyError, TypeError, ValueError):
        return ''


def handled_stamp():
    try:
        return storage.read_json(storage.CACHE_DIR / 'refresh-request.handled.json')['requested_at']
    except (OSError, KeyError, TypeError, ValueError):
        return ''


def execute(script):
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name(script))], timeout=210)
        print(f'[worker] {script} exit={result.returncode}', flush=True)
        return result.returncode
    except subprocess.TimeoutExpired:
        print(f'[worker] {script} exceeded 210 seconds', flush=True)
        return 1


def interval(kind):
    key = 'REFRESH_INTERVAL_SECONDS' if kind == 'collector' else 'NOTIFY_INTERVAL_SECONDS'
    value = int(os.getenv(key, '21600' if kind == 'collector' else '600'))
    minimum = 900 if kind == 'collector' else 60
    if value < minimum:
        raise ValueError(f'{key} must be at least {minimum}')
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=('collector', 'notifier'))
    args = parser.parse_args()
    public_base_url()
    delay = interval(args.kind)
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    due = 0
    print(f'[worker] {args.kind} started; interval={delay}s', flush=True)
    while not STOP.is_set():
        stamp = request_stamp() if args.kind == 'collector' else ''
        requested = bool(stamp and stamp != handled_stamp())
        if time.monotonic() >= due or requested:
            if args.kind == 'collector':
                execute('refresh.py')
                if stamp:
                    storage.atomic_json(storage.CACHE_DIR / 'refresh-request.handled.json', {'requested_at': stamp})
            elif notifications_enabled():
                execute('notifier.py')
            else:
                print('[worker] automatic DMs disabled', flush=True)
            due = time.monotonic() + delay
        STOP.wait(5)
    print('[worker] stopped after current job completed', flush=True)


if __name__ == '__main__':
    main()
