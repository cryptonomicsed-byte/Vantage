"""coincurve stand-in for hosts where the C extension cannot build (Python 3.13
cffi metadata break on Termux/arm64). Pure-python secp256k1 with the two
surfaces Vantage's guild path actually uses:

  PrivateKey(32 bytes)          -> .public_key (with .format(compressed=True))
                                   .sign_schnorr(msg32)  [BIP-340, aux=zeros]
  PublicKeyXOnly(32 x-bytes)    -> .verify(sig64, msg32) [BIP-340]

This is REAL BIP-340/Schnorr — no shortcuts, stdlib only — not a stub that
agrees with itself. It exists so coordination/guild modules import on dev
hosts; anything whose result flows into production crypto must still be
cross-verified against the real coincurve on the deployment box.
"""
import hashlib
import hmac

# secp256k1 domain
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


def _inv(a, m):
    return pow(a, m - 2, m)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1) * _inv(2 * y1, P) % P
    else:
        lam = (y2 - y1) * _inv((x2 - x1) % P, P) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return x3, y3


def _mul(p, k):
    r = None
    while k:
        if k & 1:
            r = _add(r, p)
        p = _add(p, p)
        k >>= 1
    return r


def _lift_x(x):
    if x >= P:
        return None
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if (y * y) % P != y_sq:
        return None
    return x, y if y % 2 == 0 else P - y


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


class PublicKey:
    def __init__(self, point):
        self._point = point

    def format(self, compressed=True):
        if not compressed:
            x, y = self._point
            return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")
        x, y = self._point
        return (b"\x02" if y % 2 == 0 else b"\x03") + x.to_bytes(32, "big")


class PublicKeyXOnly:
    def __init__(self, data):
        if isinstance(data, PublicKeyXOnly):
            self._x = data._x
            return
        if len(data) != 32:
            raise ValueError("x-only public keys are 32 bytes")
        self._x = int.from_bytes(bytes(data), "big")

    def format(self):
        return self._x.to_bytes(32, "big")

    def verify(self, sig, message):
        if len(sig) != 64:
            return False
        r = int.from_bytes(sig[:32], "big")
        s = int.from_bytes(sig[32:], "big")
        if r >= P or s >= N:
            return False
        p = _lift_x(self._x)
        if p is None:
            return False
        e = int.from_bytes(_tagged_hash("BIP0340/challenge", sig[:32] + self.format() + bytes(message)), "big") % N
        rp = _add(_mul((Gx, Gy), s), _mul((p[0], P - p[1]), e))
        if rp is None or rp[1] % 2 == 1 or rp[0] != r:
            return False
        return True


class PrivateKey:
    def __init__(self, secret=None):
        if secret is None:
            secret = int.from_bytes(hashlib.sha256(b"shim-default").digest(), "big") % N or 1
        if isinstance(secret, int):
            d = secret % N
        else:
            d = int.from_bytes(bytes(secret), "big") % N
        if d == 0:
            raise ValueError("private key must be non-zero")
        self._d = d
        self.public_key = PublicKey(_mul((Gx, Gy), d))

    def public_key_xonly(self):
        return PublicKeyXOnly(self.public_key.format(compressed=True)[1:])

    def sign_schnorr(self, message, aux_randomness=b"\x00" * 32):
        d = self._d
        x = self.public_key.format(compressed=True)[1:]
        p = _mul((Gx, Gy), d)
        if p[1] % 2 == 1:
            d = N - d
        t = (d ^ int.from_bytes(_tagged_hash("BIP0340/aux", aux_randomness), "big")) % N
        rand = _tagged_hash("BIP0340/nonce", t.to_bytes(32, "big") + x + bytes(message))
        k = int.from_bytes(rand, "big") % N
        if k == 0:
            raise ValueError("nonce is zero")
        rp = _mul((Gx, Gy), k)
        if rp[1] % 2 == 1:
            k = N - k
        rx = rp[0].to_bytes(32, "big")
        e = int.from_bytes(_tagged_hash("BIP0340/challenge", rx + x + bytes(message)), "big") % N
        s = (k + e * d) % N
        return rx + s.to_bytes(32, "big")

    def sign(self, message, hasher=hashlib.sha256, custom_nonce=None):
        # ECDSA signature (RFC 6979-style deterministic k) — present for API
        # compatibility; the guild path uses sign_schnorr.
        z = int.from_bytes(hasher(bytes(message)).digest(), "big") % N
        k = (int.from_bytes(hashlib.sha256(bytes(message) + self._d.to_bytes(32, "big")).digest(), "big") % (N - 1)) + 1
        rp = _mul((Gx, Gy), k)
        r = rp[0] % N
        s = (_inv(k, N) * (z + r * self._d)) % N
        half = N // 2
        if s > half:
            s = N - s
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")
