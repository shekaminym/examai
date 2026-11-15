"""Daily USD admission budget with durable reservations and provider token usage."""

import os
from decimal import Decimal, ROUND_CEILING


class CloudBudget:
    def __init__(self):
        self.cap = int(os.getenv("EXAMAI_CLOUD_DAILY_BUDGET_MICROUSD", "0"))
        self.reservation = int(os.getenv("EXAMAI_CLOUD_RESERVE_MICROUSD", "0"))
        self.input_rate = Decimal(os.getenv("EXAMAI_CLOUD_INPUT_USD_PER_MILLION", "0"))
        self.output_rate = Decimal(os.getenv("EXAMAI_CLOUD_OUTPUT_USD_PER_MILLION", "0"))
        if not (0 < self.reservation <= self.cap and
                all(rate.is_finite() and rate > 0 for rate in (self.input_rate, self.output_rate))):
            raise ValueError("Configure positive Cloud reservation, daily budget and token rates")

    def reserve(self, tx, request_id, day):
        reserved = tx.execute("SELECT coalesce(sum(micro_usd),0) FROM cloud_charge WHERE day=?",
                              (day,)).fetchone()[0]
        if reserved + self.reservation > self.cap:
            return False
        tx.execute("INSERT INTO cloud_charge(id,day,micro_usd) VALUES (?,?,?)",
                   (request_id, day, self.reservation))
        return True

    def settle(self, tx, request_id, input_tokens, output_tokens, complete, metered):
        # USD per million tokens is numerically equal to micro-USD per token.
        actual = int((self.input_rate * input_tokens + self.output_rate * output_tokens)
                     .to_integral_value(rounding=ROUND_CEILING))
        charge = actual if complete and metered else max(actual, self.reservation)
        tx.execute("UPDATE cloud_charge SET input_tokens=?,output_tokens=?,micro_usd=? WHERE id=?",
                   (input_tokens, output_tokens, charge, request_id))
