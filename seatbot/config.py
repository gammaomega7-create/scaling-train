"""환경 변수에서 설정을 읽는다. 비밀번호는 절대 코드에 넣지 않는다."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# 사람이 손으로 새로고침하는 속도(약 2초에 1번)보다 빠르게는 요청하지 않는다.
MIN_INTERVAL_SEC = 2.0

# 열람실 이름 → 시스템 내부 ID. 앱 트래픽을 캡처한 뒤 채운다.
ROOM_CATALOG = Path(__file__).resolve().parent.parent / "rooms.json"

ANY_SEAT = "*"

MODES = ("reserve", "notify", "check")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Room:
    name: str
    id: str


@dataclass(frozen=True)
class SeatPref:
    seat: str              # 좌석 번호(예: "50" 또는 "050"), 또는 ANY_SEAT
    room: str | None = None  # 열람실 이름. None이면 고른 열람실 아무 데나


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def load_catalog(path: Path = ROOM_CATALOG) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_rooms(selection: list[str], catalog: dict[str, str]) -> list[Room]:
    """열람실 이름(또는 ID) 목록을 Room 목록으로 바꾼다. 적은 순서가 우선순위다."""
    if not selection:
        raise ConfigError("열람실을 하나 이상 골라야 합니다.")
    if not catalog:
        # 아직 rooms.json을 채우기 전에는 적은 값을 내부 ID로 그대로 쓴다.
        return [Room(name=s, id=s) for s in selection]

    by_id = {room_id: name for name, room_id in catalog.items()}
    rooms = []
    for s in selection:
        if s in catalog:
            rooms.append(Room(name=s, id=catalog[s]))
        elif s in by_id:
            rooms.append(Room(name=by_id[s], id=s))
        else:
            raise ConfigError(f"모르는 열람실: {s!r} (가능한 값: {', '.join(catalog)})")
    return rooms


def parse_seat_prefs(value: str, rooms: list[Room]) -> list[SeatPref]:
    """'A12' 는 고른 열람실 아무 데나, '제1열람실:A12' 는 그 열람실에서만,
    '제1열람실:*' 는 그 열람실의 아무 자리나 뜻한다."""
    names = {r.name for r in rooms}
    prefs = []
    for item in _split(value):
        room, sep, seat = item.rpartition(":")
        room, seat = room.strip(), seat.strip()
        if not sep:
            prefs.append(SeatPref(seat=seat))
            continue
        if room not in names:
            raise ConfigError(f"좌석 {item!r}의 열람실 {room!r}이(가) 고른 열람실 목록에 없습니다.")
        prefs.append(SeatPref(seat=seat or ANY_SEAT, room=room))
    return prefs


@dataclass
class Config:
    student_id: str
    password: str
    rooms: list[Room]
    preferred_seats: list[SeatPref] = field(default_factory=list)
    interval_sec: float = 3.0
    duration_min: int = 120
    mode: str = "reserve"  # reserve: 예약, notify: 알림만, check: 연결만 확인
    ntfy_topic: str = ""

    @classmethod
    def from_env(cls, env=os.environ, catalog: dict[str, str] | None = None) -> "Config":
        missing = [k for k in ("PNU_ID", "PNU_PW", "ROOMS") if not env.get(k)]
        if missing:
            raise ConfigError(f"필수 환경 변수가 없습니다: {', '.join(missing)}")

        interval = float(env.get("INTERVAL_SEC") or 3)
        if interval < MIN_INTERVAL_SEC:
            raise ConfigError(f"INTERVAL_SEC는 {MIN_INTERVAL_SEC:g}초 이상이어야 합니다.")

        duration = int(env.get("DURATION_MIN") or 120)
        # GitHub Actions 작업 하나는 최대 6시간까지만 실행된다.
        if not 1 <= duration <= 350:
            raise ConfigError("DURATION_MIN은 1~350 사이여야 합니다.")

        mode = (env.get("MODE") or "reserve").strip().lower()
        if mode not in MODES:
            raise ConfigError(f"MODE는 {', '.join(MODES)} 중 하나여야 합니다.")

        rooms = resolve_rooms(_split(env["ROOMS"]), load_catalog() if catalog is None else catalog)
        return cls(
            student_id=env["PNU_ID"],
            password=env["PNU_PW"],
            rooms=rooms,
            preferred_seats=parse_seat_prefs(env.get("SEATS", ""), rooms),
            interval_sec=interval,
            duration_min=duration,
            mode=mode,
            ntfy_topic=env.get("NTFY_TOPIC", ""),
        )
