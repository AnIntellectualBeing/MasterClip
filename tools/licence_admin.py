"""Seller tool for Clip Studio licence keys. The private key never goes in this repo or the app.

  python tools/licence_admin.py keygen --private "D:\\somewhere-safe\\clipstudio-private.pem"
      -> writes the private key (refuses to overwrite or to write inside this repo)
      -> prints the PUBLIC key hex: paste it into clipstudio/licence.py PUBLIC_KEY, then rebuild

  python tools/licence_admin.py issue --private "...pem" --order RZP_ORDER_123 [--seats 2]
      -> prints a key like CS1-ABCDE-...   (email it to the buyer)

  python tools/licence_admin.py check "CS1-..."    -> shows what's inside a key (uses the app's public key)
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from clipstudio.licence import make_key, read_key  # noqa: E402


def load_private(path: str) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(Path(path).read_bytes(), password=None)


def public_hex(private: Ed25519PrivateKey) -> str:
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--private", required=True)
    i = sub.add_parser("issue")
    i.add_argument("--private", required=True)
    i.add_argument("--order", required=True)
    i.add_argument("--seats", type=int, default=2)
    c = sub.add_parser("check")
    c.add_argument("key")
    args = parser.parse_args()

    if args.cmd == "keygen":
        target = Path(args.private).resolve()
        if ROOT in target.parents:
            sys.exit("Put the private key OUTSIDE the project folder, somewhere you back up.")
        if target.exists():
            sys.exit(f"{target} already exists. Keys issued with it would stop working if you replaced it.")
        target.parent.mkdir(parents=True, exist_ok=True)
        private = Ed25519PrivateKey.generate()
        target.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                 serialization.NoEncryption()))
        print("Private key saved to", target, "(back it up; never share it)")
        print("PUBLIC_KEY =", public_hex(private))
    elif args.cmd == "issue":
        print(make_key(load_private(args.private), args.order, args.seats))
    else:
        print(read_key(args.key))


if __name__ == "__main__":
    main()
