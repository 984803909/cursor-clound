"""Dependency-free Ed25519 *verification* (RFC 8032 reference arithmetic).

Why this exists: the desktop client only ever needs to **verify** signatures, and
shipping `cryptography` / `PyNaCl` into a Nuitka build drags in native DLLs. This
module is pure Python + hashlib, so it compiles cleanly and adds no runtime
dependency. A verification takes roughly 30-80 ms, which is irrelevant for a
once-per-launch licence check.

Do not use this module for signing - it deliberately implements verification only.
"""

from __future__ import annotations

import hashlib

# Field modulus and group order for edwards25519.
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493


def _modp_inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _modp_inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

# Extended homogeneous coordinates: (X, Y, Z, T) with x = X/Z, y = Y/Z, xy = T/Z.
Point = tuple[int, int, int, int]


def _point_add(p: Point, q: Point) -> Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    dd = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(scalar: int, point: Point) -> Point:
    result: Point = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(p: Point, q: Point) -> bool:
    if (p[0] * q[2] - q[0] * p[2]) % _P != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _modp_inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_G_Y = 4 * _modp_inv(5) % _P
_G_X = _recover_x(_G_Y, 0)
assert _G_X is not None
_G: Point = (_G_X, _G_Y, 1, _G_X * _G_Y % _P)


def _point_decompress(data: bytes) -> Point | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Return True when ``signature`` is a valid Ed25519 signature.

    Never raises for malformed input - a bad key/signature simply returns False.
    """
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        point_a = _point_decompress(public_key)
        if point_a is None:
            return False
        r_bytes = signature[:32]
        point_r = _point_decompress(r_bytes)
        if point_r is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        h = int.from_bytes(
            hashlib.sha512(r_bytes + public_key + message).digest(), "little"
        ) % _L
        return _point_equal(_point_mul(s, _G), _point_add(point_r, _point_mul(h, point_a)))
    except Exception:  # noqa: BLE001 - verification must never explode the caller
        return False


__all__ = ["verify"]
