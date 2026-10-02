"""Load only this distribution's explicit settings, then run one component."""
import argparse
import os
from pathlib import Path
import sys
from configuration import public_base_url, notifications_enabled

ROOT = Path(__file__).resolve().parent.parent
GENERAL_KEYS = {'PUBLIC_BASE_URL', 'WEB_PORT', 'GEVENT_CACHE_DIR', 'NOTIFICATIONS_ENABLED',
                'REFRESH_INTERVAL_SECONDS', 'NOTIFY_INTERVAL_SECONDS', 'SCHEDULE_CACHE_TTL_SECONDS'}
DISCORD_KEYS = {'DISCORD_BOT_TOKEN', 'DISCORD_USER_ID'}


def load_settings(path, allowed):
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or key.strip() not in allowed:
            raise ValueError(f'Unexpected setting in {path.name}; use its example file')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # These files contain literal values, never shell expressions.
        os.environ[key.strip()] = value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('web', 'collector', 'notifier', 'discord-check', 'discord-test', 'dry-run'))
    args = parser.parse_args()
    load_settings(ROOT / '.env', GENERAL_KEYS)
    if args.mode in ('notifier', 'discord-check', 'discord-test', 'dry-run'):
        load_settings(ROOT / '.discord.env', DISCORD_KEYS)
    public_base_url()
    notifications_enabled()
    cache = os.getenv('GEVENT_CACHE_DIR', '').strip() or str(ROOT / 'data')
    if not Path(cache).is_absolute():
        raise ValueError('GEVENT_CACHE_DIR must be an absolute path or empty')
    os.environ['GEVENT_CACHE_DIR'] = cache
    os.chdir(ROOT / 'source')
    if args.mode == 'web':
        port = int(os.getenv('WEB_PORT', '18136'))
        if not 1024 <= port <= 65535:
            raise ValueError('WEB_PORT must be between 1024 and 65535')
        command = ['-m', 'gunicorn', '--worker-class', 'gevent', '--workers', '2',
                   '--bind', f'127.0.0.1:{port}', '--graceful-timeout', '30',
                   '--access-logfile', '-', '--error-logfile', '-', 'wsgi:application']
    elif args.mode in ('collector', 'notifier'):
        command = ['worker.py', args.mode]
    elif args.mode == 'dry-run':
        command = ['notifier.py', '--dry-run']
    else:
        command = ['discord_cli.py', '--check' if args.mode == 'discord-check' else '--send-test']
    os.execv(sys.executable, [sys.executable, *command])


if __name__ == '__main__':
    main()
