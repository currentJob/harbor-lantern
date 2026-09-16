"""터널 주소가 담긴 접속 링크를 Telegram 으로 보낸다.

**왜 주소가 아니라 링크인가.** 화면은 `?api=` 로 받은 주소를 그 브라우저에 저장한다
(`web/js/api.js` 의 우선순위: `?api=` → 저장값 → 빌드 설정 → 동일 출처). 그래서 주소만
보내면 사람이 옮겨 적어야 하지만, `<Pages 주소>?api=<터널 주소>` 를 보내면 **휴대폰에서
링크 한 번**으로 연결이 끝난다.

**터널 주소는 재시작마다 바뀐다.** 그래서 배포 시점에 굽지 않는다(README). 대신 띄울 때마다
이 도구가 새 링크를 보낸다 — 바뀌는 값을 사람이 따라다니지 않게 하는 것이 이 도구의 전부다.

**비밀값을 복사하지 않는다.** 토큰은 하네스의 `.local/telegram/config.json` 한 곳에만 둔다는
정책이 있으므로, 여기서는 **그 파일의 경로만** 받는다(`--config` 또는 `HL_TELEGRAM_CONFIG`).
설정이 없으면 조용히 건너뛴다 — 알림이 없다고 터널이 실패한 것은 아니다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

import httpx

__all__ = ["build_share_url", "find_tunnel_url", "load_config", "send"]

DEFAULT_PAGES = "https://currentjob.github.io/harbor-lantern/"
TUNNEL_PATTERN = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def find_tunnel_url(log_text: str) -> str | None:
    """cloudflared 로그에서 **마지막** 주소를 고른다.

    로그는 append 되므로 이전 실행의 주소가 위에 남아 있다. 처음 것을 고르면 죽은 터널을
    보내게 된다 — 링크를 눌렀는데 안 되는 것이 링크를 안 보내는 것보다 나쁘다.
    """
    found = TUNNEL_PATTERN.findall(log_text)
    return found[-1] if found else None


def build_share_url(pages_url: str, api_base: str) -> str:
    """`<Pages 주소>?api=<터널 주소>`.

    이미 붙어 있던 `api` 는 **덮어쓴다**(이전 실행의 죽은 주소가 남아 있을 수 있다).
    다른 쿼리는 보존한다. 조각(`#...`)은 쿼리 뒤에 와야 하므로 마지막에 다시 붙인다.
    """
    parts = urllib.parse.urlsplit(pages_url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != "api"]
    query.append(("api", api_base.rstrip("/")))
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def load_config(path: str | os.PathLike[str] | None) -> dict[str, object] | None:
    """`{token, chat}` 을 읽는다. 없으면 `None` — 알림은 선택 기능이다."""
    target = Path(path) if path else None
    if target is None or not target.is_file():
        return None
    document = json.loads(target.read_text(encoding="utf-8"))
    token, chat = document.get("token"), document.get("chat")
    if not isinstance(token, str) or not token or chat is None:
        return None
    return {"token": token, "chat": chat}


def send(config: dict[str, object], text: str) -> int:
    """Telegram 으로 보낸다. HTTP 상태 코드를 돌려준다.

    `urllib` 을 쓰지 않는 이유가 있다 — `urllib` 은 `file://` 도 연다. 여기서는 URL 이
    상수 템플릿이지만 토큰이 설정 파일에서 오므로 정적 분석은 "동적 값"으로 읽고
    막는다(이 저장소가 좌표 해석 도구에서 이미 겪은 일이다 · 커밋 9534daa).
    `httpx` 는 HTTP(S) 만 말하므로 그 경로 자체가 없다.
    """
    response = httpx.post(
        TELEGRAM_API.format(token=config["token"]),
        data={"chat_id": str(config["chat"]), "text": text, "disable_web_page_preview": "true"},
        timeout=20,
    )
    return int(response.status_code)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="터널 접속 링크를 Telegram 으로 보낸다.")
    parser.add_argument("--url", help="터널 주소. 생략하면 --log 에서 찾는다")
    parser.add_argument("--log", default=".local/tunnel.stderr.log", help="cloudflared 로그")
    parser.add_argument("--pages", default=os.environ.get("HL_PAGES_URL", DEFAULT_PAGES))
    parser.add_argument("--config", default=os.environ.get("HL_TELEGRAM_CONFIG"),
                        help="Telegram 설정 파일 경로(토큰을 복사하지 않고 가리킨다)")
    parser.add_argument("--dry-run", action="store_true", help="보내지 않고 링크만 출력한다")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # 한국어 Windows 콘솔은 기본이 cp949 다
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    api_base = args.url
    if not api_base:
        log = Path(args.log)
        if not log.is_file():
            print(f"터널 로그가 없다: {log}", file=sys.stderr)
            return 1
        api_base = find_tunnel_url(log.read_text(encoding="utf-8", errors="replace"))
    if not api_base:
        print("로그에서 터널 주소를 찾지 못했다 — 터널이 아직 주소를 받지 못했을 수 있다.", file=sys.stderr)
        return 1

    share = build_share_url(args.pages, api_base)
    text = (
        "Harbor Lantern 백엔드가 열렸습니다.\n\n"
        f"{share}\n\n"
        "이 링크로 열면 주소가 이 브라우저에 저장됩니다. "
        "터널 주소는 재시작마다 바뀌므로 그때마다 새 링크가 옵니다.\n"
        f"백엔드 주소만: {api_base}"
    )
    if args.dry_run:
        print(share)
        return 0

    config = load_config(args.config)
    if config is None:
        print("Telegram 설정이 없어 보내지 않았다(터널은 정상이다).", file=sys.stderr)
        print(share)
        return 0
    status = send(config, text)
    print(f"Telegram 전송 HTTP {status}")
    print(share)
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
