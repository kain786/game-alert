"""Read-only schedule access and crash-safe writes shared by web and jobs."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(os.environ.get('GEVENT_CACHE_DIR', Path(__file__).resolve().parent / 'cache'))
TTL = int(os.environ.get('SCHEDULE_CACHE_TTL_SECONDS', '21600'))
KEYS = ('genshin', 'genshin_broadcast', 'zzz', 'zzz_broadcast', 'hsr', 'hsr_broadcast')


def utcnow():
    return datetime.now(timezone.utc)


def read_json(path):
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def lock(name, *, blocking=False):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with (CACHE_DIR / name).open('a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def valid_source(value):
    return (isinstance(value, dict) and isinstance(value.get('data'), list)
            and isinstance(value.get('meta', {}), dict)
            and all(isinstance(row, dict) for row in value['data']))


def read_sources():
    """No fetching, writing, or repair on HTTP reads, even with damaged state."""
    sources = None
    recovery = False
    for name in ('schedule_snapshot.json', 'schedule_snapshot.previous.json'):
        try:
            raw = read_json(CACHE_DIR / name)
            candidate = raw['sources']
            if set(candidate) != set(KEYS) or not all(valid_source(candidate[k]) for k in KEYS):
                raise ValueError('Invalid snapshot')
            sources = candidate
            recovery = name.endswith('previous.json')
            break
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if sources is None:
        recovery = (CACHE_DIR / 'schedule_snapshot.json').exists()
        sources = {}
        for key in KEYS:
            path = CACHE_DIR / f'{key}.json'
            try:
                value = read_json(path)
                if not valid_source(value):
                    raise ValueError('Invalid source')
                value.setdefault('meta', {}).setdefault('fetched_at', datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
                sources[key] = value
            except (OSError, ValueError, TypeError):
                sources[key] = {'data': [], 'meta': {'error': '저장된 일정을 읽을 수 없습니다', 'stale': True}}
    for value in sources.values():
        meta = value.setdefault('meta', {})
        try:
            stamp = datetime.fromisoformat(meta['fetched_at'])
            if stamp.tzinfo is None:
                raise ValueError('Timezone required')
            age = (utcnow() - stamp).total_seconds()
        except (KeyError, ValueError, TypeError):
            age = TTL + 1
        meta['stale'] = bool(meta.get('error') or age > TTL or recovery)
        if recovery:
            meta['error'] = '저장 자료 복구본을 표시하고 있습니다'
    return sources


def save_sources(sources):
    target = CACHE_DIR / 'schedule_snapshot.json'
    try:
        previous = read_json(target)
        if set(previous['sources']) == set(KEYS) and all(valid_source(previous['sources'][k]) for k in KEYS):
            atomic_json(CACHE_DIR / 'schedule_snapshot.previous.json', previous)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # Preserve the legacy files as a forward-compatible rollback path.
    for key, value in sources.items():
        if value['data']:
            atomic_json(CACHE_DIR / f'{key}.json', value)
    atomic_json(target, {'schema': 2, 'sources': sources})


def job_status():
    try:
        status = read_json(CACHE_DIR / 'refresh_status.json')
        if not isinstance(status, dict):
            return {}
        if status.get('state') == 'running':
            started = datetime.fromisoformat(status['started_at'])
            if (utcnow() - started).total_seconds() > 210:
                status = {**status, 'state': 'failed', 'error': '갱신 제한 시간을 초과했습니다'}
        return status
    except (OSError, ValueError, KeyError, TypeError):
        return {}
