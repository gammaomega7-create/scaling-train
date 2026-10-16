"""환경 변수에서 설정을 읽는다. 비밀번호는 절대 코드에 넣지 않는다."""

import os
from dataclasses import dataclass, field

# 사람이 손으로 새로고침하는 속도(약 2초에 1번)보다 빠르게는 요청하지 않는다.
MIN_INTERVAL_SEC = 2.0


class ConfigError(ValueError):
    pass


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass
class Config:
    student_id: str
    password: str
    room_ids: list[str]
    preferred_seats: list[str] = field(default_factory=list)
    interval_sec: float = 3.0
    duration_min: int = 120
    notify_only: bool = False
    ntfy_topic: str = ""

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        missing = [k for k in ("PNU_ID", "PNU_PW", "ROOM_IDS") if not env.get(k)]
        if missing:
            raise ConfigError(f"필수 환경 변수가 없습니다: {', '.join(missing)}")

        interval = float(env.get("INTERVAL_SEC") or 3)
        if interval < MIN_INTERVAL_SEC:
            raise ConfigError(f"INTERVAL_SEC는 {MIN_INTERVAL_SEC:g}초 이상이어야 합니다.")

        duration = int(env.get("DURATION_MIN") or 120)
        # GitHub Actions 작업 하나는 최대 6시간까지만 실행된다.
        if not 1 <= duration <= 350:
            raise ConfigError("DURATION_MIN은 1~350 사이여야 합니다.")

        return cls(
            student_id=env["PNU_ID"],
            password=env["PNU_PW"],
            room_ids=_split(env["ROOM_IDS"]),
            preferred_seats=_split(env.get("SEATS", "")),
            interval_sec=interval,
            duration_min=duration,
            notify_only=env.get("NOTIFY_ONLY", "").lower() in ("1", "true", "yes"),
            ntfy_topic=env.get("NTFY_TOPIC", ""),
        )
