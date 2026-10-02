"""Schedule presentation and calendar export, without external network access."""
import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import storage

SEOUL = ZoneInfo('Asia/Seoul')
GAMES = {'genshin': '원신', 'zzz': '젠레스', 'hsr': '스타레일'}
SOURCES = {
    'genshin': 'https://genshin-impact.fandom.com/wiki/Wish/List',
    'zzz': 'https://zenless-zone-zero.fandom.com/wiki/Exclusive_Channel/History',
    'hsr': 'https://honkai-star-rail.fandom.com/wiki/Character_Event_Warp',
    'genshin_broadcast': 'https://genshin-impact.fandom.com/wiki/Special_Program',
    'zzz_broadcast': 'https://zenless-zone-zero.fandom.com/wiki/Special_Program',
    'hsr_broadcast': 'https://honkai-star-rail.fandom.com/wiki/Special_Program',
}
ICONS = {'genshin': '/static/icons/genshin.webp', 'zzz': '/static/icons/zzz.webp', 'hsr': '/static/icons/starrail.webp'}


def iso_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def localized(row):
    """Convert only explicitly zoned times. Unknown zones remain visibly unknown."""
    day, clock, tz = row.get('update_date'), row.get('time', ''), row.get('tz', '')
    offset = re.fullmatch(r'(?:UTC|GMT)([+-])(\d{1,2})(?::(\d{2}))?', tz or '')
    try:
        if tz == 'KST':
            zone = SEOUL
        elif offset:
            hours, minutes = int(offset[2]), int(offset[3] or 0)
            if hours > 14 or minutes > 59:
                raise ValueError('Invalid offset')
            zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if offset[1] == '+' else -1)))
        else:
            return day, clock, '시간대 미확인' if clock else '', None
        dt = datetime.fromisoformat(f'{day}T{clock}').replace(tzinfo=zone).astimezone(SEOUL)
        return dt.date().isoformat(), dt.strftime('%H:%M'), 'KST', dt.isoformat()
    except (ValueError, TypeError):
        return day, clock, '시간대 미확인' if clock else '', None


def event_id(game, kind, row):
    identity = row.get('source_id') or f"{row.get('version', '').strip()}|{row.get('update_date') or 'TBA'}"
    return hashlib.sha256(f'{game}|{kind}|{identity}'.encode()).hexdigest()[:24]


def event_rows(sources=None, *, now=None):
    now = now or datetime.now(SEOUL)
    today = now.astimezone(SEOUL).date()
    rows = []
    for key, source in (sources if sources is not None else storage.read_sources()).items():
        game = key.split('_')[0]
        kind = 'broadcast' if key.endswith('_broadcast') else 'pickup'
        for raw in source['data']:
            day, clock, tz, start_at = localized(raw) if kind == 'broadcast' else (raw.get('update_date'), '', '', None)
            d, end = iso_date(day), iso_date(raw.get('end_date'))
            if end and d and end < d:
                end = None
            delta = (d - today).days if d else None
            status = '미정' if d is None else ('오늘' if delta == 0 else ('예정' if delta > 0 else '지난 일정'))
            if kind == 'broadcast' and start_at and datetime.fromisoformat(start_at) <= now:
                status = '시작 시각 지남'
            if kind == 'pickup' and d and end and d <= today <= end:
                status = '종료일' if end == today else ('마감 임박' if (end - today).days <= 2 else '진행 중')
            item = {**raw, 'id': event_id(game, kind, raw), 'game_id': game, 'game': GAMES[game],
                    'kind': kind, 'kind_label': '공식 방송' if kind == 'broadcast' else '픽업 시작',
                    'update_date': day, 'time': clock, 'tz': tz, 'start_at': start_at,
                    'end_date': end.isoformat() if end else None,
                    'weekday': ['월','화','수','목','금','토','일'][d.weekday()] if d else '',
                    'dday': ('D-day' if delta == 0 else f'D-{delta}' if delta and delta > 0 else ''),
                    'status': status, 'source_url': SOURCES[key], 'source_key': key,
                    'stale': source['meta'].get('stale', False)}
            rows.append(item)
    # Repeated source rows should not create duplicate notifications or calendars.
    return list({row['id']: row for row in rows}.values())


def sort_key(item):
    d = iso_date(item.get('update_date'))
    return (d is None, d or date.max, 0 if item['kind'] == 'broadcast' else 1, item.get('time', ''), item['game_id'])


def payload(game='', *, now=None):
    now = now or datetime.now(SEOUL)
    today = now.astimezone(SEOUL).date().isoformat()
    sources = storage.read_sources()
    rows = sorted(event_rows(sources, now=now), key=sort_key)
    if game in GAMES:
        rows = [r for r in rows if r['game_id'] == game]
    statuses = [{'key': k, 'game': GAMES[k.split('_')[0]], 'kind': '방송' if k.endswith('_broadcast') else '픽업',
                 **v['meta'], 'count': len(v['data']), 'source_url': SOURCES[k]} for k, v in sources.items()
                if game not in GAMES or k.split('_')[0] == game]
    for status in statuses:
        try:
            status['last_success'] = datetime.fromisoformat(status['fetched_at']).astimezone(SEOUL).strftime('%m/%d %H:%M KST')
        except (KeyError, ValueError, TypeError):
            status['last_success'] = '갱신 기록 없음'
    return {'today': today, 'timezone': 'Asia/Seoul', 'cache_ttl': storage.TTL,
            'games': [{'id': g, 'name': name, 'rows': sources[g]['data'], **sources[g]['meta']} for g, name in GAMES.items()],
            'sources': statuses, 'degraded': any(s.get('stale') for s in statuses),
            'today_items': [r for r in rows if r['update_date'] == today],
            'ongoing': [r for r in rows if r['kind'] == 'pickup' and r.get('end_date') and (r.get('update_date') or '9999') <= today <= r['end_date']],
            'upcoming': [r for r in rows if not r['update_date'] or r['update_date'] > today],
            'past': sorted([r for r in rows if r['update_date'] and r['update_date'] < today and r not in [i for i in rows if i['status'] in ('진행 중', '마감 임박', '종료일')]], key=sort_key, reverse=True),
            'icons': ICONS, 'refresh': storage.job_status(), 'selected_game': game if game in GAMES else ''}


def ics_escape(value):
    return str(value).replace('\\', '\\\\').replace('\r', '').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def fold_line(line):
    parts, part = [], ''
    for char in line:
        if len((part + char).encode()) > 75:
            parts.append(part)
            part = ' '
        part += char
    return '\r\n'.join(parts + [part])


def calendar(game=''):
    sources = storage.read_sources()
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//GameAlert//Schedule//KO', 'CALSCALE:GREGORIAN', 'X-WR-CALNAME:게임 일정']
    for row in sorted(event_rows(sources), key=sort_key):
        if game in GAMES and row['game_id'] != game:
            continue
        day = iso_date(row['update_date'])
        if not day:
            continue
        # Unknown-zone clock values must not silently become local timed events.
        variants = [('start', day, row['kind_label'])]
        if row['kind'] == 'pickup' and iso_date(row.get('end_date')):
            variants.append(('end', iso_date(row['end_date']), '픽업 종료일'))
        for variant, event_day, label in variants:
            stamp = sources[row['source_key']]['meta'].get('fetched_at')
            try:
                updated = datetime.fromisoformat(stamp).astimezone(timezone.utc)
            except (ValueError, TypeError):
                updated = datetime(1970, 1, 1, tzinfo=timezone.utc)
            lines += ['BEGIN:VEVENT', f"UID:{row['id']}-{variant}@game-alert.local", 'DTSTAMP:' + updated.strftime('%Y%m%dT%H%M%SZ'), 'LAST-MODIFIED:' + updated.strftime('%Y%m%dT%H%M%SZ')]
            if variant == 'start' and row.get('start_at'):
                lines += ['DTSTART:' + datetime.fromisoformat(row['start_at']).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')]
            else:
                lines += ['DTSTART;VALUE=DATE:' + event_day.strftime('%Y%m%d'), 'DTEND;VALUE=DATE:' + (event_day + timedelta(days=1)).strftime('%Y%m%d')]
            description = ', '.join(row.get('pickup_characters') or [])
            if row['time'] and row['tz'] != 'KST':
                description += f" / 원본 시각 {row['time']} (시간대 미확인)"
            if variant == 'end':
                description += ' / 정확한 종료 시각은 원문 확인'
            lines += ['SUMMARY:' + ics_escape(f"{row['game']} {row.get('version', '')} {label}"),
                      'DESCRIPTION:' + ics_escape(description), 'URL:' + row['source_url'], 'END:VEVENT']
    lines += ['END:VCALENDAR']
    return '\r\n'.join(fold_line(line) for line in lines) + '\r\n'
