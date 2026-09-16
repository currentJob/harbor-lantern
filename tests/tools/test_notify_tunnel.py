"""터널 알림 링크 만들기 — 순수 함수만 (네트워크 0).

보내는 일 자체는 테스트하지 않는다. 검사할 값어치가 있는 것은 **어떤 링크를 만드는가**다 —
잘못된 링크는 사람이 눌러 보고 나서야 실패를 안다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from notify_tunnel import build_share_url, find_tunnel_url, load_config  # noqa: E402

PAGES = "https://currentjob.github.io/harbor-lantern/"


def test_the_share_link_carries_the_tunnel_address() -> None:
    """`?api=` 로 열면 화면이 주소를 저장한다(`web/js/api.js` 의 우선순위)."""
    url = build_share_url(PAGES, "https://abc-def.trycloudflare.com")
    assert url.startswith(PAGES)
    assert "api=https%3A%2F%2Fabc-def.trycloudflare.com" in url


def test_a_stale_api_parameter_is_replaced_not_appended() -> None:
    """이전 실행의 죽은 주소가 붙어 있어도 새 주소가 이긴다.

    덧붙이기만 하면 `?api=죽은주소&api=새주소` 가 되고, 어느 쪽을 읽을지는 구현에 달렸다 —
    눌렀는데 안 되는 링크가 된다.
    """
    url = build_share_url(PAGES + "?api=https://old.trycloudflare.com", "https://new.trycloudflare.com")
    assert url.count("api=") == 1
    assert "new.trycloudflare.com" in url
    assert "old.trycloudflare.com" not in url


def test_other_query_parameters_and_the_fragment_survive() -> None:
    url = build_share_url(PAGES + "?lang=ko#plan", "https://x.trycloudflare.com")
    assert "lang=ko" in url
    assert url.endswith("#plan")


def test_a_trailing_slash_on_the_tunnel_address_is_dropped() -> None:
    """`https://x.trycloudflare.com//api/...` 로 이어 붙는 것을 막는다."""
    url = build_share_url(PAGES, "https://x.trycloudflare.com/")
    assert "trycloudflare.com%2F&" not in url and not url.endswith("%2F")


def test_the_last_address_in_the_log_wins() -> None:
    """로그는 append 된다 — 처음 것을 고르면 **죽은 터널**을 보내게 된다."""
    log = (
        "2026-09-16T08:00:00Z INF https://first-one.trycloudflare.com\n"
        "2026-09-16T08:27:00Z INF https://second-one.trycloudflare.com\n"
    )
    assert find_tunnel_url(log) == "https://second-one.trycloudflare.com"


def test_no_address_in_the_log_is_not_an_error_value() -> None:
    assert find_tunnel_url("INF starting tunnel\n") is None


@pytest.mark.parametrize("document", [
    {},                                   # 빈 설정
    {"token": "", "chat": 1},             # 토큰 없음
    {"token": "abc"},                     # chat 없음
])
def test_an_incomplete_config_reads_as_no_config(tmp_path: Path, document: dict[str, object]) -> None:
    """설정이 모자라면 **없는 것으로 친다** — 알림이 없다고 터널이 실패한 것은 아니다."""
    target = tmp_path / "telegram.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    assert load_config(target) is None


def test_a_missing_config_path_reads_as_no_config(tmp_path: Path) -> None:
    assert load_config(tmp_path / "없는파일.json") is None
    assert load_config(None) is None


def test_a_complete_config_is_read(tmp_path: Path) -> None:
    target = tmp_path / "telegram.json"
    target.write_text(json.dumps({"token": "t" * 46, "chat": 1234567890}), encoding="utf-8")
    config = load_config(target)
    assert config is not None
    assert config["token"] == "t" * 46
    assert config["chat"] == 1234567890


def test_the_tool_does_not_carry_a_copy_of_the_secret() -> None:
    """토큰은 하네스의 설정 파일 한 곳에만 둔다 — 이 도구는 **경로만** 받는다."""
    source = (TOOLS_DIR / "notify_tunnel.py").read_text(encoding="utf-8")
    assert "HL_TELEGRAM_CONFIG" in source
    # 봇 토큰 모양(숫자:영숫자)이 소스에 박혀 있으면 안 된다.
    import re
    assert not re.search(r"\b\d{8,}:[A-Za-z0-9_-]{30,}\b", source)
