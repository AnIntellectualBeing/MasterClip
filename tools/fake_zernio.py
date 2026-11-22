"""A tiny stand-in for the Zernio API, for testing the Posting screen without a real account.

.venv\\Scripts\\python -m uvicorn tools.fake_zernio:app --port 8798
then start Clip Studio with CLIPSTUDIO_ZERNIO_API=http://127.0.0.1:8798/api/v1 and CLIPSTUDIO_VAULT=memory.
The good key is "zk_test_key_123456". Posts 'publish' on the second status check.
"""

import itertools

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()
GOOD = "Bearer zk_test_key_123456"
posts: dict[str, dict] = {}
checks: dict[str, int] = {}
automations: list[dict] = []
counter = itertools.count(1)
connected = {"instagram": True, "youtube": False}


def auth(value):
    if value != GOOD:
        raise HTTPException(401, {"error": "Invalid API key"})


@app.get("/api/v1/accounts")
def accounts(authorization: str = Header("")):
    auth(authorization)
    out = []
    if connected["instagram"]:
        out.append({"_id": "ig_1", "platform": "instagram", "username": "clipstudio_demo", "profileId": "prof_1", "isActive": True})
    if connected["youtube"]:
        out.append({"_id": "yt_1", "platform": "youtube", "username": "Clip Studio Demo", "profileId": "prof_1", "isActive": True})
    return {"accounts": out}


@app.get("/api/v1/profiles")
def profiles(authorization: str = Header("")):
    auth(authorization)
    return {"profiles": [{"_id": "prof_1", "name": "Default", "isDefault": True}]}


@app.get("/api/v1/connect/{platform}")
def connect(platform: str, request: Request, authorization: str = Header("")):
    auth(authorization)
    connected[platform] = True  # pretend the buyer approved
    return {"authUrl": f"http://127.0.0.1:8798/fake-auth/{platform}"}


@app.get("/fake-auth/{platform}")
def fake_auth(platform: str):
    return {"ok": f"{platform} connected (fake)"}


@app.post("/api/v1/media/presign")
def presign(authorization: str = Header("")):
    auth(authorization)
    n = next(counter)
    return {"uploadUrl": f"http://127.0.0.1:8798/upload/{n}", "publicUrl": f"https://cdn.example/{n}.mp4"}


@app.put("/upload/{n}")
async def upload(n: int, request: Request):
    await request.body()
    return {}


@app.post("/api/v1/posts")
async def create(request: Request, authorization: str = Header("")):
    auth(authorization)
    body = await request.json()
    pid = f"post_{next(counter)}"
    fail = "FAIL" in body.get("content", "")
    posts[pid] = {"_id": pid, "status": "scheduled", "scheduledFor": body.get("scheduledFor"), "fail": fail,
                  "platforms": [{"platform": p["platform"], "status": "pending"} for p in body["platforms"]]}
    return {"post": posts[pid]}


@app.get("/api/v1/posts/{pid}")
def get(pid: str, authorization: str = Header("")):
    auth(authorization)
    post = posts[pid]
    checks[pid] = checks.get(pid, 0) + 1
    if checks[pid] >= 2 and post["status"] == "scheduled":
        if post["fail"]:
            post["status"] = "failed"
            for entry in post["platforms"]:
                entry.update(status="failed", errorMessage="Instagram said the video is too short (fake)")
        else:
            post["status"] = "published"
            for entry in post["platforms"]:
                entry.update(status="published", platformPostUrl=f"https://{entry['platform']}.example/{pid}", platformPostId=f"9{pid[5:]}")
    return {"post": post}


@app.post("/api/v1/posts/{pid}/retry")
def retry(pid: str, authorization: str = Header("")):
    auth(authorization)
    post = posts[pid]
    post["status"] = "published"
    for entry in post["platforms"]:
        entry.update(status="published", platformPostUrl=f"https://{entry['platform']}.example/{pid}", platformPostId=f"9{pid[5:]}", errorMessage=None)
    return {"post": post}


@app.post("/api/v1/comment-automations")
async def comment_automation(request: Request, authorization: str = Header("")):
    auth(authorization)
    automations.append(await request.json())
    return {"automation": {"_id": f"auto_{len(automations)}"}}


@app.get("/debug")
def debug():
    return {"posts": posts, "automations": automations}
