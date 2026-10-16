"""빈자리가 날 때까지 일정 간격으로 조회하다가, 나면 예약(또는 알림)한다."""

import logging
import random
import time
from typing import Callable

from .client import AlreadyReserved, AuthExpired, ReserveFailed, Seat, SeatClient
from .config import ANY_SEAT, Config, Room, SeatPref
from .notify import notify

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 5
MAX_BACKOFF_SEC = 60


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
            if pref.seat in (ANY_SEAT, s.name) and (room, s) not in picked:
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


def run(
    cfg: Config,
    client: SeatClient,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> Seat | None:
    """좌석을 잡으면 그 좌석을, 시간 안에 못 잡으면 None을 돌려준다."""
    deadline = now() + cfg.duration_min * 60
    client.login(cfg.student_id, cfg.password)

    if client.has_active_reservation():
        notify(cfg.ntfy_topic, "이미 예약 있음", "이용 중인 좌석이 있어 종료합니다.")
        return None

    log.info("대상 열람실: %s", ", ".join(r.name for r in cfg.rooms))
    errors = 0
    attempt = 0
    while now() < deadline:
        attempt += 1
        try:
            seats_by_room = [(room, client.list_seats(room.id)) for room in cfg.rooms]
            for room, seat in candidates(seats_by_room, cfg.preferred_seats):
                label = f"{room.name} {seat.name}"
                if cfg.notify_only:
                    notify(cfg.ntfy_topic, "빈자리 발견", f"{label} 좌석이 비었습니다. 앱에서 예약하세요.")
                    return seat
                try:
                    client.reserve(seat)
                except ReserveFailed as e:
                    log.info("%s 예약 실패(%s), 다음 후보 시도", label, e)
                    continue
                notify(cfg.ntfy_topic, "예약 성공", f"{label} 좌석을 예약했습니다. 시간 안에 입실하세요.")
                return seat
            errors = 0
            log.info("시도 %d: 원하는 빈자리 없음", attempt)
        except AuthExpired:
            # 로그인 직후에도 계속 만료되면 아래 오류 횟수 제한에 걸리도록 센다.
            errors += 1
            if errors >= MAX_CONSECUTIVE_ERRORS:
                notify(cfg.ntfy_topic, "봇 중단", "로그인이 계속 만료되어 종료합니다.")
                raise
            log.info("세션 만료, 다시 로그인")
            client.login(cfg.student_id, cfg.password)
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

    notify(cfg.ntfy_topic, "대기 종료", f"{cfg.duration_min}분 동안 빈자리가 나지 않았습니다.")
    return None
