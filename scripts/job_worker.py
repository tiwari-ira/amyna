"""Durable-jobs worker (task 20/28).

Periodically asks Core to run due jobs (POST /internal/jobs/drain). Runs as a
small standalone process in the prod stack (deploy/docker-compose.prod.yml,
service "jobs"). The DRAIN endpoint is scoped: this worker presents the FETCH
service token. The sweep job includes the 17A unreachable-patient escalation.

Runs forever; exits non-zero only when Core stays unreachable (so the
container supervisor restarts it -- no silent looping on a down Core).
"""

import logging
import os
import sys
import time
import urllib.error
import urllib.request

log = logging.getLogger("claimshield.job_worker")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


def _post(url: str, token: str) -> tuple[bool, int]:
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Amyna-Request-Id": f"req_{os.urandom(8).hex()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return True, response.status or 200
    except urllib.error.HTTPError as exc:
        return (exc.code == 429 or exc.code >= 500), exc.code
    except OSError:
        return True, 0  # network failure: retry next tick


def main() -> int:
    core_base_url = os.environ.get("CORE_BASE_URL", "http://localhost:8080")
    token = os.environ.get("FETCH_SERVICE_TOKEN", "")
    interval = float(os.environ.get("WORKER_INTERVAL_S", "5"))
    if not token:
        log.error("FETCH_SERVICE_TOKEN is required for the job worker")
        return 1
    log.info(
        "job worker: draining %s every %.1fs", f"{core_base_url}/internal/jobs/drain", interval
    )
    consecutive_errors = 0
    while True:
        ok, status = _post(f"{core_base_url}/internal/jobs/drain", token)
        consecutive_errors = 0 if ok else consecutive_errors + 1
        if status:
            log.info("drain tick -> http %s", status)
        if consecutive_errors >= 12:
            log.error("unreachable after %d consecutive ticks; exiting for restart", 12)
            return 2
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
