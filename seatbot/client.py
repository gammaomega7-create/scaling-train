"""좌석 시스템과 통신하는 부분.

PNU Place 앱의 실제 API(주소, 요청 형식)는 아직 확인되지 않았다.
앱 트래픽을 캡처한 뒤 PnuPlaceClient의 TODO 부분을 채운다. (README 참고)
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Seat:
    room_id: str
    seat_id: str      # 예약 요청에 쓰이는 내부 ID
    name: str         # 화면에 보이는 좌석 번호 (예: "A-12")
    available: bool


class AuthExpired(Exception):
    """세션이 만료되어 다시 로그인해야 함."""


class AlreadyReserved(Exception):
    """이미 이용 중인 좌석이 있음."""


class ReserveFailed(Exception):
    """다른 사람이 먼저 잡는 등의 이유로 예약 실패."""


class SeatClient(Protocol):
    def login(self, student_id: str, password: str) -> None: ...
    def list_seats(self, room_id: str) -> list[Seat]: ...
    def reserve(self, seat: Seat) -> None: ...
    def has_active_reservation(self) -> bool: ...


class PnuPlaceClient:
    """PNU Place 실제 클라이언트. API 확인 후 구현한다."""

    BASE_URL = ""  # TODO: 캡처한 API 서버 주소

    def __init__(self):
        import requests

        self.session = requests.Session()
        self.session.headers["User-Agent"] = "seatbot (personal use)"
        # 요청을 보낼 때는 항상 timeout=10 을 넘긴다.

    def login(self, student_id: str, password: str) -> None:
        raise NotImplementedError("PNU Place 로그인 API 정보가 필요합니다.")

    def list_seats(self, room_id: str) -> list[Seat]:
        raise NotImplementedError("PNU Place 좌석 조회 API 정보가 필요합니다.")

    def reserve(self, seat: Seat) -> None:
        raise NotImplementedError("PNU Place 좌석 예약 API 정보가 필요합니다.")

    def has_active_reservation(self) -> bool:
        raise NotImplementedError("PNU Place 내 예약 조회 API 정보가 필요합니다.")
