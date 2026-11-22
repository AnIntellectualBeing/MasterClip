from types import SimpleNamespace

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from clipstudio import licence  # noqa: E402
from clipstudio.licence import Licence, LicenceError, make_key, read_key  # noqa: E402


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    return private, pub


@pytest.fixture
def vault(monkeypatch):
    store = {}
    monkeypatch.setattr(licence.vault, "get", lambda n: store.get(n))
    monkeypatch.setattr(licence.vault, "put", lambda n, v: store.__setitem__(n, v))
    monkeypatch.setattr(licence.vault, "delete", lambda n: store.pop(n, None))
    return store


def test_key_round_trip_and_typos(keys):
    private, pub = keys
    key = make_key(private, "order_42", seats=2, issued="20261122")
    assert key.startswith("CS1-")
    data = read_key(key.lower().replace("-", " "), pub)  # spaces / case don't matter
    assert data == {"v": 1, "id": "order_42", "s": 2, "t": "20261122"}
    broken = key[:-3] + ("A" if key[-3] != "A" else "B") + key[-2:]
    with pytest.raises(LicenceError):
        read_key(broken, pub)
    other = Ed25519PrivateKey.generate()
    with pytest.raises(LicenceError, match="isn't valid"):
        read_key(make_key(other, "x"), pub)


def test_trial_then_key(keys, vault):
    private, pub = keys
    lic = Licence(pub, api="")
    for _ in range(licence.TRIAL_VIDEOS):
        lic.check_new_video()
        lic.count_new_video()
    assert lic.status()["trial_left"] == 0
    with pytest.raises(LicenceError, match="free videos"):
        lic.check_new_video()
    state = lic.activate(make_key(private, "order_7"))
    assert state["licensed"] and state["order"] == "order_7"
    lic.check_new_video()
    lic.count_new_video()
    assert vault["trial_used"] == str(licence.TRIAL_VIDEOS)  # paid videos don't count
    assert not lic.remove()["licensed"]


class FakeHttp:
    def __init__(self, code, body=None):
        self.code, self.body, self.calls = code, body or {}, []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return SimpleNamespace(status_code=self.code, json=lambda: self.body)


def test_online_seat_limit(keys, vault):
    private, pub = keys
    key = make_key(private, "order_9", seats=2)
    full = FakeHttp(409, {"error": "Used on 2 PCs already."})
    with pytest.raises(LicenceError, match="2 PCs"):
        Licence(pub, api="https://lic.test", http=full).activate(key)
    assert "licence_key" not in vault
    ok = FakeHttp(200)
    assert Licence(pub, api="https://lic.test", http=ok).activate(key)["licensed"]
    url, body = ok.calls[0]
    assert url == "https://lic.test/activate" and body["id"] == "order_9" and len(body["machine"]) == 24


def test_status_tells_the_window_where_to_buy():
    from clipstudio import licence, shop

    state = licence.Licence(api="").status()
    assert state["price"] == shop.PRICE_TEXT == "₹599"
    assert state["buy_url"] == shop.BUY_URL and state["recover_url"] == shop.RECOVER_URL
