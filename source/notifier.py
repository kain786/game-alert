#!/usr/bin/env python3
"""Durable Discord DM outbox with legacy-state compatibility and no-write dry runs."""
import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from configuration import public_base_url, notifications_enabled
from discord_delivery import DiscordDM, DeliveryError, message_content
import storage
import schedule

CACHE_DIR = storage.CACHE_DIR
STATE_PATH = CACHE_DIR / 'notify_state.json'
SEOUL = schedule.SEOUL


@dataclass(frozen=True)
class Event:
    key: str
    identity: str
    fingerprint: str
    kind: str
    game_id: str
    game_label: str
    update_date: str
    weekday: str
    version: str
    time: str
    detail: str
    pickups: tuple
    end_date: str = ''
    timezone: str = ''
    signature: str = ''
    enriched: bool = False

    def line(self):
        base = f"{self.update_date or '미정'} {self.game_label} {self.version}"
        if self.kind == 'broadcast':
            return f"[공식 방송] {base}" + (f' · {self.time} {self.timezone}' if self.time else ' · 시각 미정')
        if self.kind == 'ending':
            return f'[픽업 마감 임박] {base} · 종료일 {self.end_date} (정확한 종료 시각은 원문 확인)'
        return f"[픽업 시작] {base} · {', '.join(self.pickups) or '캐릭터 미정'}" + (f' · 종료일 {self.end_date}' if self.end_date else '')


def legacy_key(game, kind, row):
    return '|'.join([kind, game, str(row.get('update_date') or ''), (row.get('time') or '').strip(),
                     (row.get('version') or '').strip(), (row.get('detail') or '').strip(), ','.join((row.get('pickup_characters') or [])[:10])])


def _load_state():
    if not STATE_PATH.exists():
        return {'sent_keys': [], 'pending': []}
    state = storage.read_json(STATE_PATH)
    if (not isinstance(state, dict) or not isinstance(state.get('sent_keys'), list)
            or not all(isinstance(k, str) for k in state['sent_keys'])
            or not isinstance(state.get('pending'), list)
            or not all(isinstance(p, dict) and isinstance(p.get('key'), str) and isinstance(p.get('text'), str) for p in state['pending'])
            or not isinstance(state.get('records', {}), dict)
            or not all(isinstance(v, dict) and isinstance(v.get('fingerprint'), str) and isinstance(v.get('sent_at'), str) for v in state.get('records', {}).values())):
        raise ValueError('Invalid notification state; refusing to reset history')
    return state


def _save_state(state):
    storage.atomic_json(STATE_PATH, state)


def _in_send_window_kst(now):
    clock = now.astimezone(SEOUL).timetz().replace(tzinfo=None)
    return time(10) <= clock <= time(20)


def _build_events():
    sources = storage.read_sources()
    events = []
    for key, source in sources.items():
        # Missing/corrupt caches must not bootstrap an empty notification history.
        if not source['data']:
            raise ValueError('A required schedule cache is unavailable')
        game = key.split('_')[0]
        kind = 'broadcast' if key.endswith('_broadcast') else 'pickup'
        for row in source['data']:
            day, clock, tz, _ = schedule.localized(row) if kind == 'broadcast' else (row.get('update_date'), '', '', None)
            d = schedule.iso_date(day)
            # Translation/ordering corrections should not create new schedule alerts.
            content = [row.get('update_date'), row.get('time'), row.get('tz'), row.get('version')]
            signature = hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()
            content.append(sorted(row.get('pickup_ids') or row.get('pickup_characters') or []))
            fingerprint = hashlib.sha256(json.dumps(content, ensure_ascii=False).encode()).hexdigest()
            events.append(Event(legacy_key(game, 'update' if kind == 'pickup' else kind, row),
                schedule.event_id(game, kind, row), fingerprint, kind, game, schedule.GAMES[game], day or '',
                ['월','화','수','목','금','토','일'][d.weekday()] if d else '', row.get('version', ''), clock,
                row.get('detail', ''), tuple(row.get('pickup_characters') or []), row.get('end_date') or '', tz, signature, 'pickup_ids' in row))
    return list({e.identity: e for e in events}.values())


def _discord_send(token, user_id, text, key):
    client = DiscordDM(token, user_id)
    try:
        client.send(message_content(text, public_base_url()), key)
    finally:
        client.close()


def _prune(state, now, active_ids):
    cutoff = (now - timedelta(days=400)).isoformat()
    records = state.setdefault('records', {})
    state['records'] = {key: value for key, value in records.items() if key in active_ids or value.get('sent_at', '') >= cutoff}
    # Preserve the original list (and order) for rollback; bound using event dates.
    pinned = set(state.get('legacy_sent_keys', []))
    keep = []
    for key in state['sent_keys']:
        parts = key.split('|')
        day = schedule.iso_date(parts[2]) if len(parts) > 2 else None
        if key in pinned or day is None or day >= (now - timedelta(days=400)).date():
            keep.append(key)
    state['sent_keys'] = list(dict.fromkeys(keep))


def _run(*, dry_run, test_message='', now=None):
    now = now or datetime.now(SEOUL)
    state = _load_state()
    first_run = not STATE_PATH.exists()
    events = _build_events()
    state.setdefault('legacy_sent_keys', list(state['sent_keys']))
    sent = set(state['sent_keys'])
    records = state.setdefault('records', {})
    state['schema'] = 2
    pending = state['pending']
    by_key = {e.key: e for e in events}
    # Translate queued legacy events using the new display rules; keep unmatched text.
    for item in pending:
        if item['key'] in by_key:
            event = by_key[item['key']]
            item.update(text=event.line(), identity=event.identity, fingerprint=event.fingerprint,
                        legacy_key=event.key, event_date=event.update_date, signature=event.signature, enriched=event.enriched)
    for event in events:
        historical = bool(schedule.iso_date(event.update_date) and schedule.iso_date(event.update_date) < now.date())
        if event.identity not in records and (first_run or event.key in sent or historical):
            records[event.identity] = {'fingerprint': event.fingerprint, 'sent_at': now.isoformat(), 'legacy_key': event.key, 'signature': event.signature, 'enriched': event.enriched}
            if event.key not in sent:
                state['sent_keys'].append(event.key)
                sent.add(event.key)
        # Source IDs arrive only after the first new collection. Bridge the old
        # date/version identity without re-alerting on schema-only enrichment.
        if event.identity not in records:
            match = next((r for r in records.values() if r.get('signature') == event.signature and r.get('legacy_key', '').split('|')[:2] == event.key.split('|')[:2]), None)
            if match:
                records[event.identity] = dict(match)
        old = records.get(event.identity)
        if old and not old.get('enriched') and event.enriched and old.get('signature') == event.signature:
            old.update(fingerprint=event.fingerprint, enriched=True)
        queue_key = event.identity + ':' + event.fingerprint
        if (not first_run and (old is None or old['fingerprint'] != event.fingerprint)
                and not any(p.get('identity') == event.identity and p.get('fingerprint') == event.fingerprint for p in pending)):
            # Replace a queued older revision before it is sent.
            pending[:] = [p for p in pending if p.get('identity') != event.identity]
            pending.append({'key': queue_key, 'identity': event.identity, 'fingerprint': event.fingerprint,
                            'legacy_key': event.key, 'text': ('[일정 변경] ' if old else '') + event.line(),
                            'created_at': now.isoformat(), 'event_date': event.update_date, 'signature': event.signature, 'enriched': event.enriched})
    # One reminder on the day before the source's end date; no guessed clock.
    for event in events:
        end = schedule.iso_date(event.end_date)
        rid = event.identity + ':ending:' + event.end_date
        if event.kind == 'pickup' and end and 0 <= (end - now.date()).days <= 1 and rid not in records and not any(p.get('identity') == rid for p in pending):
            if schedule.iso_date(event.update_date) and schedule.iso_date(event.update_date) > now.date():
                continue
            text = f'[픽업 마감 임박] {event.game_label} {event.version} · 종료일 {event.end_date} (정확한 종료 시각은 원문 확인)\n' + schedule.SOURCES[event.game_id]
            pending.append({'key': rid, 'identity': rid, 'fingerprint': rid, 'legacy_key': '', 'text': text, 'created_at': now.isoformat(), 'event_date': event.end_date})
    if test_message:
        key = 'test|' + now.isoformat()
        pending.append({'key': key, 'identity': key, 'fingerprint': key, 'legacy_key': '', 'text': test_message, 'created_at': now.isoformat()})
    _prune(state, now, {e.identity for e in events})
    if dry_run:
        print(f'[notifier] dry-run: pending={len(pending)}; no sends or writes')
        return 0
    # Persist new messages before attempting delivery, including bootstrap state.
    _save_state(state)
    if not pending or not _in_send_window_kst(now):
        print(f'[notifier] pending={len(pending)}; send_window={_in_send_window_kst(now)}')
        return 0
    retry_at = state.get('delivery_retry_at', '')
    if retry_at and retry_at > now.isoformat():
        print('[notifier] Discord retry delayed; outbox retained')
        return 0
    token = os.getenv('DISCORD_BOT_TOKEN', '').strip()
    user_id = os.getenv('DISCORD_USER_ID', '').strip()
    if not token or not user_id:
        raise DeliveryError('Discord configuration unavailable; outbox retained')
    # One event at a time: a later failure cannot replay already acknowledged events.
    while pending:
        item = pending[0]
        try:
            _discord_send(token, user_id, item['text'], item['key'])
        except DeliveryError as exc:
            state['delivery_error'] = str(exc)
            if exc.retry_after:
                state['delivery_retry_at'] = (now + timedelta(seconds=exc.retry_after)).isoformat()
            _save_state(state)
            raise
        state.pop('delivery_error', None)
        state.pop('delivery_retry_at', None)
        identity = item.get('identity', item['key'])
        records = state['records']
        records[identity] = {'fingerprint': item.get('fingerprint', item['key']), 'sent_at': now.isoformat(), 'legacy_key': item.get('legacy_key', ''), 'signature': item.get('signature', ''), 'enriched': item.get('enriched', False)}
        legacy = item.get('legacy_key') or (item['key'] if item['key'].startswith(('update|', 'broadcast|')) else '')
        if legacy and legacy not in state['sent_keys']:
            state['sent_keys'].append(legacy)
        pending.pop(0)
        _save_state(state)
    print('[notifier] outbox delivered')
    return 0


def run(*, dry_run, test_message=''):
    try:
        if not dry_run and not notifications_enabled():
            print('[notifier] automatic DMs disabled; no sends or writes')
            return 0
        if dry_run:
            return _run(dry_run=True, test_message=test_message)
        with storage.lock('.notify.lock') as acquired:
            return _run(dry_run=False, test_message=test_message) if acquired else 0
    except Exception as exc:
        detail = str(exc) if isinstance(exc, DeliveryError) else type(exc).__name__
        print(f'[notifier] stopped safely ({detail}); history was not reset')
        return 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--test', default='')
    args = parser.parse_args()
    return run(dry_run=args.dry_run, test_message=args.test)


if __name__ == '__main__':
    raise SystemExit(main())
