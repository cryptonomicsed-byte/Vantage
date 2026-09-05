"""Transports are alternatives, not special cases -- and none of them lies
about what it can do.

The cases worth pinning: an ingress-only backend refusing rather than
silently dropping, a misconfigured default falling back instead of breaking
every post, and nothing claiming a proven round trip it has not made.
"""
import pytest

from backend import channel_transport as ct


def test_the_relay_is_the_only_proven_transport():
    """`proven` means a real round trip has been run. Freenet's has not, and
    the mesh has no egress path to run one over."""
    proven = {i.name for i in ct.available() if i.proven}
    assert proven == {"relay"}


def test_every_registered_transport_reports_both_directions():
    for info in ct.available():
        assert isinstance(info.can_publish, bool)
        assert isinstance(info.can_receive, bool)
        assert info.can_publish or info.can_receive, f"{info.name} does nothing"


@pytest.mark.asyncio
async def test_the_radio_mesh_refuses_to_publish_rather_than_dropping():
    """There is no route from here back to a specific LoRa node. A publish
    that silently goes nowhere is worse than an error."""
    with pytest.raises(ct.TransportNotSupported) as excinfo:
        await ct.get("meshnet").publish(
            principal={}, channel={}, guild_slug="g", content="hi", msg_type="say",
        )
    assert "ingress only" in str(excinfo.value)


def test_not_supported_is_distinguishable_from_merely_down():
    """Retrying a transport that is down may help; retrying one that cannot
    carry this direction never will."""
    assert issubclass(ct.TransportNotSupported, ct.TransportUnavailable)


def test_an_unknown_transport_names_the_ones_that_exist():
    with pytest.raises(ct.TransportUnavailable) as excinfo:
        ct.get("carrier-pigeon")
    assert "relay" in str(excinfo.value)


# ── Freenet ──────────────────────────────────────────────────────────────────

def test_freenet_is_disabled_until_a_bridge_is_configured():
    assert ct.FreenetTransport(bridge_url="").info().configured is False
    assert ct.FreenetTransport(bridge_url="http://localhost:9000").info().configured is True


@pytest.mark.asyncio
async def test_publishing_to_an_unconfigured_freenet_fails_clearly():
    with pytest.raises(ct.TransportUnavailable) as excinfo:
        await ct.FreenetTransport(bridge_url="").publish(
            principal={}, channel={"buzz_channel_id": "c", "slug": "s"},
            guild_slug="g", content="hi", msg_type="say",
        )
    assert "no Freenet bridge" in str(excinfo.value)


def test_a_channels_contract_key_is_derived_not_assigned():
    """The same channel must resolve to the same contract on every instance
    that carries it, or a Freenet-backed channel cannot be federated."""
    transport = ct.FreenetTransport(bridge_url="http://x")
    channel = {"buzz_channel_id": "chan-abc", "id": 1}
    assert transport.contract_key(channel) == transport.contract_key(dict(channel))
    assert transport.contract_key(channel) != transport.contract_key(
        {"buzz_channel_id": "chan-def", "id": 1}
    )
    assert len(transport.contract_key(channel)) == 64


def test_a_channel_with_no_identifier_has_no_contract():
    with pytest.raises(ct.TransportUnavailable):
        ct.FreenetTransport(bridge_url="http://x").contract_key({})


def test_the_bridge_operations_name_the_real_freenet_variants():
    """Checked against freenet-stdlib's ContractRequest, not recalled. If
    the correspondence stops being legible from this side, the bridge
    contract is the thing that quietly rots."""
    assert set(ct.FreenetTransport.OPERATIONS.values()) == {
        "ContractRequest::Put",
        "ContractRequest::Get",
        "ContractRequest::Update",
        "ContractRequest::Subscribe",
    }


# ── the default ──────────────────────────────────────────────────────────────

def test_the_default_transport_is_the_relay(monkeypatch):
    monkeypatch.delenv("VANTAGE_DEFAULT_TRANSPORT", raising=False)
    assert ct.default_name() == "relay"


def test_an_unusable_default_falls_back_rather_than_breaking_every_post(monkeypatch):
    monkeypatch.setenv("VANTAGE_DEFAULT_TRANSPORT", "freenet")  # unconfigured here
    assert ct.default_name() == "relay"


def test_an_entirely_unknown_default_also_falls_back(monkeypatch):
    monkeypatch.setenv("VANTAGE_DEFAULT_TRANSPORT", "nonsense")
    assert ct.default_name() == "relay"


def test_no_transport_mints_an_event_kind():
    """A transport moves the same signed kind-9 event. Giving one its own
    kind would make the message depend on how it travelled."""
    from backend import nostr_kinds

    refused = {spec.name for spec in nostr_kinds.NOT_MINTED}
    assert "freenet_contract_state" in refused
