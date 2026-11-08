"""P1-10, Vantage side: the fpconv daemon passes FPCONV_CHAIN to a bridge
that accepts it, and still works with an older bridge that does not."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _daemon():
    spec = importlib.util.spec_from_file_location("fpconv_daemon", ROOT / "daemons" / "fpconv_daemon.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NewBridge:
    def __init__(self, base="", tool_key=None, tool="intel", timeout=20, allow_auto_execute=False, chain=None):
        self.chain = chain


class OldBridge:
    def __init__(self, base="", tool_key=None, tool="intel", timeout=20, allow_auto_execute=False):
        pass


def test_chain_defaults_to_robinhood_and_follows_env(monkeypatch):
    d = _daemon()
    monkeypatch.delenv("FPCONV_CHAIN", raising=False)
    assert d._chain_kwargs(NewBridge) == {"chain": "robinhood"}
    monkeypatch.setenv("FPCONV_CHAIN", "base")
    assert NewBridge(**d._chain_kwargs(NewBridge)).chain == "base"


def test_an_older_bridge_still_constructs(monkeypatch):
    d = _daemon()
    assert d._chain_kwargs(OldBridge) == {}
    OldBridge(base="x", tool_key="k", **d._chain_kwargs(OldBridge))
