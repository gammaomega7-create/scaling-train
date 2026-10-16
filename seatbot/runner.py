"""빈자리가 날 때까지 일정 간격으로 조회하다가, 나면 예약(또는 알림)한다.

평소에는 메인 화면과 같은 "열람실별 남은 좌석 수" 요청 한 번만 보내고,
고른 열람실에 남은 좌석이 생겼을 때만 그 열람실 도면을 불러온다.
"""

import logging
import random
import time
from typing import Callable

from .client import (
    AlreadyReserved, AuthExpired, LoginFailed, ReserveFailed, Seat, SeatClient,
)
from .config import ANY_SEAT, Config, Room, SeatPref
from .notify import notify

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 5
MAX_BACKOFF_SEC = 60


def same_seat(a: str, b: str) -> bool:
    """'50' 과 '050' 처럼 앞의 0만 다른 번호는 같은 좌석으로 본다."""
    a, b = a.strip(), b.strip()
    if a.isdigit() and b.isdigit():
        return int(a) == int(b)
    return a.upper() == b.upper()


def candidates(
    seats_by_room: list[tuple[Room, list[Seat]]], preferred: list[SeatPref]
) -> list[tuple[Room, Seat]]:
    """예약을 시도할 빈자리를 우선순위 순서로 돌려준다.

    선호 좌석이 없으면 열람실 순서대로 아무 빈자리나, 있으면 선호 목록 순서대로
    그 조건에 맞는 빈자리만 고른다. 고른 열람실 밖의 좌석은 절대 고르지 않는다.
    """
    free = [(room, s) for room, seats in seats_by_room for s in seats if s.available]
    if not preferred:
        return free

    picked = []
    for pref in preferred:
        for room, s in free:
            if pref.room not in (None, room.name):
                continue
            if (pref.seat == ANY_SEAT or same_seat(pref.seat, s.name)) and (room, s) not in picked:
                picked.append((room, s))
    return picked


def next_delay(interval: float, errors: int) -> float:
    """다음 조회까지 기다릴 시간.

    일정한 주기로 요청하지 않도록 무작위 간격을 더하고, 오류가 나면
    (서버가 바쁘거나 요청을 막는 중일 수 있으므로) 점점 더 오래 쉰다.
    """
    base = interval + random.uniform(0, interval * 0.5)
    if errors:
        base = max(base, min(MAX_BACKOFF_SEC, interval * 2 ** errors))
    return base


def _login(cfg: Config, client: SeatClient) -> None:
    try:
        client.login(cfg.student_id, cfg.password)
    except LoginFailed as e:
        notify(cfg.ntfy_topic, "로그인 실패", f"학번/비밀번호를 확인하세요: {e}")
        raise


def check(cfg: Config, client: SeatClient) -> bool:
    """로그인과 조회만 해 보고 결과를 알린다. 예약은 하지 않는다."""
    _login(cfg, client)
    avail = client.room_availability()
    lines = [f"{r.name}: 남은 좌석 {avail.get(r.id, '?')}" for r in cfg.rooms]
    if client.has_active_reservation():
        lines.append("현재 이용 중인 좌석이 있습니다.")
    notify(cfg.ntfy_topic, "연결 확인 성공", "\n".join(lines))
    return True


def run(
    cfg: Config,
    client: SeatClient,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Seat | None:
    """좌석을 잡으면 그 좌석을, 시간 안에 못 잡으면 None을 돌려준다."""
    deadline = now() + cfg.duration_min * 60
    _login(cfg, client)

    if client.has_active_reservation():
        notify(cfg.ntfy_topic, "이미 예약 있음", "이용 중인 좌석이 있어 종료합니다.")
        return None

    log.info("대상 열람실: %s", ", ".join(r.name for r in cfg.rooms))
    errors = 0
    attempt = 0
    while now() < deadline:
        attempt += 1
        try:
            avail = client.room_availability()
            open_rooms = [r for r in cfg.rooms if avail.get(r.id, 0) > 0]
            seats_by_room = [(room, client.list_seats(room.id)) for room in open_rooms]
            for room, seat in candidates(seats_by_room, cfg.preferred_seats):
                label = f"{room.name} {seat.name}번"
                if cfg.mode == "notify":
                    notify(cfg.ntfy_topic, "빈자리 발견", f"{label} 좌석이 비었습니다. 앱에서 예약하세요.")
                    return seat
                try:
                    r = client.reserve(seat)
                except ReserveFailed as e:
                    log.info("%s 예약 실패(%s), 다음 후보 시도", label, e)
                    if client.has_active_reservation():
                        raise AlreadyReserved() from None
                    continue
                until = f" {r.checkin_deadline[11:16]}까지" if r.checkin_deadline else ""
                notify(cfg.ntfy_topic, "예약 성공",
                       f"{label} 배정 ({r.begin[11:16]}~{r.end[11:16]}).{until} 게이트나 키오스크에서 입실 확인하세요.")
                return seat
            errors = 0
            if attempt % 20 == 1:
                log.info("시도 %d: 원하는 빈자리 없음 (%s)", attempt,
                         ", ".join(f"{r.name} {avail.get(r.id, '?')}" for r in cfg.rooms))
        except AuthExpired:
            # 로그인 직후에도 계속 만료되면 아래 오류 횟수 제한에 걸리도록 센다.
            errors += 1
            if errors >= MAX_CONSECUTIVE_ERRORS:
                notify(cfg.ntfy_topic, "봇 중단", "로그인이 계속 만료되어 종료합니다.")
                raise
            log.info("세션 만료, 다시 로그인")
            _login(cfg, client)
            continue
        except AlreadyReserved:
            notify(cfg.ntfy_topic, "이미 예약 있음", "이용 중인 좌석이 있어 종료합니다.")
            return None
        except Exception as e:  # 네트워크 오류 등
            errors += 1
            log.warning("오류 %d/%d: %s", errors, MAX_CONSECUTIVE_ERRORS, e)
            if errors >= MAX_CONSECUTIVE_ERRORS:
                notify(cfg.ntfy_topic, "봇 중단", f"오류가 계속되어 종료합니다: {e}")
                raise

        sleep(next_delay(cfg.interval_sec, errors))

    notify(cfg.ntfy_topic, "대기 종료", f"{cfg.duration_min}분 동안 원하는 빈자리가 나지 않았습니다.")
    return None
