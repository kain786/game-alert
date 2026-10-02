import importlib.util
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import configuration
from discord_delivery import DiscordDM, DeliveryError, message_content
import notifier
import runtime
import storage
import worker


def response(status=200, data=None, headers=None):
    result = Mock(status_code=status, headers=headers or {})
    result.json.return_value = data if data is not None else {'id': '123456789012345678'}
    return result


class DiscordDelivery(unittest.TestCase):
    def setUp(self):
        self.client = DiscordDM('fixture-token', '123456789012345678')
        self.addCleanup(self.client.close)

    def test_dm_recipient_bot_auth_and_nonce(self):
        with patch.object(self.client.session, 'request', side_effect=[response(), response(), response()]) as request:
            self.client.send('hello', 'event-1')
            self.client.send('hello', 'event-1')
        self.assertEqual(self.client.session.headers['Authorization'], 'Bot fixture-token')
        self.assertEqual(request.call_args_list[0].kwargs['json'], {'recipient_id': self.client.recipient_id})
        self.assertEqual(request.call_args_list[1].args[1], 'https://discord.com/api/v10/channels/123456789012345678/messages')
        body = request.call_args_list[1].kwargs['json']
        self.assertEqual(body['allowed_mentions'], {'parse': []})
        self.assertTrue(body['enforce_nonce'])
        self.assertEqual(body['nonce'], request.call_args_list[2].kwargs['json']['nonce'])
        other = DiscordDM('fixture-token', '123456789012345679')
        with patch.object(other.session, 'request', side_effect=[response(), response()]) as other_request:
            other.send('hello', 'event-1')
        self.assertNotEqual(body['nonce'], other_request.call_args.kwargs['json']['nonce'])
        other.close()

    def test_check_does_not_open_or_send_dm(self):
        with patch.object(self.client.session, 'request', return_value=response()) as request:
            self.client.check()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[:2], ('GET', 'https://discord.com/api/v10/users/@me'))

    def test_content_limit_including_emoji_and_footer(self):
        text = message_content('일정😀' * 2000, 'https://games.example.com')
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 2000)
        self.assertTrue(text.endswith('…\n\nhttps://games.example.com'))

    def test_retry_after_honored(self):
        with patch.object(self.client.session, 'request', side_effect=[response(429, {'retry_after': 1.25}), response()]) as request, patch('discord_delivery.time.sleep') as sleep:
            self.client.check()
        sleep.assert_called_once_with(1.25)
        self.assertEqual(request.call_count, 2)

    def test_long_rate_limit_is_deferred(self):
        with patch.object(self.client.session, 'request', return_value=response(429, {'retry_after': 900})), patch('discord_delivery.time.sleep') as sleep:
            with self.assertRaises(DeliveryError) as result:
                self.client.check()
        self.assertEqual(result.exception.retry_after, 900)
        sleep.assert_not_called()

    def test_failed_auth_or_blocked_dm_explains_fix(self):
        for status, message in ((401, 'token'), (403, 'DM')):
            with self.subTest(status=status), patch.object(self.client.session, 'request', return_value=response(status)):
                with self.assertRaisesRegex(DeliveryError, message):
                    self.client.check()

    def test_http_errors_do_not_leak_credentials(self):
        with patch.object(self.client.session, 'request', side_effect=RuntimeError('Authorization: Bot fixture-token')):
            with self.assertRaises(DeliveryError) as result:
                self.client.check()
        self.assertNotIn('fixture-token', str(result.exception))
        self.assertTrue(result.exception.__suppress_context__)

    def test_bad_ack_is_not_marked_sent(self):
        with patch.object(self.client.session, 'request', return_value=response(data={'ok': True})):
            with self.assertRaisesRegex(DeliveryError, 'acknowledgement'):
                self.client.check()


class Portability(unittest.TestCase):
    def test_origin_configuration(self):
        for value in ('https://games.example.com', 'http://localhost:18136', 'http://127.0.0.1:18136'):
            with patch.dict(os.environ, {'PUBLIC_BASE_URL': value}):
                self.assertEqual(configuration.public_base_url(), value)
        for value in ('http://games.example.com', 'https://name:pass@example.com', 'https://games.example.com/subpath', 'https://games.example.com?x=1'):
            with patch.dict(os.environ, {'PUBLIC_BASE_URL': value}), self.assertRaises(ValueError):
                configuration.public_base_url()

    def test_disabled_notifier_has_no_state_or_network_effects(self):
        with patch.dict(os.environ, {'NOTIFICATIONS_ENABLED': 'false'}), patch.object(notifier, '_run') as run, patch.object(storage, 'lock') as lock:
            self.assertEqual(notifier.run(dry_run=False), 0)
        run.assert_not_called()
        lock.assert_not_called()

    def test_literal_env_no_shell_and_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / '.discord.env'
            file.write_text("DISCORD_BOT_TOKEN='fixture$literal`text`'\nDISCORD_USER_ID=123456789012345678\n")
            with patch.dict(os.environ, {}, clear=False):
                runtime.load_settings(file, runtime.DISCORD_KEYS)
                self.assertEqual(os.environ['DISCORD_BOT_TOKEN'], 'fixture$literal`text`')
            file.write_text('PATH=/bad\n')
            with self.assertRaises(ValueError):
                runtime.load_settings(file, runtime.DISCORD_KEYS)

    def test_manual_request_ack_survives_worker_restart(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(storage, 'CACHE_DIR', Path(temp)):
            storage.atomic_json(storage.CACHE_DIR / 'refresh-request.json', {'requested_at': 'fixture-stamp'})
            self.assertEqual(worker.request_stamp(), 'fixture-stamp')
            self.assertNotEqual(worker.request_stamp(), worker.handled_stamp())
            storage.atomic_json(storage.CACHE_DIR / 'refresh-request.handled.json', {'requested_at': worker.request_stamp()})
            self.assertEqual(worker.request_stamp(), worker.handled_stamp())

    def test_launchagents_roundtrip_with_spaces_and_no_secrets(self):
        script = Path(__file__).resolve().parent.parent / 'scripts' / 'macos_service.py'
        spec = importlib.util.spec_from_file_location('macos_service', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, 'ROOT', Path('/Users/Example User/game-alert')):
            for kind in module.KINDS:
                value = plistlib.loads(plistlib.dumps(module.definition(kind, Path('/Users/Example User'))))
                self.assertEqual(value['ProgramArguments'][0], '/Users/Example User/game-alert/.venv/bin/python')
                self.assertNotIn('DISCORD_BOT_TOKEN', value['EnvironmentVariables'])
                self.assertGreater(value['ExitTimeOut'], 210)
            with tempfile.TemporaryDirectory() as temp:
                file = Path(temp) / 'agent.plist'
                file.write_bytes(plistlib.dumps({'ProgramArguments': ['/other/app']}))
                with self.assertRaises(RuntimeError):
                    module.verify_owned(file, 'web')


if __name__ == '__main__':
    unittest.main()
