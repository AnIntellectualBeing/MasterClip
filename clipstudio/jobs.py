from __future__ import annotations
import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = {"queued", "importing", "transcribing", "analyzing", "rendering", "live"}


class JobStore:
    def __init__(self, root, work_root=None, extra_roots=()):
        # root keeps app-wide files (templates, posting); job folders (big downloads and renders)
        # go in work_root, which the buyer can move to another drive.
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.work_root = Path(work_root) if work_root else self.root
        try:
            self.work_root.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.work_root = self.root  # the chosen drive is unplugged: keep working on the default
        self.lock = threading.RLock()
        self.jobs = {}
        self.paths = {}
        bases = dict.fromkeys([self.root, self.work_root, *(Path(p) for p in extra_roots)])
        for path in (p for base in bases for p in base.glob("*/job.json")):
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
                self.paths[job["id"]] = path.parent
            except (ValueError, KeyError, OSError):
                continue

    def folder(self, job_id):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return self.paths.get(job_id) or self.root / job_id

    def set_work_root(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.work_root = folder

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
            self.paths[ident] = self.work_root / ident
            self.folder(ident).mkdir(parents=True)
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
