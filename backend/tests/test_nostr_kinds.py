"""The registry is only worth having if it fails the build when it drifts.

Two guards here. The first is a plain collision check. The second is the one
that matters: it reads every `KIND_* = <n>` constant out of the backend and
asserts the number is registered -- so a new module that mints a kind
without recording it does not compile past CI, which is exactly the failure
mode `omokoda-mesh/docs/EVENT_KINDS.md` describes as "avoid collisions ad
hoc".
"""
import ast
import pathlib

import pytest

from backend import nostr_kinds as nk

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _kind_constants():
    """Every module-level `KIND_X = <int>` in backend/, by (module, name)."""
    found = {}
    for path in sorted(BACKEND.rglob("*.py")):
        if "tests" in path.parts or path.name == "nostr_kinds.py":
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name) or not target.id.startswith("KIND_"):
                    continue
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                    found[(path.relative_to(BACKEND).as_posix(), target.id)] = node.value.value
    return found


def test_no_number_is_registered_twice():
    """Importing the module already raises on a collision; this states it as
    a test so the reason is legible when it fires."""
    numbers = [spec.kind for spec in nk.REGISTRY.values()]
    assert len(numbers) == len(set(numbers))


def test_every_kind_used_in_the_backend_is_registered():
    unregistered = {
        where: number
        for where, number in _kind_constants().items()
        if number not in nk.REGISTRY
    }
    assert not unregistered, (
        "these event kinds are used but not in backend/nostr_kinds.py -- add them "
        f"there (with an authority and a reason) rather than leaving them ad hoc: {unregistered}"
    )


def test_the_registry_is_not_a_dead_list():
    """It should cover the kinds the coordination layer actually publishes."""
    from backend import coordination

    assert nk.kind("message") == coordination.KIND_MESSAGE
    assert nk.kind("create_channel") == coordination.KIND_CREATE_CHANNEL


def test_lookup_by_name_refuses_to_guess():
    with pytest.raises(KeyError):
        nk.kind("no-such-kind")


def test_ownership_is_recorded_honestly():
    """Reusing someone else's kind means we do not get to change its shape."""
    assert not nk.is_ours(nk.kind("attestation")), "1902 belongs to ip-layer"
    assert not nk.is_ours(nk.kind("message")), "9 belongs to NIP-29"
    assert nk.is_ours(nk.kind("persona")), "30175 is ours"


def test_mesh_kinds_are_marked_provisional():
    """Their own repo says the numbers may still move. If this registry
    claimed they were locked, it would be lying on their behalf."""
    for name in ("mesh_packet_route", "mesh_telemetry", "mesh_node_presence"):
        assert not nk.BY_NAME[name].locked


def test_refused_kinds_say_what_to_use_instead():
    assert nk.NOT_MINTED
    for spec in nk.NOT_MINTED:
        assert spec.instead_of, f"{spec.name} was refused without saying what replaces it"
