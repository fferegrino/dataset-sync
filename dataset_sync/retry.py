import sys
import time

RETRY_DELAYS_SECONDS = (0, 15, 45)
# Kaggle lists 200 files per page and rate limits the calls, so a large dataset needs dozens of them. Retrying the
# page that got throttled, with a longer wait, keeps one 429 from failing the whole listing.
PAGE_RETRY_DELAYS_SECONDS = (0, 30, 60, 120, 240)


def with_retries(action, description, delays=None):
    """Run action, retrying any exception; SystemExit is a deliberate stop and passes straight through."""
    last_error = None
    for attempt, delay in enumerate(delays or RETRY_DELAYS_SECONDS, start=1):
        if delay:
            time.sleep(delay)
        try:
            return action()
        except Exception as exc:
            last_error = exc
            print(f"{description} attempt {attempt} failed: {exc}", file=sys.stderr)
    raise SystemExit(f"{description} failed: {last_error}")
