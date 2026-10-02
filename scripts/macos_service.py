"""Install and manage only this package's three per-user macOS LaunchAgents."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
PREFIX = 'com.game-alert.distribution'
KINDS = ('web', 'collector', 'notifier')


def definition(kind, home=None):
    home = home or Path.home()
    logs = home / 'Library' / 'Logs' / 'game-alert'
    return {'Label': PREFIX + '.' + kind,
            'ProgramArguments': [str(ROOT / '.venv' / 'bin' / 'python'), str(ROOT / 'source' / 'runtime.py'), kind],
            'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True,
            'ThrottleInterval': 30, 'ExitTimeOut': 240,
            'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'TZ': 'Asia/Seoul'},
            'StandardOutPath': str(logs / (kind + '.log')),
            'StandardErrorPath': str(logs / (kind + '.error.log'))}


def verify_owned(path, kind):
    if path.exists():
        with path.open('rb') as f:
            old = plistlib.load(f)
        if old.get('ProgramArguments') != definition(kind)['ProgramArguments']:
            raise RuntimeError('Existing LaunchAgent belongs to another installation; leave it intact')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('install', 'status', 'stop', 'uninstall'))
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise SystemExit('macOS only; no LaunchAgents changed')
    domain = f'gui/{os.getuid()}'
    agents = Path.home() / 'Library' / 'LaunchAgents'
    # Check all owners before changing any of them.
    for kind in KINDS:
        verify_owned(agents / (PREFIX + '.' + kind + '.plist'), kind)
    if args.action == 'install':
        sys.path.insert(0, str(ROOT / 'source'))
        from runtime import load_settings, GENERAL_KEYS
        from configuration import public_base_url, notifications_enabled
        from worker import interval
        load_settings(ROOT / '.env', GENERAL_KEYS)
        public_base_url()
        notifications_enabled()
        interval('collector')
        interval('notifier')
        agents.mkdir(parents=True, exist_ok=True)
        (Path.home() / 'Library' / 'Logs' / 'game-alert').mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        label = PREFIX + '.' + kind
        path = agents / (label + '.plist')
        if args.action == 'status':
            result = subprocess.run(['launchctl', 'print', domain + '/' + label], capture_output=True, text=True)
            print(label + (': loaded' if result.returncode == 0 else ': not loaded'))
            for line in result.stdout.splitlines():
                if line.strip().startswith(('state =', 'pid =', 'last exit code =')):
                    print('  ' + line.strip())
            continue
        if path.exists():
            loaded = subprocess.run(['launchctl', 'print', domain + '/' + label], capture_output=True).returncode == 0
            if loaded:
                subprocess.run(['launchctl', 'bootout', domain + '/' + label], check=True)
        if args.action == 'install':
            temp = path.with_suffix('.plist.tmp')
            temp.write_bytes(plistlib.dumps(definition(kind)))
            temp.replace(path)
            subprocess.run(['launchctl', 'bootstrap', domain, str(path)], check=True)
            print(label + ': installed and started')
        elif args.action == 'uninstall' and path.exists():
            path.unlink()
            print(label + ': removed; settings and schedule data preserved')


if __name__ == '__main__':
    main()
