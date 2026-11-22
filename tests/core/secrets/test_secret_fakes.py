"""Stand-ins: random, the real value's shape, and nothing of the real value
but its public prefix."""
import re
import zlib

import pytest

from app import secret_fakes

from ._values import ANTHROPIC, CUSTOM, GITHUB, HEX, JWT, NUMBER, OPENAI, PASSWORD, PEM, SLASHY, STRIPE, WORDS


def classes(text):
    return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d", "0", text)))


def test_a_stand_in_keeps_the_shape_and_the_public_prefix_only():
    for real in (OPENAI, ANTHROPIC, STRIPE):
        kind, prefix = secret_fakes.match_format(real)
        fake = secret_fakes.make_fake(real, kind, prefix)
        assert fake != real and len(fake) == len(real)
        assert fake[:prefix] == real[:prefix]
        assert classes(fake) == classes(real)
        # No run of the random part survives, the tail least of all.
        assert fake[-4:] != real[-4:]
        assert not any(real[i:i + 8] in fake for i in range(prefix, len(real) - 8))


def test_punctuation_stays_in_place_so_quoting_fits_both():
    fake = secret_fakes.make_fake(PASSWORD)
    assert [c for c in fake if not c.isalnum()] == [c for c in PASSWORD if not c.isalnum()]
    assert [i for i, c in enumerate(fake) if not c.isalnum()] == [i for i, c in enumerate(PASSWORD) if not c.isalnum()]


@pytest.mark.parametrize("real", [
    CUSTOM, SLASHY, WORDS, "mt_live_" + "9F2C4E7A1B3D5F60ZQ", "v2.local." + "Qx7Lm2Vn9Rt4Wp8Zk3Jh", "base64:" + "Qx7Lm2Vn9Rt4Wp8Zk3Jh6Fd1Sb5Yc0Ga=",
])
def test_a_value_in_an_apps_own_format_gets_a_stand_in_of_that_format(real):
    """No provider's format is needed: every separator stays where it is and
    each letter or digit is redrawn as a lower-case letter, a capital or a
    digit, so whatever checks the shape of the real value accepts the stand-in."""
    for _ in range(20):
        fake = secret_fakes.make_fake(real)
        assert fake != real and classes(fake) == classes(real)
        # Random throughout: no piece of the real value is left to recognise.
        assert not any(real[i:i + 6] in fake for i in range(len(real) - 5) if real[i:i + 6].isalnum())


def test_a_uuid_stays_a_uuid():
    real = "5b0e84a0-e29b-41d4-" + "a716-4466f5440c1d"
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", secret_fakes.make_fake(real))


@pytest.mark.parametrize("value,plain", [
    (WORDS, True), ("correct-horse-battery-staple", True), ("Summer2024", True), (NUMBER, True), ("deadbeefcafe1234", True),
    (PASSWORD, False), ("aB3xY9kQ", False), (HEX, False), (CUSTOM, False), (SLASHY, False), (OPENAI, False), (GITHUB, False),
])
def test_a_value_that_reads_like_text_is_told_apart(value, plain):
    assert secret_fakes.plain(value) is plain


def test_hex_stays_hex_and_digits_stay_digits():
    assert re.fullmatch(r"[0-9a-f]{64}", secret_fakes.make_fake(HEX))
    assert re.fullmatch(r"\d{12}", secret_fakes.make_fake("482915730264"))


def test_a_github_stand_in_carries_a_valid_checksum():
    kind, prefix = secret_fakes.match_format(GITHUB)
    fake = secret_fakes.make_fake(GITHUB, kind, prefix)
    body, check = fake[4:34], fake[34:]
    n, alphabet = zlib.crc32(body.encode()), "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    want = ""
    while n:
        n, r = divmod(n, 62)
        want = alphabet[r] + want
    assert fake.startswith("ghp_") and check == want.rjust(6, "0")


def test_a_jwt_keeps_its_readable_part_and_a_pem_its_frame():
    kind, prefix = secret_fakes.match_format(JWT)
    fake = secret_fakes.make_fake(JWT, kind, prefix)
    assert fake.rsplit(".", 1)[0] == JWT.rsplit(".", 1)[0] and fake.rsplit(".", 1)[1] != JWT.rsplit(".", 1)[1]
    kind, prefix = secret_fakes.match_format(PEM)
    fake = secret_fakes.make_fake(PEM, kind, prefix)
    assert fake.splitlines()[0] == PEM.splitlines()[0] and fake.splitlines()[-1] == PEM.splitlines()[-1]
    assert [len(x) for x in fake.splitlines()] == [len(x) for x in PEM.splitlines()]
    assert fake.splitlines()[1] != PEM.splitlines()[1]


def test_a_stand_in_never_overlaps_a_value_already_kept():
    taken = [OPENAI, "0000"]
    for _ in range(20):
        fake = secret_fakes.make_fake("48291573", taken=taken)
        assert not any(fake in t or t in fake for t in taken)


def test_strength_and_shell_safety():
    assert secret_fakes.strength(OPENAI, "openai", 8)[0] is True
    assert secret_fakes.strength(PASSWORD)[0] is False
    assert secret_fakes.shell_safe(OPENAI) and secret_fakes.shell_safe("p@ss!w%rd#1~x")
    for bad in "$\x60\\'\"();|&<>{}\n":
        assert not secret_fakes.shell_safe("abc" + bad + "def12345")
