"""초대코드 — AC-003(생성 부분) · AC-039 (설계서 §6.4 · NFR-005).

엔트로피 60비트는 주석에 적어 두면 알파벳이나 길이가 바뀐 순간 거짓말이 된다.
그래서 **테스트가 직접 계산해서** 확인한다.
"""

from __future__ import annotations

import math
import secrets
from typing import Any

import pytest

from harbor_lantern.domain import invite
from harbor_lantern.domain.invite import (
    ALPHABET,
    CODE_LEN,
    entropy_bits,
    format_code,
    generate_code,
    normalize_code,
)


class TestAlphabetAndEntropy:
    """AC-039 — 정의된 알파벳·길이, 엔트로피 60비트 이상."""

    def test_ac039_alphabet_is_crockford_base32_without_confusables(self) -> None:
        assert len(ALPHABET) == 32
        assert len(set(ALPHABET)) == 32  # 중복 없음
        assert set("ILOU").isdisjoint(ALPHABET)  # 혼동 문자·불쾌어 방지 문자 제외
        assert ALPHABET == "".join(sorted(ALPHABET))  # 정렬돼 있어야 눈으로 검증된다

    def test_ac039_entropy_is_at_least_60_bits(self) -> None:
        bits = entropy_bits(len(ALPHABET), CODE_LEN)
        assert bits == pytest.approx(60.0)
        assert bits >= 60.0

    def test_ac039_entropy_formula(self) -> None:
        assert entropy_bits(32, 12) == pytest.approx(math.log2(32) * 12)
        assert entropy_bits(2, 8) == pytest.approx(8.0)

    def test_ac039_entropy_would_fail_the_requirement_with_a_shorter_code(self) -> None:
        """길이를 하나 줄이면 57.5비트 — NFR-005 미달이다. 하한이 빡빡하다는 사실을 남긴다."""
        assert entropy_bits(len(ALPHABET), CODE_LEN - 1) < 60.0

    def test_ac039_rejected_letters_are_documented_choices(self) -> None:
        for letter in "ILOU":
            assert letter not in ALPHABET


class TestGenerate:
    """AC-003 · AC-039 — CSPRNG 로 생성, 충돌 0."""

    def test_ac039_shape(self) -> None:
        code = generate_code()
        assert len(code) == CODE_LEN
        assert set(code).issubset(ALPHABET)

    def test_ac039_no_duplicates_in_10000_codes(self) -> None:
        codes = {generate_code() for _ in range(10_000)}
        assert len(codes) == 10_000

    def test_ac039_uses_the_secrets_module(self) -> None:
        """`random` 은 메르센 트위스터라 출력 몇 개로 내부 상태가 복원된다 — 금지."""
        assert not hasattr(invite, "random")
        assert invite.secrets is secrets

    def test_ac039_default_path_calls_secrets_choice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        class SpyingSecrets:
            @staticmethod
            def choice(sequence: str) -> str:
                calls.append(sequence)
                return sequence[0]

        monkeypatch.setattr(invite, "secrets", SpyingSecrets)
        assert generate_code() == ALPHABET[0] * CODE_LEN
        assert calls == [ALPHABET] * CODE_LEN

    def test_ac039_injected_rng_must_be_a_csprng(self) -> None:
        rng = secrets.SystemRandom()
        code = generate_code(rng)
        assert len(code) == CODE_LEN
        assert set(code).issubset(ALPHABET)

    def test_ac003_two_trips_get_different_codes(self) -> None:
        assert generate_code() != generate_code()

    def test_generated_codes_are_already_normal_form(self) -> None:
        for _ in range(50):
            code = generate_code()
            assert normalize_code(code) == code


class TestNormalize:
    def test_lowercase_is_accepted(self) -> None:
        code = generate_code()
        assert normalize_code(code.lower()) == code

    def test_display_hyphens_are_stripped(self) -> None:
        code = generate_code()
        assert normalize_code(format_code(code)) == code

    def test_surrounding_whitespace_and_inner_spaces(self) -> None:
        code = generate_code()
        spaced = f"  {code[:4]} {code[4:8]} {code[8:]}  "
        assert normalize_code(spaced) == code

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("I23456789ABC", "123456789ABC"),
            ("L23456789ABC", "123456789ABC"),
            ("O23456789ABC", "023456789ABC"),
            ("io23456789AB", "1023456789AB"),
        ],
    )
    def test_crockford_confusables_are_folded(self, typed: str, expected: str) -> None:
        assert normalize_code(typed) == expected

    def test_full_width_input_is_normalised(self) -> None:
        assert normalize_code("ＡＢＣＤ２３４５６７８９") == "ABCD23456789"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "SHORT",
            "0123456789ABCD",  # 13자
            "0123456789A!",  # 알파벳 밖
            "U23456789ABC",  # U 는 알파벳에 없고 매핑도 하지 않는다
            "한글이라서안됨아아아",
        ],
    )
    def test_ac040_malformed_codes_return_none(self, bad: str) -> None:
        """`None` 은 **404 로** 응답해야 한다 — 422 를 주면 열거 신호가 된다(§12 F7)."""
        assert normalize_code(bad) is None

    def test_non_string_input_is_none_not_an_exception(self) -> None:
        assert normalize_code(None) is None  # type: ignore[arg-type]
        assert normalize_code(123456789012) is None  # type: ignore[arg-type]

    def test_normalisation_is_idempotent(self) -> None:
        code = generate_code()
        once = normalize_code(format_code(code).lower())
        assert once is not None
        assert normalize_code(once) == once


class TestFormat:
    def test_ac003_display_grouping(self) -> None:
        assert format_code("0123456789AB") == "0123-4567-89AB"

    def test_format_round_trips_through_normalize(self) -> None:
        for _ in range(20):
            code = generate_code()
            formatted = format_code(code)
            assert formatted.count("-") == 2
            assert normalize_code(formatted) == code


def test_module_exports_are_importable() -> None:
    """IMP-B 가 import 할 이름들 — 오타로 사라지면 여기서 먼저 걸린다."""
    exported: list[str] = list(invite.__all__)
    for name in exported:
        assert hasattr(invite, name), name
    value: Any = invite.CODE_GROUP
    assert value == 4
