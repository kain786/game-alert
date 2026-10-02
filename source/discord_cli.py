"""Explicit bot check or one DM test, independent of the schedule outbox."""
import argparse
import os
import uuid
from configuration import public_base_url
from discord_delivery import DiscordDM, DeliveryError, message_content


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true', help='Validate bot credentials without sending a DM')
    group.add_argument('--send-test', action='store_true', help='Explicitly send one test DM')
    args = parser.parse_args()
    client = None
    try:
        client = DiscordDM(os.getenv('DISCORD_BOT_TOKEN', '').strip(), os.getenv('DISCORD_USER_ID', '').strip())
        client.check()
        if args.send_test:
            client.send(message_content('게임알리미 디스코드 DM 테스트입니다.', public_base_url()), 'test:' + uuid.uuid4().hex)
            print('Test DM delivered. Schedule history was not changed.')
        else:
            print('Bot credentials accepted. No DM sent; recipient DM permissions remain unverified.')
        return 0
    except (DeliveryError, ValueError) as exc:
        print(str(exc))
        return 1
    finally:
        if client:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
