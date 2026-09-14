"""네트워크 비의존 검증 — AC-037 (설계서 §6.13 · NFR-003).

`tests/conftest.py` 의 `block_network` 픽스처가 세션 전체에서 루프백 밖 연결을 막는다.
**그 차단기 자체를 검사하는 것이 이 파일의 일**이다.

왜 차단기를 또 검사하나. 차단이 조용히 풀리면 (누가 픽스처를 지우거나, 이름을 바꾸거나,
autouse 를 떼거나) 테스트는 계속 통과한다 — 다만 인터넷이 되는 기계에서만 통과한다.
그런 스위트는 비행기 안에서, 그리고 CI 의 네트워크 없는 러너에서 처음 빨개진다.
차단이 살아 있는지 묻는 테스트가 하나 있어야 그 사고가 여기서 끝난다.

여기서 쓰는 목적지는 **연결을 시도조차 하지 않는 문서화용 주소**다(TEST-NET-3,
RFC 5737). 차단이 정상 동작하면 소켓을 열기 전에 예외가 난다.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tests.conftest import NetworkAccessBlocked

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# RFC 5737 TEST-NET-3 — 실제로 라우팅되지 않는 문서화 전용 주소.
UNROUTABLE = ("203.0.113.7", 80)


def test_socket_connect_to_outside_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkAccessBlocked):
            sock.connect(UNROUTABLE)
    finally:
        sock.close()


def test_create_connection_to_outside_is_blocked() -> None:
    with pytest.raises(NetworkAccessBlocked):
        socket.create_connection(UNROUTABLE, timeout=0.1)


def test_dns_lookup_is_blocked() -> None:
    """connect 만 막으면 DNS 조회에서 수 초를 버린 뒤에야 실패한다 — 느린 실패는 원인을 흐린다."""
    with pytest.raises(NetworkAccessBlocked):
        socket.getaddrinfo("example.com", 443)


def test_loopback_is_still_allowed() -> None:
    """차단기가 루프백까지 막으면 TestClient·임시 서버가 죽는다.

    127.0.0.1 의 닫힌 포트로 붙으면 '연결 거부'가 나야 한다 — 차단기의 예외가 아니라.
    """
    with pytest.raises(OSError) as caught:
        socket.create_connection(("127.0.0.1", 9), timeout=0.2)
    assert not isinstance(caught.value, NetworkAccessBlocked), "루프백까지 막혔다 — 인프로세스 테스트가 불가능해진다"


def test_http_client_cannot_reach_the_internet() -> None:
    """httpx 로 밖에 나가려 하면 실패한다 (AC-037 — 실제 HTTP 호출 0건)."""
    httpx = pytest.importorskip("httpx")
    with pytest.raises(Exception) as caught:  # noqa: B017 — 어떤 예외든 '나가지 못했다'가 요점이다
        httpx.get("https://example.com", timeout=1.0)
    assert not isinstance(caught.value, AssertionError)


def test_external_adapters_are_not_called_at_import_time() -> None:
    """어댑터 모듈을 import 하는 것만으로 외부 호출이 일어나면 안 된다.

    import 시점 호출은 테스트 수집(collection) 단계에서 터진다 — 그 순간 실패 메시지가
    '어떤 테스트'가 아니라 '수집 실패'로 나와 원인을 찾기 어렵다.
    """
    external = PROJECT_ROOT / "src" / "harbor_lantern" / "services" / "external"
    if not external.is_dir():
        pytest.skip("services/external/ 이 아직 없다 (T2 · IMP-B 진행 중)")
    pytest.importorskip("harbor_lantern.services.external.openmeteo")
    pytest.importorskip("harbor_lantern.services.external.frankfurter")
    # 여기까지 예외 없이 왔다 = import 만으로는 아무 데도 접속하지 않았다.
