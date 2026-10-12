"""Explicitly configured outbound-only event delivery; never invoked by Slack."""
import argparse
import base64
import json
import logging
from pathlib import Path
import random
import stat
import time
from urllib.parse import urlsplit

from rook_events import canonical, sha256, validate_attachment, validate_event, validate_receipt
from rook_outbox import Outbox


class DeliveryError(Exception):
    def __init__(self, code, permanent=False, pause=False):
        super().__init__(code)
        self.code, self.permanent, self.pause = code, permanent, pause


class HTTPSender:
    def __init__(self, endpoint, token, client=None):
        url = urlsplit(endpoint)
        if (url.scheme != 'https' or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment):
            raise ValueError('Expected HTTPS endpoint without credentials, query or fragment')
        if (not isinstance(token, str) or not 16 <= len(token) <= 4096
                or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in token)):
            raise ValueError('Invalid device token')
        self.endpoint, self.token = endpoint, token
        if client is None:
            import httpx
            # No environment proxy, redirects or insecure-TLS option. A redirect
            # must never forward the device credential to another host.
            client = httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
        self.client = client

    def close(self):
        self.client.close()

    def send(self, delivery):
        import httpx
        # Preserve exact canonical bytes through JavaScript/other receivers;
        # reserializing a parsed event could change e.g. 1.0 to 1.
        content = canonical({'event_json': delivery.payload.decode('utf-8'),
                             'image_base64': (base64.b64encode(delivery.image).decode('ascii')
                                              if delivery.image is not None else None)})
        try:
            with self.client.stream('POST', self.endpoint, content=content,
                                    follow_redirects=False,
                                    headers={'Authorization': 'Bearer ' + self.token,
                                             'Content-Type': 'application/json',
                                             'Accept-Encoding': 'identity',
                                             'Idempotency-Key': delivery.event['device_id'] + '/' + delivery.event['event_id']}) as response:
                code = response.status_code
                if code not in (200, 201):
                    if code in (408, 425, 429) or code >= 500:
                        raise DeliveryError(f'http_{code}')
                    # 401/403 need token repair; 3xx/404 suggest a wrong endpoint.
                    raise DeliveryError(f'http_{code}', permanent=True,
                                        pause=code in (401, 403, 404) or 300 <= code < 400)
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=4096):
                    data.extend(chunk)
                    if len(data) > 4096:
                        raise DeliveryError('receipt_too_large')
                try:
                    receipt = json.loads(data)
                    validate_receipt(receipt, delivery.event, sha256(delivery.payload))
                except (ValueError, TypeError, KeyError, OverflowError):
                    raise DeliveryError('invalid_receipt') from None
                return receipt
        except httpx.HTTPError:
            # Exception text can contain request details. Never persist/log it.
            raise DeliveryError('network_error') from None


class Worker:
    def __init__(self, outbox, sender, clock=time.time, jitter=random.uniform):
        self.outbox, self.sender, self.clock, self.jitter = outbox, sender, clock, jitter

    def run_once(self):
        delivery = self.outbox.claim(now=self.clock())
        if delivery is None:
            return 'idle'
        try:
            if validate_event(delivery.event) != delivery.payload:
                raise ValueError('Payload is not canonical')
            if delivery.event['device_id'] != delivery.device_id or delivery.event['event_id'] != delivery.event_id:
                raise ValueError('Payload identity changed')
            validate_attachment(delivery.event, delivery.image)
        except (ValueError, TypeError, KeyError):
            self.outbox.failed(delivery, 'invalid_local_event', blocked=True, now=self.clock())
            return 'blocked'
        try:
            receipt = self.sender.send(delivery)
            try:
                validate_receipt(receipt, delivery.event, sha256(delivery.payload))
            except (ValueError, TypeError, KeyError, OverflowError):
                raise DeliveryError('invalid_receipt') from None
        except DeliveryError as error:
            delay = min(3600, 5 * 2 ** min(delivery.attempts - 1, 10))
            delay = min(3600, delay * self.jitter(1, 1.25))
            self.outbox.failed(delivery, error.code, retry_seconds=delay,
                               blocked=error.permanent, now=self.clock())
            return 'paused' if error.pause else ('blocked' if error.permanent else 'retry')
        return 'delivered' if self.outbox.acknowledge(delivery, receipt) else 'lease_lost'


def read_token(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise ValueError('Token file must not be a symlink')
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError('Device token requires a private regular file (mode 600)')
    if path.stat().st_size > 4096:
        raise ValueError('Device token file is too large')
    return path.read_text().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=Path.home()/'rook-local/outbox/events.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    sub.add_parser('retry-blocked')
    send = sub.add_parser('send')
    send.add_argument('--endpoint', required=True)
    send.add_argument('--token-file', type=Path, required=True)
    send.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    outbox = Outbox(args.db)
    if args.command == 'status':
        print(json.dumps(outbox.status(), sort_keys=True))
        return 0
    if args.command == 'retry-blocked':
        print(json.dumps({'requeued': outbox.retry_blocked()}))
        return 0
    sender = HTTPSender(args.endpoint, read_token(args.token_file))
    try:
        worker = Worker(outbox, sender)
        while True:
            result = worker.run_once()
            if result != 'idle':
                logging.info('outbox result=%s', result)
            if result == 'paused':
                return 2
            if args.once:
                return 0 if result in ('delivered', 'idle') else 1
            if result in ('idle', 'retry', 'lease_lost'):
                time.sleep(2)
    finally:
        sender.close()


if __name__ == '__main__':
    raise SystemExit(main())
