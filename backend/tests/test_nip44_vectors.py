"""Validates backend/nip44.py against the official nip44.vectors.json test
vectors (the same file rust-nostr's own NIP-44 test suite uses).

The vectors file isn't vendored into the repo, so under pytest this skips
unless you point NIP44_VECTORS at a copy. Fetch it from
https://github.com/paulmillr/nip44/blob/main/nip44.vectors.json and run:

    NIP44_VECTORS=/path/to/nip44.vectors.json python -m pytest \\
        backend/tests/test_nip44_vectors.py

It used to `sys.path.insert("/tmp/Vantage/backend")` and `import nip44`,
which resolved on exactly one long-gone machine; everywhere else it raised
at import and, because pytest collects this file by name, took the entire
suite's collection down with it.
"""
import json
import os
from pathlib import Path

import pytest
from coincurve import PrivateKey

from backend import nip44

_VECTORS_PATH = os.environ.get("NIP44_VECTORS", "")


def pub_xonly(privkey_hex: str) -> str:
    pk = PrivateKey(bytes.fromhex(privkey_hex))
    return pk.public_key.format(compressed=True)[1:].hex()


def _load_vectors() -> dict:
    if not _VECTORS_PATH or not Path(_VECTORS_PATH).is_file():
        pytest.skip("set NIP44_VECTORS to the nip44.vectors.json path to run these")
    with open(_VECTORS_PATH) as f:
        return json.load(f)["v2"]


def test_conversation_keys_match_vectors():
    vectors = _load_vectors()
    for v in vectors["valid"]["get_conversation_key"]:
        priv = PrivateKey(bytes.fromhex(v["sec1"]))
        assert nip44.get_conversation_key(priv, v["pub2"]).hex() == v["conversation_key"], v.get("note", "")


def test_padded_lengths_match_vectors():
    vectors = _load_vectors()
    for length, pad in vectors["valid"]["calc_padded_len"]:
        assert nip44._calc_padded_len(length) == pad, f"calc_padded_len({length})"


def test_encrypt_decrypt_round_trips_vectors():
    vectors = _load_vectors()
    for i, v in enumerate(vectors["valid"]["encrypt_decrypt"]):
        priv1 = PrivateKey(bytes.fromhex(v["sec1"]))
        conv_key = bytes.fromhex(v["conversation_key"])
        assert nip44.get_conversation_key(priv1, pub_xonly(v["sec2"])) == conv_key, f"#{i} conversation key"
        assert nip44.encrypt(v["plaintext"], conv_key, bytes.fromhex(v["nonce"])) == v["ciphertext"], f"#{i} encrypt"
        assert nip44.decrypt(v["ciphertext"], conv_key) == v["plaintext"], f"#{i} decrypt"


def test_invalid_ciphertexts_are_rejected():
    vectors = _load_vectors()
    for i, v in enumerate(vectors["invalid"]["decrypt"]):
        with pytest.raises(nip44.Nip44Error):
            nip44.decrypt(v["ciphertext"], bytes.fromhex(v["conversation_key"]))


def main():
    vectors = json.load(open(_VECTORS_PATH))["v2"]

    failures = 0

    for i, v in enumerate(vectors["valid"]["get_conversation_key"]):
        priv = PrivateKey(bytes.fromhex(v["sec1"]))
        got = nip44.get_conversation_key(priv, v["pub2"]).hex()
        if got != v["conversation_key"]:
            print(f"FAIL conversation_key #{i} ({v['note']}): got {got} want {v['conversation_key']}")
            failures += 1
    print(f"conversation_key: {len(vectors['valid']['get_conversation_key'])} vectors checked")

    for length, pad in vectors["valid"]["calc_padded_len"]:
        got = nip44._calc_padded_len(length)
        if got != pad:
            print(f"FAIL calc_padded_len({length}): got {got} want {pad}")
            failures += 1
    print(f"calc_padded_len: {len(vectors['valid']['calc_padded_len'])} vectors checked")

    for i, v in enumerate(vectors["valid"]["encrypt_decrypt"]):
        priv1 = PrivateKey(bytes.fromhex(v["sec1"]))
        pub2 = pub_xonly(v["sec2"])
        conv_key = bytes.fromhex(v["conversation_key"])
        nonce = bytes.fromhex(v["nonce"])

        computed_conv = nip44.get_conversation_key(priv1, pub2)
        if computed_conv != conv_key:
            print(f"FAIL conv key mismatch on encrypt_decrypt #{i}")
            failures += 1
            continue

        ct = nip44.encrypt(v["plaintext"], conv_key, nonce)
        if ct != v["ciphertext"]:
            print(f"FAIL encrypt #{i}: got {ct[:50]}... want {v['ciphertext'][:50]}...")
            failures += 1

        pt = nip44.decrypt(v["ciphertext"], conv_key)
        if pt != v["plaintext"]:
            print(f"FAIL decrypt #{i}: got {pt!r} want {v['plaintext']!r}")
            failures += 1
    print(f"encrypt_decrypt: {len(vectors['valid']['encrypt_decrypt'])} vectors checked")

    for i, v in enumerate(vectors["invalid"]["decrypt"]):
        conv_key = bytes.fromhex(v["conversation_key"])
        try:
            nip44.decrypt(v["ciphertext"], conv_key)
            print(f"FAIL invalid decrypt #{i} ({v['note']}) should have raised")
            failures += 1
        except nip44.Nip44Error:
            pass
    print(f"invalid decrypt: {len(vectors['invalid']['decrypt'])} vectors checked")

    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILURES'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
