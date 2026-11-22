"""Where buyers pay and where keys are checked. Public values (no secrets), baked into Setup.exe.

Fill LICENCE_API once the licence worker (server/licence-worker) is deployed, then rebuild Setup.exe:
  - the licence window gets a "Buy for Rs 599" button -> LICENCE_API/buy (Razorpay)
  - "Lost your key?" -> LICENCE_API/buy/key
  - keys are limited to 2 PCs (activate/deactivate)
Empty = no Buy button, keys work offline with no PC limit (what ships today).
Env vars CLIPSTUDIO_LICENCE_API / CLIPSTUDIO_BUY_URL override these for QA.
"""

import os

# Clip Studio is free and open source (owner, 22 Nov 2026): no trial limit, no licence window.
# The licence code stays so a paid edition can be switched back on with CLIPSTUDIO_PAID=1.


def free() -> bool:
    return os.environ.get("CLIPSTUDIO_PAID", "") != "1"


LICENCE_API = ""  # e.g. "https://clipstudio-licence.<account>.workers.dev"
BUY_URL = ""  # leave empty to use LICENCE_API + "/buy"; set to sell somewhere else (e.g. a Gumroad page)
PRICE_TEXT = "₹599"

LICENCE_API = os.environ.get("CLIPSTUDIO_LICENCE_API", LICENCE_API).rstrip("/")
BUY_URL = os.environ.get("CLIPSTUDIO_BUY_URL", BUY_URL) or (f"{LICENCE_API}/buy" if LICENCE_API else "")
RECOVER_URL = f"{LICENCE_API}/buy/key" if LICENCE_API else ""
