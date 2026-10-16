"""빈자리가 날 때까지 일정 간격으로 조회하다가, 나면 예약(또는 알림)한다."""

import logging
import random
import time
from typing import Callable

from .client import AlreadyReserved, AuthExpired, ReserveFailed, Seat, SeatClient
from .config import Config
from .notify import notify

log = logging.getLogger(__name__)

MAX_CONSECUTIVE_ERRORS = 5


def pick_seat(seats: list[Seat], preferred: list[str]) -> Seat | None:
    """선호 좌석 목록 순서대로 고르고, 목록이 비어 있으면 아무 빈자리나 고른다."""
    free = [s for s in seats if s.available]
    if not preferred:
        return free[0] if free else None
    by_name = {s.name: s for s in free}
    for name in preferred:
        if name in by_name:
            return by_name[name]
    return None


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

    errors = 0
    attempt = 0
    while now() < deadline:
        attempt += 1
        try:
            for room_id in cfg.room_ids:
                seat = pick_seat(client.list_seats(room_id), cfg.preferred_seats)
                if not seat:
                    continue
                if cfg.notify_only:
                    notify(cfg.ntfy_topic, "빈자리 발견", f"{seat.name} 좌석이 비었습니다. 앱에서 예약하세요.")
                    return seat
                try:
                    client.reserve(seat)
                except ReserveFailed as e:
                    log.info("%s 예약 실패(%s), 계속 대기", seat.name, e)
                    continue
                notify(cfg.ntfy_topic, "예약 성공", f"{seat.name} 좌석을 예약했습니다. 시간 안에 입실하세요.")
                return seat
            errors = 0
            log.info("시도 %d: 빈자리 없음", attempt)
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

        # 일정한 주기로 요청하지 않도록 약간의 무작위 간격을 더한다.
        sleep(cfg.interval_sec + random.uniform(0, cfg.interval_sec * 0.3))

    notify(cfg.ntfy_topic, "대기 종료", f"{cfg.duration_min}분 동안 빈자리가 나지 않았습니다.")
    return None
