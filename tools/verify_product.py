"""Run full media QA against the new product server, using private local fixtures."""

import json
import argparse
import subprocess
import time
from pathlib import Path
import httpx

root = Path(__file__).resolve().parents[1]
base = "http://127.0.0.1:8766"
out = root / "artifacts"
long_id = (out / "product-qa-job-id.txt").read_text().strip()
parser = argparse.ArgumentParser()
parser.add_argument("--reuse-vertical", action="store_true")
args = parser.parse_args()


def wait(client, ident):
    started = time.monotonic()
    last = None
    while time.monotonic() - started < 1200:
        job = client.get(base + "/api/jobs/" + ident).json()
        if job["message"] != last:
            print(ident[:6], job["message"], flush=True)
            last = job["message"]
        if job["status"] == "complete":
            return job
        if job["status"] in ("failed", "cancelled", "interrupted"):
            raise RuntimeError(job.get("error") or job["message"])
        time.sleep(3)
    raise TimeoutError("Product QA timed out")


with httpx.Client(timeout=30) as client:
    response = client.post(
        base + "/api/jobs/" + long_id + "/export",
        json={"mode": "highlights", "target_seconds": 210, "burn_captions": True},
    )
    response.raise_for_status()
    long_job = wait(client, long_id)
    if args.reuse_vertical:
        short_id = (out / "vertical-qa-job-id.txt").read_text().strip()
        response = client.post(
            base + "/api/jobs/" + short_id + "/export",
            json={
                "mode": "shorts",
                "layout": "center",
                "burn_captions": True,
                "short_count": 3,
            },
        )
        response.raise_for_status()
    else:
        response = client.post(
            base + "/api/projects",
            json={
                "source_type": "path",
                "source": str(out / "caption-qa/gameplay-30s.mp4"),
                "format": "vertical",
                "captions": True,
                "language": "en",
                "model": "base",
            },
        )
        response.raise_for_status()
        short_id = response.json()["id"]
    short_job = wait(client, short_id)
    observations = []
    for job, kind, width, height, target in [
        (long_job, "highlights", 1920, 1080, 210),
        (short_job, "short", 1080, 1920, 30),
    ]:
        revision = job["export_revision"]
        video = next(
            o
            for o in reversed(job["outputs"])
            if o["kind"] == kind and f"-{revision:02}" in o["name"]
        )
        path = root / "data" / job["id"] / video["name"]
        metadata = json.loads(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )
        stream = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        assert (stream["width"], stream["height"]) == (width, height)
        assert abs(float(metadata["format"]["duration"]) - target) < 0.15
        assert video["captions"] is True and path.with_suffix(".srt").is_file()
        decoded = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert decoded.returncode == 0 and not decoded.stderr, decoded.stderr
        transcript = json.loads(
            (path.parent / "transcript.json").read_text(encoding="utf-8")
        )
        words = sum(len(c.get("words", [])) for c in transcript)
        assert words > 0
        with client.stream(
            "GET", base + video["url"], headers={"Range": "bytes=0-1023"}
        ) as ranged:
            assert ranged.status_code == 206
        observations.append(
            dict(
                job=job["id"],
                name=video["name"],
                kind=kind,
                width=width,
                height=height,
                duration=float(metadata["format"]["duration"]),
                captions=True,
                timed_words=words,
                full_decode="passed",
                range_download="passed",
            )
        )
    report = dict(passed=True, videos=observations)
    (out / "product-media-check.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (out / "vertical-qa-job-id.txt").write_text(short_id, encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
