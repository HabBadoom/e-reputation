"""Browser worker process: `python -m app.browser.worker`.

Fleet Manager / Worker Registry: the worker registers itself and heartbeats its capacity.
Worker Scheduler: claims missions through `browser_claim_missions` (priority, free contexts,
per-domain concurrency and spacing, cool-downs, per-tenant concurrency).
Resource Monitor: takes no new mission when memory is low.
Health / Cleanup: relaunches a crashed browser, recycles it periodically, verifies each
context is destroyed; `browser_recover` re-queues missions of silent workers and expires late ones.
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.browser import guard
from app.browser.outcome import MissionSpec, finish_mission, start_mission
from app.browser.runtime import BrowserRuntime, VisitResult
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import session_scope

log = logging.getLogger("repintel.browser")
VERSION = "1.0"


def free_memory_mb() -> int | None:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


class BrowserWorker:
    def __init__(self, worker_id: uuid.UUID | None = None) -> None:
        s = get_settings()
        self.id = worker_id or uuid.uuid4()
        self.name = s.browser_worker_name or socket.gethostname()
        self.runtime = BrowserRuntime()
        self.active: dict[uuid.UUID, asyncio.Task[Any]] = {}
        self.done = 0
        self.status = "online"

    # --------------------------------------------------------------- registry & health
    def capacity(self) -> int:
        s = get_settings()
        mem = free_memory_mb()
        if mem is not None and mem < s.browser_min_free_memory_mb:
            return 0
        return max(0, s.browser_worker_max_contexts - len(self.active))

    def metrics(self) -> dict[str, Any]:
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
        return {"free_memory_mb": free_memory_mb(), "load_1m": round(load[0], 2),
                "missions_done": self.done, "browser_restarts": self.runtime.restarts,
                "visits": self.runtime.visits}

    def _heartbeat(self) -> None:
        s = get_settings()
        with session_scope() as db:
            db.execute(text("SELECT browser_worker_heartbeat(:i, :n, :h, :v, :m, :a, "
                            "CAST(:x AS jsonb), :st)"),
                       {"i": self.id, "n": self.name, "h": socket.gethostname(), "v": VERSION,
                        "m": s.browser_worker_max_contexts, "a": len(self.active),
                        "x": json.dumps(self.metrics()), "st": self.status})
            if self.active:
                db.execute(text("SELECT browser_mission_heartbeat(:w, CAST(:ids AS uuid[]))"),
                           {"w": self.id, "ids": [str(i) for i in self.active]})
            rec = db.execute(text("SELECT * FROM browser_recover(:s)"),
                             {"s": s.browser_stale_seconds}).one()
            if any(rec):
                log.info("browser_recovered", extra=dict(rec._mapping))

    def _claim(self, n: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
        if n <= 0:
            return []
        with session_scope() as db:
            rows = db.execute(text("SELECT * FROM browser_claim_missions(:w, :n, :t)"),
                              {"w": self.id, "n": n,
                               "t": get_settings().browser_tenant_concurrency}).all()
        return [(r.mission_id, r.mission_tenant) for r in rows]

    # -------------------------------------------------------------------- missions
    async def _run(self, mission_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        try:
            spec: MissionSpec | None = await asyncio.to_thread(
                start_mission, mission_id, tenant_id, self.id)
            if spec is None:
                return
            try:
                res = await asyncio.wait_for(
                    self.runtime.visit(spec.url, timeout_seconds=spec.timeout_seconds,
                                       block_resources=spec.block_resources,
                                       allow=guard.url_allowed),
                    timeout=spec.timeout_seconds + 30)
            except TimeoutError:
                res = VisitResult(url=spec.url, error="timeout")
            status = await asyncio.to_thread(finish_mission, spec, self.id, res)
            log.info("browser_mission_done", extra={"mission_id": str(mission_id),
                                                    "status": status, **res.summary()})
        except Exception:
            # Left ASSIGNED/RUNNING: browser_recover re-queues it once heartbeats stop.
            log.exception("browser_mission_crashed", extra={"mission_id": str(mission_id)})
        finally:
            self.done += 1
            self.active.pop(mission_id, None)

    async def tick(self) -> int:
        await self.runtime.ensure_healthy()
        await asyncio.to_thread(self._heartbeat)
        if self.status != "online":
            return 0
        claimed = await asyncio.to_thread(self._claim, self.capacity())
        for mid, tid in claimed:
            self.active[mid] = asyncio.create_task(self._run(mid, tid))
        return len(claimed)

    async def run_once(self) -> int:
        """One scheduling round, waiting for the missions it started (tests, cron)."""
        if self.runtime.browser is None:
            await self.runtime.start()
        n = await self.tick()
        if self.active:
            await asyncio.gather(*self.active.values(), return_exceptions=True)
        return n

    async def run_forever(self) -> None:
        s = get_settings()
        await self.runtime.start()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        log.info("browser_worker_started", extra={"worker_id": str(self.id),
                                                  "max_contexts": s.browser_worker_max_contexts})
        last_beat = datetime.min.replace(tzinfo=UTC)
        while not stop.is_set():
            try:
                now = datetime.now(UTC)
                if (now - last_beat).total_seconds() >= s.browser_heartbeat_seconds or \
                        self.capacity() > 0:
                    await self.tick()
                    last_beat = now
            except Exception:
                log.exception("browser_tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=s.browser_poll_seconds)
        # Graceful drain: finish what is running, take nothing new.
        self.status = "draining"
        await asyncio.to_thread(self._heartbeat)
        if self.active:
            await asyncio.gather(*self.active.values(), return_exceptions=True)
        self.status = "offline"
        await asyncio.to_thread(self._heartbeat)
        await self.runtime.stop()


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(BrowserWorker().run_forever())


if __name__ == "__main__":
    main()
