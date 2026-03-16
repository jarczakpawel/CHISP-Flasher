from __future__ import annotations


def calc_xor_key_seed(seed: bytes, uid_chk: int, chip_id: int) -> bytes:
    if len(seed) < 8:
        raise ValueError('seed too short')
    a = len(seed) // 5
    b = len(seed) // 7
    k0 = seed[b * 4] ^ uid_chk
    k1 = seed[a] ^ uid_chk
    k2 = seed[b] ^ uid_chk
    k3 = seed[b * 6] ^ uid_chk
    k4 = seed[b * 3] ^ uid_chk
    k5 = seed[a * 3] ^ uid_chk
    k6 = seed[b * 5] ^ uid_chk
    k7 = (k0 + (chip_id & 0xFF)) & 0xFF
    return bytes([k0, k1, k2, k3, k4, k5, k6, k7])


def calc_xor_key_uid(uid8: bytes, chip_id: int) -> bytes:
    s = sum(uid8) & 0xFF
    key = [s] * 8
    key[7] = (key[7] + (chip_id & 0xFF)) & 0xFF
    return bytes(key)


def xor_crypt(data: bytes, key8: bytes) -> bytes:
    if len(key8) != 8:
        raise ValueError('xor key must have 8 bytes')
    return bytes((b ^ key8[i & 7]) for i, b in enumerate(data))
