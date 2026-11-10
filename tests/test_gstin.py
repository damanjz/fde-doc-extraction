import pytest

from app import gstin
from data.common import BUYER_GSTIN, COURIERS, SUPPLIERS

# Publicly cited GSTINs whose checksum is valid (they confirm the algorithm, not just our own generator).
KNOWN_VALID = ["27AAPFU0939F1ZV", "27AAACR5055K1Z7", "29AAGCB7383J1Z4", "07AAGFF2194N1Z1", "24AAACC1206D1ZM"]


@pytest.mark.parametrize("g", KNOWN_VALID)
def test_known_valid(g):
    assert gstin.validate(g) == (True, "")


def test_check_char_matches_last_char():
    for g in KNOWN_VALID:
        assert gstin.check_char(g[:14]) == g[14]


@pytest.mark.parametrize("g", KNOWN_VALID)
def test_every_single_character_change_is_detected(g):
    for i in range(15):
        for ch in gstin.CHARSET:
            if ch == g[i]:
                continue
            bad = g[:i] + ch + g[i + 1 :]
            assert gstin.validate(bad)[0] is False, (bad, i)


def test_checksum_mismatch_reason():
    ok, why = gstin.validate("27AAPFU0939F1ZX")
    assert not ok and "checksum" in why


def test_length_and_format():
    assert gstin.validate("27AAPFU0939F1Z")[1].startswith("length")
    assert "format" in gstin.validate("27AAPFU0939F1AV")[1]  # 14th char must be Z
    assert "format" in gstin.validate("ZZAAPFU0939F1ZV")[1]
    assert "format" in gstin.validate("27AAPF10939F1ZV")[1]  # PAN letters


def test_unknown_state_code():
    g = gstin.make("99", "AAPFU0939F")
    assert gstin.validate(g)[0]  # 99 is valid
    g = gstin.make("50", "AAPFU0939F")
    assert gstin.validate(g) == (False, "unknown state code 50")


def test_missing_and_normalisation():
    assert gstin.validate(None) == (False, "missing")
    assert gstin.validate("") == (False, "missing")
    assert gstin.validate(" 27aapfu0939f1zv ")[0]
    assert gstin.validate("27 AAPFU 0939 F1ZV")[0]


def test_make_roundtrip_and_state():
    g = gstin.make("36", "AAKCL4417Q")
    assert gstin.validate(g)[0] and gstin.state_code(g) == "36"


def test_all_generator_entities_have_valid_gstins():
    for s in SUPPLIERS:
        assert gstin.validate(s.gstin)[0], s.name
    assert gstin.validate(BUYER_GSTIN)[0]
    assert all(gstin.validate(c.gstin)[0] for c in COURIERS)
