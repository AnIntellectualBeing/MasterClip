from __future__ import annotations
import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = {"queued", "importing", "transcribing", "analyzing", "rendering", "live"}


class JobStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs = {}
        for path in self.root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                if job["status"] == "live" and job.get("clips"):
                    job.update(
                        status="complete",
                        message="The app closed during the live capture. The clips made so far are saved.",
                    )
                elif job["status"] in ACTIVE:
                    job.update(
                        status="interrupted",
                        message="The application stopped during processing. Import the source again to retry.",
                    )
                self.jobs[job["id"]] = job
            except (ValueError, KeyError, OSError):
                continue

    def folder(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return self.root / job_id

    def create(self, title, **values):
        ident = uuid.uuid4().hex
        job = dict(
            id=ident,
            title=title,
            status="queued",
            progress=0,
            message="Waiting for worker",
            created_at=datetime.now(timezone.utc).isoformat(),
            candidates=[],
            outputs=[],
            warnings=[],
            error=None,
        )
        job.update(values)
        with self.lock:
            self.jobs[ident] = job
            self.folder(ident).mkdir()
            return self.update(ident)

    def update(self, ident, **values):
        with self.lock:
            self.jobs[ident].update(values)
            path = self.folder(ident) / "job.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    self.jobs[ident], ensure_ascii=False, indent=2, allow_nan=False
                ),
                encoding="utf-8",
            )
            tmp.replace(path)
            return copy.deepcopy(self.jobs[ident])

    def get(self, ident):
        with self.lock:
            return copy.deepcopy(self.jobs[ident])

    def all(self):
        with self.lock:
            return sorted(
                copy.deepcopy(list(self.jobs.values())),
                key=lambda j: j["created_at"],
                reverse=True,
            )
