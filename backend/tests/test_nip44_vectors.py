"""Validates backend/nip44.py against the official nip44.vectors.json test
vectors (the same file rust-nostr's own NIP-44 test suite uses) -- run
manually, not part of CI (vectors file isn't vendored into the repo)."""
import base64
import json
import sys

from coincurve import PrivateKey

sys.path.insert(0, "/tmp/Vantage/backend")
import nip44


def pub_xonly(privkey_hex: str) -> str:
    pk = PrivateKey(bytes.fromhex(privkey_hex))
    return pk.public_key.format(compressed=True)[1:].hex()


def main():
    with open("/private/tmp/claude-501/-Users-bino/bd4320ab-4ed2-45e1-a48e-8c37302f7f3d/scratchpad/nip44.vectors.json") as f:
        vectors = json.load(f)["v2"]

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
