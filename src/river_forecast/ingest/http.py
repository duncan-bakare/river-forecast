import time
import urllib.error
import urllib.request

from river_forecast.config import USER_AGENT


def fetch(url, retries=3, timeout=30, backoff=2.0):
    """GET url and return bytes, retrying transient failures."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            # 4xx won't get better by retrying.
            if 400 <= e.code < 500:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        if attempt < retries - 1:
            time.sleep(backoff * (attempt + 1))
    raise last
