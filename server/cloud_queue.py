"""FIFO admissions and durable per-model budgets for Gemini Free Tier."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
PROVIDER_RPM = 12
PROVIDER_TPM = 200_000
PROVIDER_RPD = 400
MAX_WAITING = 32
MAX_WAITING_BYTES = 8_000_000
MAX_WAIT_SECONDS = 240
PACIFIC = ZoneInfo("America/Los_Angeles")


def provider_day(now):
    return datetime.fromtimestamp(now, PACIFIC).date().toordinal()


def next_provider_day(now):
    pacific = datetime.fromtimestamp(now, PACIFIC)
    midnight = (pacific + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


def candidate(tx, now, estimated_tokens, blocked_models=()):
    day = provider_day(now)
    rows = tx.execute("""SELECT model,count(*),sum(admitted>?),
        sum(CASE WHEN admitted>? THEN input_tokens ELSE 0 END)
        FROM cloud_provider_usage WHERE day=? OR admitted>? GROUP BY model""",
        (now - 60, now - 60, day, now - 60))
    # The rolling window crosses the provider's midnight, but the RPD counter does not.
    daily = dict(tx.execute("SELECT model,count(*) FROM cloud_provider_usage WHERE day=? GROUP BY model", (day,)))
    recent = {model: (rpm, tpm) for model, _total, rpm, tpm in rows}
    cooldowns = dict(tx.execute("SELECT model,until FROM cloud_provider_pause WHERE until>?", (now,)))
    choices = []
    waits = []
    for index, model in enumerate(MODELS):
        if model in blocked_models:
            continue
        count = daily.get(model, 0)
        rpm, tpm = recent.get(model, (0, 0))
        wait = max(0, cooldowns.get(model, 0) - now)
        if count >= PROVIDER_RPD:
            wait = max(wait, next_provider_day(now) - now)
        if rpm >= PROVIDER_RPM or tpm + estimated_tokens > PROVIDER_TPM:
            # Reevaluate shortly when a reservation or measurement may have changed.
            wait = max(wait, 1)
        if wait:
            waits.append(wait)
        else:
            load = max(rpm / PROVIDER_RPM, (tpm + estimated_tokens) / PROVIDER_TPM, count / PROVIDER_RPD)
            choices.append((load, index, model))
    return (min(choices)[2], 0) if choices else (None, min(waits, default=30))


def reserve(tx, attempt, model, now, estimated_tokens):
    day = provider_day(now)
    tx.execute("DELETE FROM cloud_provider_usage WHERE day < ? AND admitted<=?", (day, now - 60))
    tx.execute("INSERT INTO cloud_provider_usage VALUES(?,?,?,?,?)",
               (attempt, model, day, now, estimated_tokens))


def settle(tx, attempt, model, now, measured, retry_after):
    if measured:
        tx.execute("UPDATE cloud_provider_usage SET input_tokens=? WHERE id=?", (measured, attempt))
    if retry_after:
        tx.execute("""INSERT INTO cloud_provider_pause VALUES(?,?)
            ON CONFLICT(model) DO UPDATE SET until=max(until,excluded.until)""",
            (model, now + retry_after))
