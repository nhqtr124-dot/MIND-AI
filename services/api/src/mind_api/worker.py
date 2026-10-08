"""Background worker process: ``python -m mind_api.worker``."""

from __future__ import annotations

import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import get_settings
from .db import session_scope
from .jobs import claim, reclaim_expired, run_job, worker_id
from .services import register_handlers
from .services.scheduler import enqueue_due_workflows
from .services.previews import reap_idle_previews

log = logging.getLogger("mind.worker")


def main(concurrency: int = 2) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    register_handlers()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    wid = worker_id()
    poll = get_settings().worker_poll_seconds
    log.info("worker %s started (concurrency=%s)", wid, concurrency)
    last_housekeeping = 0.0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        running: set = set()
        while not stop.is_set():
            running = {f for f in running if not f.done()}
            if time.monotonic() - last_housekeeping > 30:
                last_housekeeping = time.monotonic()
                try:
                    with session_scope() as db:
                        n = reclaim_expired(db)
                        if n:
                            log.warning("requeued %d job(s) with expired leases", n)
                        enqueue_due_workflows(db)
                    reap_idle_previews()
                except Exception:  # noqa: BLE001
                    log.exception("housekeeping failed")
            if len(running) >= concurrency:
                time.sleep(poll)
                continue
            with session_scope() as db:
                job = claim(db, wid)
                jid = job.id if job else None
                kind = job.kind if job else None
            if jid is None:
                stop.wait(poll)
                continue
            log.info("running job %s (%s)", jid, kind)
            running.add(pool.submit(run_job, jid))
    log.info("worker stopped")


if __name__ == "__main__":
    main()
