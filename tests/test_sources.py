import unittest
from unittest.mock import patch
from datetime import date
import schedule_sources as sources


class SourceParsers(unittest.TestCase):
    def test_broadcast_table_not_first(self):
        html = '''<table><tr><td>Unrelated</td></tr></table><table><tr><td>Version 1.0</td><td>2026-09-12 20:00 (UTC+8)</td></tr></table>'''
        with patch.object(sources, '_fetch_fandom_parse_html', return_value=html):
            rows = sources.fetch_genshin_broadcasts()
        self.assertEqual(rows[0].broadcast_time, '20:00')
        self.assertEqual(rows[0].broadcast_tz, 'UTC+8')

    def test_no_schedule_table_rejected(self):
        with patch.object(sources, '_fetch_fandom_parse_html', return_value='<table><tr><td>Unrelated</td></tr></table>'):
            with self.assertRaises(RuntimeError):
                sources.fetch_genshin_broadcasts()

    def test_duplicate_broadcasts_removed(self):
        row = '<tr><td>Version 1.0</td><td>2026-09-12 20:00 (UTC+8)</td></tr>'
        with patch.object(sources, '_fetch_fandom_parse_html', return_value='<table>' + row * 2 + '</table>'):
            self.assertEqual(len(sources.fetch_zzz_broadcasts()), 1)

    def test_zzz_end_dates(self):
        html = '''<table><tr><th>Signal Search</th><th>Featured</th><th>Date Start</th><th>Date End</th><th>Version</th></tr>
        <tr><td><a title="Banner/2026">Banner</a></td><td><a title="Agent">Agent</a></td><td>September 9, 2026</td><td>September 29, 2026</td><td>3.2</td></tr></table>'''
        with patch.object(sources, '_fetch_fandom_parse_html', return_value=html):
            row = sources.fetch_zzz_updates()[0]
        self.assertEqual(row.end_date, date(2026,9,29))
        self.assertEqual(row.source_id, 'Banner/2026')

    def test_genshin_phase_end(self):
        html = '''<table><tr><th>Version 1.0 : September 9, 2026 — September 29, 2026</th></tr>
        <tr><td>Character Event</td><td><a title="Wish/2026">Wish</a></td></tr></table>'''
        with patch.object(sources, '_parse_genshin_character_event_wish_map', return_value={'Wish':'Character'}), patch.object(sources, '_fetch_fandom_parse_html', return_value=html):
            row = sources.fetch_genshin_updates()[0]
        self.assertEqual(row.end_date, date(2026,9,29))
        self.assertEqual(row.source_id, 'Wish/2026')

    def test_hsr_phase_end(self):
        html = '''<table><tr><th>Image</th><th>Name</th><th>Start</th><th>End</th></tr><tr><td>image</td><td><a title="Warp/2026">Warp</a></td><td>September 9, 2026</td><td>September 29, 2026</td></tr></table>'''
        with patch.object(sources, '_fetch_fandom_parse_html', return_value=html), patch.object(sources, '_parse_hsr_warp_occurrence', return_value=('1.0',['Character'])):
            row = sources.fetch_hsr_updates(max_workers=1)[0]
        self.assertEqual(row.end_date, date(2026,9,29))
        self.assertEqual(row.source_id, 'Warp/2026')

    def test_date_unknown(self):
        self.assertIsNone(sources._parse_date_maybe('TBA'))


if __name__ == '__main__':
    unittest.main()
