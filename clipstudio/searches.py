"""Bounded, cancellable discovery jobs for the product UI."""

from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import time
import uuid


class Searches:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.events = {}
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="stream-search"
        )

    def create(self, topic, count):
        with self.lock:
            now = time.monotonic()
            for ident, job in list(self.jobs.items()):
                if now - job["_created"] > 600:
                    if job["status"] == "searching":
                        self.events[ident].set()
                    del self.jobs[ident]
                    self.events.pop(ident, None)
            for job in self.jobs.values():
                if (
                    job["topic"].casefold() == topic.casefold()
                    and job["_count"] == count
                    and job["status"] in ("searching", "complete")
                ):
                    return self.public(job)
            if len(self.jobs) >= 40:
                raise ValueError("Too many recent searches. Try again shortly.")
            ident = uuid.uuid4().hex
            job = dict(
                id=ident,
                topic=topic,
                status="searching",
                progress=0,
                message="Searching live streams and recent VODs",
                results=[],
                warnings=[],
                error=None,
                _created=now,
                _count=count,
            )
            self.jobs[ident] = job
            self.events[ident] = threading.Event()
            self.pool.submit(self.run, ident)
            return self.public(job)

    def public(self, job):
        return copy.deepcopy({k: v for k, v in job.items() if not k.startswith("_")})

    def get(self, ident):
        with self.lock:
            return self.public(self.jobs[ident])

    def update(self, ident, **values):
        with self.lock:
            if ident in self.jobs:
                self.jobs[ident].update(values)

    def run(self, ident):
        with self.lock:
            if ident not in self.jobs:
                return
            job = dict(self.jobs[ident])
            event = self.events[ident]
        try:
            from .discovery import find_streams

            result = find_streams(
                job["topic"],
                max_results=job["_count"],
                cancel_event=event,
                progress=lambda p, m: self.update(
                    ident, progress=max(0, min(1, p)), message=m
                ),
            )
            if event.is_set():
                self.update(ident, status="cancelled", message="Search cancelled")
                return
            self.update(
                ident,
                status="complete",
                progress=1,
                results=result["results"],
                warnings=result.get("warnings", []),
                provider=result.get("provider"),
                message="Pick a stream"
                if result["results"]
                else "Nothing found. Try another search or paste a link.",
            )
        except Exception as exc:
            self.update(
                ident,
                status="cancelled" if event.is_set() else "failed",
                message="Search cancelled"
                if event.is_set()
                else "Could not search streams",
                error=None if event.is_set() else str(exc)[:400],
            )

    def cancel(self, ident):
        with self.lock:
            if ident not in self.jobs:
                raise KeyError(ident)
            self.events[ident].set()
            if self.jobs[ident]["status"] == "searching":
                self.jobs[ident].update(status="cancelled", message="Search cancelled")
            return self.public(self.jobs[ident])
