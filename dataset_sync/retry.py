import sys
import time

RETRY_DELAYS_SECONDS = (0, 15, 45)


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
