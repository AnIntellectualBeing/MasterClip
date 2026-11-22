"""Add Twitch chat replay to the moment test cases (moment_eval/cases/*.json): one list of
[seconds into the case, message] per case. Only the VOD id is sent to Twitch.

python tools/add_case_chat.py [case id ...]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipstudio.twitch import vod_chat  # noqa: E402

for path in sorted((ROOT / "moment_eval" / "cases").glob("*.json")):
    case = json.loads(path.read_text(encoding="utf-8"))
    if sys.argv[1:] and case["id"] not in sys.argv[1:]:
        continue
    src = case["source"]
    chat = vod_chat(src["vod"], src["vod_start"], src["vod_end"], deadline=None)
    case["chat"] = [[round(t - src["vod_start"], 1), text] for t, text in chat]
    path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    print(case["id"], len(chat), "chat messages")
