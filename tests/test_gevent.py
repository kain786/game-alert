"""Isolated regressions: no live cache, outbound network, or Discord sends."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
import storage
import schedule
import refresh
import notifier
from app import app

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=schedule.SEOUL)


def fixture_sources():
    return {key: {'meta': {'fetched_at': storage.utcnow().isoformat(), 'stale': False, 'error': ''}, 'data': [
        {'version': '버전 1.0', 'update_date': '2026-09-13', 'end_date': None, 'pickup_characters': ['캐릭터'],
         **({'time': '20:00', 'tz': 'UTC+8', 'detail': '공식 방송'} if key.endswith('broadcast') else {})}]}
        for key in storage.KEYS}


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.dict(os.environ, {'PUBLIC_BASE_URL': 'https://games.example.com', 'NOTIFICATIONS_ENABLED': 'true'}), patch.object(storage, 'CACHE_DIR', self.root), patch.object(notifier, 'CACHE_DIR', self.root),
                        patch.object(notifier, 'STATE_PATH', self.root / 'notify_state.json')]
        for p in self.patches:
            p.start()
        self.sources = fixture_sources()
        for key, value in self.sources.items():
            storage.atomic_json(self.root / f'{key}.json', value)
        self.client = app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def fingerprint(self):
        return {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.root.iterdir() if p.is_file()}

    def save_state(self, state):
        storage.atomic_json(notifier.STATE_PATH, state)

    def test_gunicorn_worker_dependencies(self):
        from gunicorn.util import load_class
        self.assertIsNotNone(load_class("gevent"))

    def test_kst(self):
        self.assertEqual(schedule.localized({'update_date': '2026-09-12', 'time': '20:00', 'tz': 'UTC+8'})[:3], ('2026-09-12', '21:00', 'KST'))

    def test_date_boundary(self):
        self.assertEqual(schedule.localized({'update_date': '2026-09-12', 'time': '23:30', 'tz': 'UTC+8'})[:2], ('2026-09-13', '00:30'))

    def test_unknown_zone(self):
        value = schedule.localized({'update_date': '2026-09-12', 'time': '20:00'})
        self.assertEqual(value[2], '시간대 미확인')
        self.assertIsNone(value[3])

    def test_invalid_zone_and_clock(self):
        self.assertIsNone(schedule.localized({'update_date': 'x', 'time': '90:10', 'tz': 'UTC+30'})[3])

    def test_date_first_sort(self):
        rows = [{'game_id': 'genshin', 'kind': 'broadcast', 'update_date': '2026-09-20'}, {'game_id': 'zzz', 'kind': 'pickup', 'update_date': '2026-09-13'}]
        self.assertEqual(sorted(rows, key=schedule.sort_key)[0]['update_date'], '2026-09-13')

    def test_today_uses_local_day(self):
        self.sources['genshin_broadcast']['data'][0].update(update_date='2026-09-11', time='23:30')
        with patch.object(storage, 'read_sources', return_value=self.sources):
            result = schedule.payload(now=NOW)
        self.assertEqual(result['today_items'][0]['time'], '00:30')

    def test_same_day_broadcast_first_tba_last(self):
        rows = [{'game_id': 'genshin', 'kind': k, 'update_date': d} for k, d in [('pickup', '2026-09-13'), ('broadcast', None), ('broadcast', '2026-09-13')]]
        result = sorted(rows, key=schedule.sort_key)
        self.assertEqual(result[0]['kind'], 'broadcast')
        self.assertIsNone(result[-1]['update_date'])

    def test_ongoing_and_ending(self):
        self.sources['genshin']['data'][0].update(update_date='2026-09-01', end_date='2026-09-13')
        with patch.object(storage, 'read_sources', return_value=self.sources):
            result = schedule.payload(now=NOW)
        self.assertEqual(result['ongoing'][0]['status'], '마감 임박')
        self.assertFalse(any(r['game_id'] == 'genshin' and r['kind'] == 'pickup' for r in result['past']))

    def test_missing_end_not_invented(self):
        self.sources['genshin']['data'][0].update(update_date='2026-09-01')
        with patch.object(storage, 'read_sources', return_value=self.sources):
            self.assertFalse(schedule.payload(now=NOW)['ongoing'])

    def test_empty_parse_rejected(self):
        with self.assertRaises(ValueError):
            refresh.validate([], self.sources['genshin']['data'])

    def test_invalid_dates_rejected(self):
        with self.assertRaises(ValueError):
            refresh.validate([{'version': 'v', 'update_date': 'bad'}], [])

    def test_row_drop_rejected(self):
        with self.assertRaises(ValueError):
            refresh.validate(self.sources['genshin']['data'], self.sources['genshin']['data'] * 8)

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValueError):
            refresh.validate([{'version': 'v', 'update_date': '2026-09-13', 'end_date': '2026-09-01'}], [])

    def test_corrupt_cache_degrades_without_writes(self):
        (self.root / 'genshin.json').write_text('{bad')
        before = self.fingerprint()
        result = self.client.get('/api/schedule')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['degraded'])
        self.assertEqual(before, self.fingerprint())

    def test_snapshot_recovery(self):
        storage.save_sources(self.sources)
        storage.save_sources(self.sources)
        (self.root / 'schedule_snapshot.json').write_text('{bad')
        result = storage.read_sources()
        self.assertTrue(result['genshin']['meta']['stale'])
        self.assertEqual(result['genshin']['data'], self.sources['genshin']['data'])

    def test_read_expired_cache_no_network_or_writes(self):
        self.sources['genshin']['meta']['fetched_at'] = '2020-01-01T00:00:00+00:00'
        storage.atomic_json(self.root / 'genshin.json', self.sources['genshin'])
        before = self.fingerprint()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
            result = self.client.get('/?refresh=1')
            self.assertEqual(result.status_code, 200)
            self.assertIn('마지막으로 확인된 일정', result.text)
        self.assertEqual(before, self.fingerprint())

    def test_post_csrf_and_get_refresh(self):
        self.assertEqual(self.client.get('/api/refresh').status_code, 405)
        self.assertEqual(self.client.post('/api/refresh').status_code, 403)
        self.assertEqual(self.client.post('/api/refresh', headers={'Origin': 'https://evil.invalid', 'X-Gevent-Request': 'refresh'}).status_code, 403)
        self.assertFalse((self.root / 'refresh-request.json').exists())

    def test_manual_request_only_marker_and_cooldown(self):
        headers = {'Origin': 'https://games.example.com', 'X-Gevent-Request': 'refresh'}
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.client.post('/api/refresh', headers=headers).status_code, 202)
            self.assertEqual(self.client.post('/api/refresh', headers=headers).status_code, 429)
        self.assertTrue((self.root / 'refresh-request.json').exists())

    def test_running_status_expiry(self):
        storage.atomic_json(self.root / 'refresh_status.json', {'state': 'running', 'started_at': '2020-01-01T00:00:00+00:00'})
        self.assertEqual(storage.job_status()['state'], 'failed')

    def test_etag(self):
        first = self.client.get('/api/schedule')
        self.assertEqual(self.client.get('/api/schedule', headers={'If-None-Match': first.headers['ETag']}).status_code, 304)

    def test_template_filters_and_order(self):
        response = self.client.get('/?game=zzz')
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.text.index('id="today-title"'), response.text.index('id="upcoming-title"'))
        self.assertIn('value="zzz" selected', response.text)
        self.assertIn('calendar.ics?game=zzz', response.text)
        self.assertIn('script-src \'self\'', response.headers['Content-Security-Policy'])
        self.assertEqual(self.client.get('/api/sleep/30').status_code, 404)

    def test_calendar_kst_to_utc_and_folding(self):
        self.sources['genshin_broadcast']['data'][0]['version'] = '버전' * 60
        with patch.object(storage, 'read_sources', return_value=self.sources):
            ics = schedule.calendar('genshin')
        self.assertIn('DTSTART:20260913T120000Z', ics)
        self.assertTrue(all(len(line.encode()) <= 75 for line in ics.split('\r\n')))
        self.assertNotIn('젠레스', ics)

    def test_calendar_stable_uid_and_end_date(self):
        self.sources['genshin']['data'][0]['end_date'] = '2026-10-01'
        with patch.object(storage, 'read_sources', return_value=self.sources):
            a, b = schedule.calendar('genshin'), schedule.calendar('genshin')
        self.assertEqual(a, b)
        self.assertIn('DTSTART;VALUE=DATE:20261001', a)
        self.assertIn('DTEND;VALUE=DATE:20261002', a)

    def test_name_failures_retry_and_fallback(self):
        resolver = refresh.NameResolver(None)
        with patch.object(refresh, 'fetch_official_name_ko', side_effect=RuntimeError('temporary')) as get:
            self.assertEqual(resolver.resolve('example', 'A_Name'), 'A Name')
            resolver.resolve('example', 'A_Name')
            self.assertEqual(get.call_count, 1)
        self.assertEqual(resolver.meta['example:A_Name']['state'], 'failed')

    def test_legacy_empty_names_retried(self):
        storage.atomic_json(self.root / 'ko_names.json', {'data': {'example:A': ''}})
        resolver = refresh.NameResolver(None)
        with patch.object(refresh, 'fetch_official_name_ko', return_value='이름'):
            self.assertEqual(resolver.resolve('example', 'A'), '이름')

    def test_refresh_failure_preserves_good_data(self):
        with patch.object(refresh, 'collect', side_effect=ValueError('bad source')):
            self.assertEqual(refresh.run(), 1)
        sources = storage.read_sources()
        self.assertEqual(sources['genshin']['data'], self.sources['genshin']['data'])
        self.assertTrue(sources['genshin']['meta']['stale'])

    def test_refresh_retry_backoff(self):
        with patch.object(refresh, 'collect', side_effect=ValueError('bad')) as collect:
            refresh.run()
            refresh.run()
            self.assertEqual(collect.call_count, 6)

    def test_refresh_partial_success(self):
        def collect(key, *args):
            if key == 'zzz':
                raise ValueError('bad')
            return self.sources[key]['data']
        with patch.object(refresh, 'collect', side_effect=collect):
            self.assertEqual(refresh.run(), 1)
        self.assertEqual(storage.job_status()['failed_sources'], ['zzz'])

    def test_notifier_timezone(self):
        event = next(e for e in notifier._build_events() if e.kind == 'broadcast')
        self.assertIn('21:00 KST', event.line())

    def test_dry_run_never_writes_or_sends(self):
        before = self.fingerprint()
        with patch.object(notifier, '_discord_send', side_effect=AssertionError('send forbidden')):
            self.assertEqual(notifier.run(dry_run=True), 0)
        self.assertEqual(before, self.fingerprint())

    def test_corrupt_state_fails_closed(self):
        notifier.STATE_PATH.write_text('{bad')
        before = notifier.STATE_PATH.read_bytes()
        with patch.object(notifier, '_discord_send', side_effect=AssertionError('send forbidden')):
            self.assertEqual(notifier.run(dry_run=False), 1)
        self.assertEqual(notifier.STATE_PATH.read_bytes(), before)

    def test_legacy_state_migrates_without_resend(self):
        keys = [e.key for e in notifier._build_events()]
        self.save_state({'sent_keys': keys, 'pending': []})
        with patch.object(notifier, '_discord_send') as send:
            self.assertEqual(notifier._run(dry_run=False, now=NOW), 0)
            send.assert_not_called()
        state = notifier._load_state()
        self.assertEqual(set(state['sent_keys']), set(keys))
        self.assertEqual(len(state['records']), 6)

    def test_partial_delivery_records_each_success(self):
        self.save_state({'sent_keys': [], 'pending': []})
        with patch.dict(os.environ, {'DISCORD_BOT_TOKEN': 'fixture-token', 'DISCORD_USER_ID': 'fixture-chat'}), patch.object(notifier, '_discord_send', side_effect=[None, RuntimeError('test failure')]):
            with self.assertRaises(RuntimeError):
                notifier._run(dry_run=False, now=NOW)
        state = notifier._load_state()
        self.assertEqual(len(state['pending']), 5)
        self.assertEqual(len(state['records']), 1)
        with patch.dict(os.environ, {'DISCORD_BOT_TOKEN': 'fixture-token', 'DISCORD_USER_ID': 'fixture-chat'}), patch.object(notifier, '_discord_send') as send:
            notifier._run(dry_run=False, now=NOW)
            self.assertEqual(send.call_count, 5)

    def test_outside_window_queued_and_dry_run_unchanged(self):
        self.save_state({'sent_keys': [], 'pending': []})
        with patch.object(notifier, '_discord_send') as send:
            notifier._run(dry_run=False, now=NOW.replace(hour=21))
            send.assert_not_called()
        self.assertEqual(len(notifier._load_state()['pending']), 6)
        before = self.fingerprint()
        notifier._run(dry_run=True, now=NOW)
        self.assertEqual(before, self.fingerprint())

    def test_window_boundaries(self):
        for hour, minute, second, expected in [(10,0,0,True),(20,0,0,True),(9,59,59,False),(20,0,1,False)]:
            self.assertEqual(notifier._in_send_window_kst(NOW.replace(hour=hour,minute=minute,second=second)), expected)

    def test_original_legacy_keys_remain_preserved(self):
        keys = [e.key for e in notifier._build_events()] + ['update|genshin|2020-01-01||old||']
        self.save_state({'sent_keys': keys, 'pending': []})
        notifier._run(dry_run=False, now=NOW)
        self.assertEqual(set(notifier._load_state()['sent_keys']), set(keys))

    def test_history_expansion_does_not_alert(self):
        keys = [e.key for e in notifier._build_events()]
        self.save_state({'sent_keys': keys, 'pending': []})
        row = dict(self.sources['genshin']['data'][0], version='older', update_date='2026-01-01')
        self.sources['genshin']['data'].append(row)
        storage.atomic_json(self.root/'genshin.json', self.sources['genshin'])
        with patch.object(notifier, '_discord_send') as send:
            notifier._run(dry_run=False, now=NOW)
            send.assert_not_called()

    def test_enrichment_no_resend_and_real_pickup_change_alerts(self):
        keys = [e.key for e in notifier._build_events()]
        self.save_state({'sent_keys': keys, 'pending': []})
        notifier._run(dry_run=False, now=NOW)
        row = self.sources['genshin']['data'][0]
        row.update(source_id='wish/occurrence', pickup_ids=['Original'], pickup_characters=['한국어 수정'])
        storage.atomic_json(self.root/'genshin.json', self.sources['genshin'])
        with patch.object(notifier, '_discord_send') as send:
            notifier._run(dry_run=False, now=NOW)
            send.assert_not_called()
        row['pickup_ids'] = ['Changed']
        storage.atomic_json(self.root/'genshin.json', self.sources['genshin'])
        with patch.dict(os.environ, {'DISCORD_BOT_TOKEN':'fixture', 'DISCORD_USER_ID':'fixture'}), patch.object(notifier, '_discord_send') as send:
            notifier._run(dry_run=False, now=NOW)
            self.assertEqual(send.call_count, 1)

    def test_end_reminder_once(self):
        self.sources['genshin']['data'][0].update(update_date='2026-09-01', end_date='2026-09-13')
        storage.atomic_json(self.root/'genshin.json', self.sources['genshin'])
        self.save_state({'sent_keys':[e.key for e in notifier._build_events()], 'pending':[]})
        with patch.dict(os.environ, {'DISCORD_BOT_TOKEN':'fixture','DISCORD_USER_ID':'fixture'}), patch.object(notifier, '_discord_send') as send:
            notifier._run(dry_run=False, now=NOW)
            notifier._run(dry_run=False, now=NOW + timedelta(days=1))
            self.assertEqual(send.call_count, 1)

    def test_lock_exclusion(self):
        with storage.lock('.test.lock') as one:
            with storage.lock('.test.lock') as two:
                self.assertTrue(one)
                self.assertFalse(two)

    def test_no_fixed_temporary_names_left(self):
        for i in range(10):
            storage.atomic_json(self.root/'atomic.json', {'i': i})
        self.assertEqual(storage.read_json(self.root/'atomic.json'), {'i': 9})
        self.assertFalse(list(self.root.glob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
