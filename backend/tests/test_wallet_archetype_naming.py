"""_universal_archetype_name translates the bipon39 CLI's raw Ifá/Yoruba
deity names (dominant_macro, macro_distribution entries) to the universal
archetypal names before they can reach a user-facing or agent-facing API
response -- see OSOVM_CODEX.md §9/§27b (Ifá/Yoruba stays internal, public
surfaces use universal names). Regression-pins against the CLI's real
output shape (confirmed live: `bipon39 generate` on hostinger-vps) so an
accidental change here can't silently start leaking deity names again.
"""
from backend.routers.trading import _universal_archetype_name


def test_translates_every_known_archetype_including_diacritics():
    # Exact uppercase/diacritic forms confirmed from a real bipon39 CLI run.
    cases = {
        "ṢÀNGÓ": "Divine Justice",
        "ÒGÚN": "The Forge",
        "ÈṢÙ": "The Messenger",
        "ỌBÀTÁLÁ": "Wisdom",
        "Ọ̀ṢUN": "Memory",
        "YEMỌJA": "Creation",
        "ỌYA": "Flow",
    }
    for raw, universal in cases.items():
        assert _universal_archetype_name(raw) == universal


def test_translation_is_case_and_diacritic_insensitive():
    assert _universal_archetype_name("sango") == "Divine Justice"
    assert _universal_archetype_name("Ogun") == "The Forge"
    assert _universal_archetype_name("esu") == "The Messenger"


def test_unknown_macro_passes_through_unchanged():
    """Fails safe: an unrecognized value is returned as-is rather than
    dropped or raising, so a CLI format change doesn't 500 the endpoint --
    but this only matters for genuinely unknown values, never a known
    deity name (those must always translate, covered above)."""
    assert _universal_archetype_name("SOMETHING_NEW") == "SOMETHING_NEW"


def test_empty_or_none_does_not_raise():
    assert _universal_archetype_name("") == ""
    assert _universal_archetype_name(None) is None
