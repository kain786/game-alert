"""Single-writer collector invoked only by the standalone collector worker."""
import json
import re
import signal
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
import requests
import storage
from schedule_sources import (fetch_genshin_updates, fetch_genshin_broadcasts,
    fetch_hsr_updates, fetch_hsr_broadcasts, fetch_zzz_updates, fetch_zzz_broadcasts,
    fetch_official_name_ko)

FETCHERS = {'genshin': fetch_genshin_updates, 'genshin_broadcast': fetch_genshin_broadcasts,
            'zzz': fetch_zzz_updates, 'zzz_broadcast': fetch_zzz_broadcasts,
            'hsr': fetch_hsr_updates, 'hsr_broadcast': fetch_hsr_broadcasts}
DOMAINS = {'genshin': 'genshin-impact.fandom.com', 'zzz': 'zenless-zone-zero.fandom.com', 'hsr': 'honkai-star-rail.fandom.com'}
MAX_ROWS = 16


class Deadline(BaseException):
    pass


def _deadline(_signum, _frame):
    raise Deadline()


def normalize_name(value):
    text = re.sub(r'\([^)]*\)', ' ', (value or '').replace('\xad', ''))
    text = re.sub(r"[^\uac00-\ud7a3\s·•'’\-–—]", ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


class NameResolver:
    def __init__(self, session):
        self.session = session
        self.deadline = time.monotonic() + 40
        try:
            raw = storage.read_json(storage.CACHE_DIR / 'ko_names.json')
            self.data = raw.get('data', {})
            self.meta = raw.get('entries', {})
            if not isinstance(self.data, dict) or not isinstance(self.meta, dict):
                raise ValueError('Invalid names')
        except (OSError, ValueError, TypeError):
            self.data, self.meta = {}, {}
        self.dirty = False

    def resolve(self, domain, name):
        key = f'{domain}:{name}'
        suffix = ' LV.999' if re.search(r'\bLV\.?\s*999\b', name, re.I) else ''
        if self.data.get(key):
            return (normalize_name(self.data[key]) or name.replace('_', ' ')) + suffix
        retry = self.meta.get(key, {}).get('retry_after')
        if retry and retry > storage.utcnow().isoformat():
            return name.replace('_', ' ')
        if re.search(r'[\uac00-\ud7a3]', name):
            return name
        if time.monotonic() > self.deadline:
            return name.replace('_', ' ')
        state = 'missing'
        try:
            value = normalize_name(fetch_official_name_ko(domain, name, session=self.session) or '')
            if value:
                state = 'ok'
        except Exception:
            value, state = '', 'failed'
        self.data[key] = value
        self.meta[key] = {'state': state, 'retry_after': (storage.utcnow() + timedelta(hours=1 if state == 'failed' else 24)).isoformat()}
        self.dirty = True
        return (value + suffix) if value else name.replace('_', ' ')

    def save(self):
        if self.dirty:
            storage.atomic_json(storage.CACHE_DIR / 'ko_names.json', {'meta': {'updated_at': storage.utcnow().isoformat()}, 'data': self.data, 'entries': self.meta})


def validate(rows, previous):
    if not rows or not all(isinstance(r, dict) and r.get('version') for r in rows):
        raise ValueError('수집 결과에 유효한 일정이 없습니다')
    valid_dates = 0
    for row in rows:
        for field in ('update_date', 'end_date'):
            if row.get(field):
                date.fromisoformat(row[field])
        if row.get('update_date'):
            valid_dates += 1
        if row.get('end_date') and row.get('update_date') and row['end_date'] < row['update_date']:
            raise ValueError('종료일이 시작일보다 빠릅니다')
    if not valid_dates:
        raise ValueError('일정 날짜를 해석하지 못했습니다')
    if previous and len(rows) < min(4, max(1, len(previous) // 2)):
        raise ValueError('수집 건수가 비정상적으로 감소했습니다')
    known = [r.get('update_date') for r in previous if r.get('update_date')]
    newest = max(r['update_date'] for r in rows if r.get('update_date'))
    if known and newest < max(known):
        raise ValueError('최신 일정이 이전 자료보다 과거로 이동했습니다')


def collect(key, session, resolver):
    kwargs = {'session': session}
    if key == 'hsr':
        kwargs['max_occurrences'] = 32
    entries = FETCHERS[key](**kwargs)
    # Preserve undated announcements rather than dropping them behind history.
    entries = sorted(entries, key=lambda e: (getattr(e, 'update_date', getattr(e, 'broadcast_date', None)) is None,
                    getattr(e, 'update_date', getattr(e, 'broadcast_date', None)) or date.max), reverse=True)[:MAX_ROWS]
    rows = []
    for item in entries:
        if key.endswith('_broadcast'):
            rows.append({'kind': 'broadcast', 'version': item.version.replace('Version', '버전').replace('"', ''),
                         'update_date': item.broadcast_date.isoformat() if item.broadcast_date else None,
                         'time': item.broadcast_time, 'tz': item.broadcast_tz, 'detail': '공식 방송'})
        else:
            version = item.version.replace('Version', '버전').replace('"', '')
            if key == 'zzz' and not version.startswith('버전'):
                version = '버전 ' + version
            names = list(dict.fromkeys(resolver.resolve(DOMAINS[key], name) for name in item.pickup_characters[:4]))
            rows.append({'version': version, 'update_date': item.update_date.isoformat() if item.update_date else None,
                         'end_date': item.end_date.isoformat() if item.end_date else None,
                         'source_id': item.source_id, 'pickup_characters': names, 'pickup_ids': sorted(set(item.pickup_characters[:4]))})
    return rows


def run():
    with storage.lock('.refresh.lock') as acquired:
        if not acquired:
            return 0
        started = storage.utcnow().isoformat()
        storage.atomic_json(storage.CACHE_DIR / 'refresh_status.json', {'state': 'running', 'started_at': started})
        sources = storage.read_sources()
        previous_handler = signal.signal(signal.SIGALRM, _deadline)
        signal.alarm(165)
        failed, completed = [], []
        session = requests.Session()
        resolver = NameResolver(session)
        try:
            for key in storage.KEYS:
                old = sources[key]
                retry = old['meta'].get('retry_after')
                if retry and retry > storage.utcnow().isoformat():
                    failed.append(key)
                    continue
                try:
                    rows = collect(key, session, resolver)
                    validate(rows, old['data'])
                    sources[key] = {'data': rows, 'meta': {'fetched_at': storage.utcnow().isoformat(), 'error': '', 'stale': False}}
                    completed.append(key)
                except Exception:
                    # Do not persist raw HTTP exception strings or credential-bearing URLs.
                    failed.append(key)
                    old['meta'].update(error='수집 또는 일정 검증에 실패했습니다', stale=True,
                                       last_attempt_at=storage.utcnow().isoformat(), retry_after=(storage.utcnow() + timedelta(minutes=15)).isoformat())
        except Deadline:
            for key in storage.KEYS:
                if key not in completed and key not in failed:
                    failed.append(key)
                    sources[key]['meta'].update(error='수집 제한 시간을 초과했습니다', stale=True)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_handler)
            session.close()
        try:
            resolver.save()
            storage.save_sources(sources)
            storage.atomic_json(storage.CACHE_DIR / 'refresh_status.json', {'state': 'partial' if failed else 'success', 'started_at': started,
                'finished_at': storage.utcnow().isoformat(), 'failed_sources': failed, 'completed_sources': completed})
        except Exception:
            storage.atomic_json(storage.CACHE_DIR / 'refresh_status.json', {'state': 'failed', 'started_at': started, 'error': '갱신 자료 저장에 실패했습니다'})
            return 1
        print(f'[refresh] completed={len(completed)} failed={len(failed)}')
        return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(run())
