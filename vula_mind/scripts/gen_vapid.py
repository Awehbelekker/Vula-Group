"""Generate a VAPID key pair for the coach app's Web Push.

    python scripts/gen_vapid.py

Prints two lines to paste into Railway variables:
    VAPID_PRIVATE_KEY=<base64url raw private key>   (secret — never commit or share)
    VAPID_PUBLIC_KEY=<base64url uncompressed public key>   (this is the browsers' applicationServerKey)
Generate ONCE per environment: changing the keys invalidates every existing push subscription.
"""
import base64

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


v = Vapid()
v.generate_keys()
priv = v.private_key.private_numbers().private_value.to_bytes(32, "big")
pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(f"VAPID_PRIVATE_KEY={b64(priv)}")
print(f"VAPID_PUBLIC_KEY={b64(pub)}")
