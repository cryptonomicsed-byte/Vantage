"""Unit tests for Ed25519 sovereign-identity verification."""
import binascii

from nacl.signing import SigningKey

from backend.identity_verify import verify_identity


def _keypair_and_sig(message: str):
    sk = SigningKey.generate()
    pk_hex = binascii.hexlify(bytes(sk.verify_key)).decode()
    sig_hex = binascii.hexlify(sk.sign(message.encode("utf-8")).signature).decode()
    return pk_hex, sig_hex


def test_valid_signature_verifies():
    agent_id = "agent-deadbeefdeadbeef"
    pk_hex, sig_hex = _keypair_and_sig(agent_id)
    assert verify_identity(pk_hex, agent_id, sig_hex) is True


def test_tampered_message_fails():
    pk_hex, sig_hex = _keypair_and_sig("agent-aaaa")
    assert verify_identity(pk_hex, "agent-bbbb", sig_hex) is False


def test_wrong_key_fails():
    agent_id = "agent-cafef00dcafef00d"
    _, sig_hex = _keypair_and_sig(agent_id)
    other_pk_hex = binascii.hexlify(bytes(SigningKey.generate().verify_key)).decode()
    assert verify_identity(other_pk_hex, agent_id, sig_hex) is False


def test_malformed_inputs_return_false_never_raise():
    assert verify_identity("", "agent-x", "ab") is False
    assert verify_identity("nothex", "agent-x", "nothex") is False
    assert verify_identity("ab", "agent-x", "") is False
    assert verify_identity("zz", "agent-x", "zz") is False
