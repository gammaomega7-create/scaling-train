"""부산대 도서관(Pyxis) 좌석 API와 통신하는 부분.

도서관 앱은 lib.pusan.ac.kr 웹 화면을 그대로 띄우는 앱이고, 실제 통신은
아래 /pyxis-api 주소로 한다. (2026-10 앱 트래픽 캡처로 확인)

    POST /pyxis-api/sso/mobile-login              로그인 → data.accessToken
    GET  /pyxis-api/1/seat-rooms?roomTypeId=5...  열람실별 남은 좌석 수 (메인 화면)
    GET  /pyxis-api/1/api/rooms/{방}/seats        열람실 도면(좌석별 상태)
    POST /pyxis-api/1/api/seat-charges            좌석 배정 {"seatId", "smufMethodCode"}
    GET  /pyxis-api/1/api/seat-charges            내 배정 현황

로그인 후에는 Pyxis-Auth-Token 헤더에 토큰을 넣어 보낸다.
"""

from dataclasses import dataclass
from typing import Protocol

BASE_URL = "https://lib.pusan.ac.kr/pyxis-api"
METHOD = "MOBILE"

# 새벽벌도서관 열람실 (메인 화면의 "남은 좌석" 목록과 같은 조건)
BRANCH_ID = 2
BUILDING_ID = 2
READING_ROOM_TYPE_ID = 5

TIMEOUT_SEC = 10


@dataclass(frozen=True)
class Seat:
    room_id: str
    seat_id: str      # 예약 요청에 쓰이는 내부 ID
    name: str         # 화면에 보이는 좌석 번호 (예: "050")
    available: bool


@dataclass(frozen=True)
class Reservation:
    room_name: str
    seat_name: str
    begin: str
    end: str
    checkin_deadline: str  # 이 시각까지 게이트/키오스크에서 입실 확인을 해야 한다


class ApiError(Exception):
    """서버가 success=false 로 답함."""


class LoginFailed(Exception):
    """학번이나 비밀번호가 틀리는 등 로그인 자체가 안 됨. 재시도해도 소용없다."""


class AuthExpired(Exception):
    """세션이 만료되어 다시 로그인해야 함."""


class AlreadyReserved(Exception):
    """이미 이용 중인 좌석이 있음."""


class ReserveFailed(Exception):
    """다른 사람이 먼저 잡는 등의 이유로 예약 실패."""


class SeatClient(Protocol):
    def login(self, student_id: str, password: str) -> None: ...
    def room_availability(self) -> dict[str, int]: ...
    def list_seats(self, room_id: str) -> list[Seat]: ...
    def reserve(self, seat: Seat) -> Reservation: ...
    def has_active_reservation(self) -> bool: ...


class PnuLibraryClient:
    def __init__(self, session=None):
        if session is None:
            import requests

            session = requests.Session()
        self.session = session
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Accept-Language": "ko",
            "Origin": "https://lib.pusan.ac.kr",
            "Referer": "https://lib.pusan.ac.kr/",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko)",
        })

    def _call(self, method: str, path: str, **kw) -> dict:
        resp = self.session.request(method, BASE_URL + path, timeout=TIMEOUT_SEC, **kw)
        if resp.status_code in (401, 403):
            raise AuthExpired(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            code = str(body.get("code", ""))
            # 만료 응답의 정확한 코드는 아직 못 봤다. 로그인/인증 관련 코드면 만료로 본다.
            if any(w in code.lower() for w in ("login", "auth", "token", "session")):
                raise AuthExpired(code)
            raise ApiError(f"{code}: {body.get('message', '')}")
        return body

    def login(self, student_id: str, password: str) -> None:
        self.session.headers.pop("Pyxis-Auth-Token", None)
        try:
            body = self._call("POST", "/sso/mobile-login", json={
                "loginId": student_id, "password": password,
                "isFamilyLogin": False, "isMobile": True,
            })
        except (ApiError, AuthExpired) as e:
            raise LoginFailed(str(e)) from None
        self.session.headers["Pyxis-Auth-Token"] = body["data"]["accessToken"]

    def room_availability(self) -> dict[str, int]:
        body = self._call("GET", "/1/seat-rooms", params={
            "roomTypeId": READING_ROOM_TYPE_ID, "smufMethodCode": METHOD,
            "buildingId": BUILDING_ID, "branchId": BRANCH_ID,
        })
        return {str(r["id"]): int(r["seats"]["available"]) for r in body["data"]["list"]}

    def list_seats(self, room_id: str) -> list[Seat]:
        body = self._call("GET", f"/1/api/rooms/{room_id}/seats")
        return [
            Seat(room_id=str(room_id), seat_id=str(s["id"]), name=s["code"],
                 available=bool(s["isActive"]) and not s["isOccupied"])
            for s in body["data"]["list"]
        ]

    def reserve(self, seat: Seat) -> Reservation:
        try:
            body = self._call("POST", "/1/api/seat-charges",
                              json={"seatId": int(seat.seat_id), "smufMethodCode": METHOD})
        except ApiError as e:
            raise ReserveFailed(str(e)) from None
        d = body["data"]
        deadline = ""
        try:
            for c in self._call("GET", "/1/api/seat-charges").get("data", {}).get("list", []):
                if c["id"] == d["id"]:
                    deadline = c.get("checkinExpiryDate") or ""
        except ApiError:
            pass  # 예약은 이미 됐으니 입실 기한을 못 가져와도 괜찮다
        return Reservation(room_name=d["room"]["name"], seat_name=d["seat"]["code"],
                           begin=d["beginTime"], end=d["endTime"], checkin_deadline=deadline)

    def has_active_reservation(self) -> bool:
        body = self._call("GET", "/1/api/seat-charges")
        return int((body.get("data") or {}).get("totalCount", 0)) > 0
