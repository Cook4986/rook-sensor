"""Fail-closed, single-host accounting for paid auto-label requests.

Policy rates and token ceilings must be verified for the exact provider/models.
This guards Rook's configured estimate, not unrelated account usage or provider
billing errors. The authoritative ledger must live on one designated local host.
"""
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import uuid

LIFETIME_LIMIT = Decimal('50')


class SpendError(RuntimeError):
    pass


class BudgetExhausted(SpendError):
    pass


def amount(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise SpendError('Invalid dollar amount') from exc
    if not result.is_finite() or result < 0:
        raise SpendError('Dollar amounts must be finite and nonnegative')
    return result


def local_path(path):
    path = Path(path).expanduser().resolve()
    if any(part in ('Dropbox', 'CloudStorage') for part in path.parts):
        raise SpendError('Paid accounting must use local disk on the designated labeling host')
    return path


def read_history(path):
    """Legacy deltas and v2 reservations count; damaged history is never skipped."""
    total = Decimal(0)
    records = []
    try:
        with Path(path).open() as source:
            for line in source:
                if not line.endswith('\n'):
                    raise SpendError('Incomplete spend record; reconcile before paid work')
                record = json.loads(line)
                total += amount(record['delta_usd'])
                records.append(record)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SpendError('Spend history missing or invalid; reconcile it before paid work') from exc
    if not records:
        raise SpendError('Empty spend history is not an initialized budget')
    return total, records


def initialize_ledger(legacy, destination):
    """Explicit one-time copy. Never overwrite or reset existing accounting."""
    destination = local_path(destination)
    content = Path(legacy).read_bytes()
    # Validate the exact bytes being copied, including legacy reconciliation entries.
    for line in content.splitlines():
        try:
            record = json.loads(line)
            amount(record['delta_usd'])
        except (ValueError, KeyError, TypeError) as exc:
            raise SpendError('Invalid legacy spend history') from exc
    if not content or not content.endswith(b'\n'):
        raise SpendError('Legacy history must be nonempty and end with a complete line')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())


class SpendGuard(AbstractContextManager):
    def __init__(self, ledger, policy, endpoint, models, ceiling='50'):
        self.path = local_path(ledger)
        self.policy = policy
        self.endpoint = endpoint
        if not isinstance(policy, dict):
            raise SpendError('Spend policy must be a JSON object')
        self.ceiling = amount(ceiling)
        if not 0 < self.ceiling <= LIFETIME_LIMIT:
            raise SpendError('Lifetime ceiling must be positive and no greater than $50')
        if policy.get('endpoint') != endpoint or not endpoint.startswith('https://'):
            raise SpendError('Policy must match the exact HTTPS provider endpoint')
        if not policy.get('verified_at') or not policy.get('source'):
            raise SpendError('Document verified rates and token ceilings before paid work')
        self.rates = {}
        for model in models:
            try:
                spec = policy['models'][model]
                rate_in = amount(spec['input_usd_per_million'])
                rate_out = amount(spec['output_usd_per_million'])
                cap_in, cap_out = spec['max_input_tokens'], spec['max_output_tokens']
            except (KeyError, TypeError) as exc:
                raise SpendError('Missing model-specific pricing/token policy') from exc
            if (not rate_in or not rate_out or type(cap_in) is not int or type(cap_out) is not int
                    or cap_in < 1 or cap_out < 1):
                raise SpendError('Both rates and integer token ceilings must be positive')
            self.rates[model] = (rate_in, rate_out, cap_in, cap_out)
        self.handle = None
        self.total = Decimal(0)
        self.initial_total = Decimal(0)
        self.reservations = {}
        self.attempts = 0
        self.failed = False

    def __enter__(self):
        # Lock the existing ledger inode, not a separately replaceable lock file.
        # Never create an empty ledger: initialization requires historical accounting.
        self.handle = self.path.open('r+')
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.total, records = read_history(self.path)
            if any(r.get('event') == 'bound_violation' for r in records):
                raise SpendError('A provider exceeded its configured bound; reconcile before resuming')
            self.initial_total = self.total
        except BaseException:
            self.handle.close()
            self.handle = None
            raise
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()
            self.handle = None

    def _append(self, record):
        if self.handle is None:
            raise SpendError('Acquire the spend lock before sending requests')
        record = {'ts': datetime.now(timezone.utc).isoformat(), **record}
        self.handle.seek(0, os.SEEK_END)
        self.handle.write(json.dumps(record) + '\n')
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def request_bound(self, model):
        rin, rout, cap_in, cap_out = self.rates[model]
        return (rin * cap_in + rout * cap_out) / Decimal(1000000)

    def reserve(self, model):
        if self.handle is None:
            raise SpendError('Acquire the spend lock before sending requests')
        if self.failed:
            raise SpendError('Accounting violation; paid work remains stopped')
        bound = self.request_bound(model)
        if self.total + bound > self.ceiling:
            raise BudgetExhausted('No room for a full request reservation; no request sent')
        key = uuid.uuid4().hex
        # Durable BEFORE network I/O. Every HTTP attempt, including retries, reserves.
        self._append({'event': 'reserve', 'request_id': key, 'model': model,
                      'delta_usd': str(bound), 'policy': self.policy})
        self.total += bound
        self.reservations[key] = (model, bound)
        self.attempts += 1
        return key

    def observe(self, key, usage):
        model, bound = self.reservations.pop(key)
        rin, rout, cap_in, cap_out = self.rates[model]
        valid = isinstance(usage, dict) and all(
            type(usage.get(k)) is int and usage[k] >= 0
            for k in ('prompt_tokens', 'completion_tokens'))
        # Even successful responses retain their reservation. No automatic refunds;
        # this prevents missing/underreported usage from releasing paid capacity.
        record = {'event': 'usage' if valid else 'usage_unknown', 'request_id': key,
                  'delta_usd': '0'}
        if valid:
            observed = (rin * usage['prompt_tokens'] + rout * usage['completion_tokens']) / Decimal(1000000)
            record.update(usage=usage, observed_usd=str(observed))
            if usage['prompt_tokens'] > cap_in or usage['completion_tokens'] > cap_out:
                self.failed = True
                excess = max(Decimal(0), observed - bound)
                record.update(event='bound_violation', delta_usd=str(excess))
                self._append(record)
                self.total += excess
                raise SpendError('Provider usage exceeded the verified bound; paid work stopped')
        self._append(record)
