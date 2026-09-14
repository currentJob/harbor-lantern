"""초대코드 — DSN-07 (설계서 §6.4 · REQ-002 · NFR-005 · AC-003 · AC-039).

무계정 공유 모델(A4)에서 **초대코드가 유일한 접근 통제 수단**이다. 그래서 두 가지가
동시에 필요하다 — 추측 난이도(CSPRNG · 60비트 이상)와 사람이 불러 줄 수 있는 형태
(혼동 문자 제외 · 대소문자·하이픈 무시).

`random` 모듈을 쓰지 않는다. 메르센 트위스터는 출력 몇 개로 내부 상태가 복원된다.
"""

from __future__ import annotations

import math
import secrets
import unicodedata

__all__ = ["ALPHABET", "CODE_LEN", "CODE_GROUP", "entropy_bits", "format_code", "generate_code", "normalize_code"]

# Crockford Base32 — I·L·O·U 를 뺀 32자.
# I·L 은 1 과, O 는 0 과 헷갈린다. U 는 우연히 만들어지는 불쾌한 단어를 줄이려는
# Crockford 원저자의 관례다.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LEN = 12  # log2(32) × 12 = 60.0 비트 (NFR-005 의 하한을 정확히 만족)
CODE_GROUP = 4  # 표시용 묶음 크기 — 'XXXX-XXXX-XXXX'

# 입력 관용: Crockford 관례대로 I·L → 1, O → 0. U 는 매핑하지 않는다(알파벳 밖 → 거부).
_CONFUSABLES = str.maketrans({"I": "1", "L": "1", "O": "0"})
_STRIP = str.maketrans({"-": None, " ": None, "\t": None, "\n": None, "\r": None, "_": None})

_ALPHABET_SET = frozenset(ALPHABET)


def generate_code(rng: secrets.SystemRandom | None = None) -> str:
    """CSPRNG 로 정규형 12자 코드를 만든다 (AC-039).

    `rng` 를 주지 않으면 `secrets.choice` 를 쓴다. 인자를 남겨 둔 이유는 테스트가
    **다른 난수원을 꽂기 위해서가 아니라** 호출부가 하나의 `SystemRandom` 인스턴스를
    재사용할 수 있게 하기 위해서다 — 어느 쪽이든 CSPRNG 다.
    """
    if rng is None:
        return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return "".join(rng.choice(ALPHABET) for _ in range(CODE_LEN))


def normalize_code(raw: str) -> str | None:
    """사람이 친 문자열 → 정규형 12자. 형식이 아니면 `None`.

    NFKC → 대문자 → `-`·공백 제거 → `I`·`L`→`1`, `O`→`0` → 길이·알파벳 검사.

    > **호출부 주의 (AC-040 · §12 F7).** `None` 을 **422 로 돌려주면 안 된다.**
    > 형식이 틀린 코드와 존재하지 않는 코드는 **완전히 같은 404 본문**이어야 한다 —
    > 구별되는 순간 열거 공격에 "형식은 맞았다"는 신호를 주게 된다.
    """
    if not isinstance(raw, str):
        return None
    text = unicodedata.normalize("NFKC", raw).upper().translate(_STRIP).translate(_CONFUSABLES)
    if len(text) != CODE_LEN:
        return None
    if not _ALPHABET_SET.issuperset(text):
        return None
    return text


def format_code(code: str) -> str:
    """정규형 → 표시용 `'XXXX-XXXX-XXXX'`. 저장·비교는 언제나 정규형으로 한다."""
    return "-".join(code[index : index + CODE_GROUP] for index in range(0, len(code), CODE_GROUP))


def entropy_bits(alphabet_size: int, length: int) -> float:
    """`log2(alphabet_size) × length`. NFR-005 의 60비트 하한을 **테스트가 계산해** 확인한다.

    상수 `60.0` 을 주석에 적어 두는 것으로는 알파벳이나 길이가 바뀐 순간 거짓말이 된다.
    """
    if alphabet_size < 2 or length < 1:
        raise ValueError("알파벳은 2자 이상, 길이는 1 이상이어야 한다")
    return math.log2(alphabet_size) * length
