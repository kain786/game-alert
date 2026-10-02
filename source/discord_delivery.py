"""One opted-in recipient, Discord Bot REST API, no Gateway or privileged intents."""
import hashlib
import math
import time
import requests

API = 'https://discord.com/api/v10'


class DeliveryError(RuntimeError):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def message_content(text, site_url):
    footer = '\n\n' + site_url
    limit = 2000 - len(footer.encode('utf-16-le')) // 2
    if limit < 20:
        raise DeliveryError('PUBLIC_BASE_URL is too long for a Discord message')
    if len(text.encode('utf-16-le')) // 2 > limit:
        while len((text + '…').encode('utf-16-le')) // 2 > limit:
            text = text[:-1]
        text += '…'
    return text + footer


class DiscordDM:
    def __init__(self, token, recipient_id):
        if not token or not recipient_id.isascii() or not recipient_id.isdigit() or not 15 <= len(recipient_id) <= 22:
            raise DeliveryError('Set DISCORD_BOT_TOKEN and a numeric DISCORD_USER_ID (15–22 digits)')
        self.recipient_id = recipient_id
        self.channel_id = None
        self.session = requests.Session()
        self.session.headers.update({'Authorization': 'Bot ' + token,
                                     'User-Agent': 'GameAlertDistribution/1.0'})

    def close(self):
        self.session.close()

    def request(self, method, path, payload=None):
        for attempt in range(3):
            try:
                response = self.session.request(method, API + path, json=payload, timeout=20, allow_redirects=False)
                if response.status_code == 429:
                    data = response.json()
                    delay = float(data.get('retry_after', response.headers.get('Retry-After', 60)))
                    if not math.isfinite(delay) or delay < 0:
                        delay = 60
                    if delay > 30 or attempt == 2:
                        raise DeliveryError('Discord rate limit; queued messages retained', delay)
                    time.sleep(max(0.1, delay))
                    continue
                if response.status_code == 401:
                    raise DeliveryError('Discord rejected the bot token; check DISCORD_BOT_TOKEN')
                if response.status_code == 403:
                    raise DeliveryError('Discord blocked the DM; check shared server, DM permissions and bot blocking')
                if not 200 <= response.status_code < 300:
                    raise DeliveryError(f'Discord returned HTTP {response.status_code}; queued messages retained')
                data = response.json()
                if not isinstance(data, dict) or not str(data.get('id', '')).isdigit():
                    raise DeliveryError('Discord returned an invalid acknowledgement')
                return data
            except DeliveryError:
                raise
            except Exception:
                # Never echo requests exceptions, response bodies or authorization headers.
                raise DeliveryError('Discord connection or response failed; queued messages retained') from None

    def check(self):
        return self.request('GET', '/users/@me')

    def send(self, text, key):
        if self.channel_id is None:
            self.channel_id = self.request('POST', '/users/@me/channels', {'recipient_id': self.recipient_id})['id']
        nonce = str(int(hashlib.sha256((self.recipient_id + '|' + key).encode()).hexdigest()[:15], 16))
        return self.request('POST', f'/channels/{self.channel_id}/messages', {
            'content': text, 'allowed_mentions': {'parse': []}, 'flags': 4,
            'nonce': nonce, 'enforce_nonce': True,
        })
