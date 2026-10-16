"""PnuLibraryClient를 가짜 HTTP 세션으로 시험한다.

응답 형식은 2026-10 앱 캡처에서 가져왔고, 개인정보와 토큰은 지어낸 값으로 바꿨다.
"""

import unittest

from seatbot.client import (
    AuthExpired, LoginFailed, PnuLibraryClient, ReserveFailed, Seat,
)

BASE = "https://lib.pusan.ac.kr/pyxis-api"

LOGIN_OK = {"success": True, "code": "success.loggedIn", "message": "로그인되었습니다.",
            "data": {"accessToken": "test-token", "id": 1, "isExpired": False}}
LOGIN_BAD = {"success": False, "code": "error.login.failed", "message": "아이디 또는 비밀번호를 확인하세요."}

ROOMS = {"success": True, "code": "success.retrieved", "data": {"totalCount": 2, "list": [
    {"id": 15, "name": "3열람실-A", "floor": 4,
     "seats": {"total": 120, "occupied": 108, "waiting": 0, "available": 12, "unavailable": 0}},
    {"id": 69, "name": "1열람실", "floor": 2,
     "seats": {"total": 360, "occupied": 360, "waiting": 0, "available": 0, "unavailable": 3}},
]}}

SEATS = {"success": True, "code": "success.retrieved", "data": {"totalCount": 3, "list": [
    {"id": 1859, "room": {"id": 15, "name": "3열람실-A"}, "code": "001", "isActive": True,
     "isReservable": False, "isOccupied": True, "seatChargeState": "CHARGE", "remainingTime": 223},
    {"id": 1908, "room": {"id": 15, "name": "3열람실-A"}, "code": "050", "isActive": True,
     "isReservable": False, "isOccupied": False},
    {"id": 1910, "room": {"id": 15, "name": "3열람실-A"}, "code": "052", "isActive": False,
     "isReservable": False, "isOccupied": False},
]}}

CHARGE_OK = {"success": True, "code": "success.processed", "data": {
    "id": 542395, "patron": {"id": 1, "name": "홍길동"}, "room": {"id": 15, "name": "3열람실-A"},
    "seat": {"id": 1908, "code": "050"},
    "beginTime": "2026-10-16 15:49:00", "endTime": "2026-10-16 19:49:00"}}
MY_CHARGES = {"success": True, "code": "success.retrieved", "data": {"totalCount": 1, "list": [
    {"id": 542395, "state": {"code": "TEMP_CHARGE", "name": "임시배정"},
     "checkinExpiryDate": "2026-10-16 16:04:00", "arrivalConfirmMethods": ["KIOSK", "GATE"]}]}}
NO_RECORD = {"success": True, "code": "success.noRecord", "message": "조회된 결과가 없습니다."}
SEAT_TAKEN = {"success": False, "code": "error.seat.occupied", "message": "이미 사용중인 좌석입니다."}


class FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, routes):
        self.routes = routes  # (method, path) -> body 또는 (body, status)
        self.headers = {}
        self.calls = []

    def request(self, method, url, timeout=None, **kw):
        assert timeout, "모든 요청에 timeout이 있어야 한다"
        path = url.removeprefix(BASE)
        self.calls.append((method, path, kw, dict(self.headers)))
        out = self.routes[(method, path)]
        return FakeResponse(*out) if isinstance(out, tuple) else FakeResponse(out)


def client():
    session = FakeSession({("POST", "/sso/mobile-login"): LOGIN_OK})
    return PnuLibraryClient(session), session


class ClientTest(unittest.TestCase):
    def test_login_sends_mobile_login_and_stores_token(self):
        c, s = client()
        c.login("202600000", "pw")
        method, path, kw, _ = s.calls[0]
        self.assertEqual((method, path), ("POST", "/sso/mobile-login"))
        self.assertEqual(kw["json"], {"loginId": "202600000", "password": "pw",
                                      "isFamilyLogin": False, "isMobile": True})
        self.assertEqual(s.headers["Pyxis-Auth-Token"], "test-token")

    def test_bad_login(self):
        c, s = client()
        s.routes[("POST", "/sso/mobile-login")] = LOGIN_BAD
        with self.assertRaises(LoginFailed):
            c.login("x", "y")

    def test_room_availability_uses_reading_room_summary(self):
        c, s = client()
        s.routes[("GET", "/1/seat-rooms")] = ROOMS
        self.assertEqual(c.room_availability(), {"15": 12, "69": 0})
        params = s.calls[-1][2]["params"]
        self.assertEqual(params, {"roomTypeId": 5, "smufMethodCode": "MOBILE", "buildingId": 2, "branchId": 2})

    def test_list_seats_marks_only_active_free_seats(self):
        c, s = client()
        s.routes[("GET", "/1/api/rooms/15/seats")] = SEATS
        self.assertEqual(c.list_seats("15"), [
            Seat("15", "1859", "001", False), Seat("15", "1908", "050", True), Seat("15", "1910", "052", False),
        ])

    def test_reserve_posts_seat_charge_and_reads_checkin_deadline(self):
        c, s = client()
        s.routes[("POST", "/1/api/seat-charges")] = CHARGE_OK
        s.routes[("GET", "/1/api/seat-charges")] = MY_CHARGES
        c.login("a", "b")
        r = c.reserve(Seat("15", "1908", "050", True))
        post = [x for x in s.calls if x[0] == "POST" and x[1] == "/1/api/seat-charges"][0]
        self.assertEqual(post[2]["json"], {"seatId": 1908, "smufMethodCode": "MOBILE"})
        self.assertEqual(post[3]["Pyxis-Auth-Token"], "test-token")
        self.assertEqual((r.room_name, r.seat_name, r.checkin_deadline), ("3열람실-A", "050", "2026-10-16 16:04:00"))

    def test_reserve_failure(self):
        c, s = client()
        s.routes[("POST", "/1/api/seat-charges")] = SEAT_TAKEN
        with self.assertRaises(ReserveFailed):
            c.reserve(Seat("15", "1908", "050", True))

    def test_has_active_reservation(self):
        c, s = client()
        s.routes[("GET", "/1/api/seat-charges")] = MY_CHARGES
        self.assertTrue(c.has_active_reservation())
        s.routes[("GET", "/1/api/seat-charges")] = NO_RECORD
        self.assertFalse(c.has_active_reservation())

    def test_http_401_means_expired(self):
        c, s = client()
        s.routes[("GET", "/1/seat-rooms")] = ({}, 401)
        with self.assertRaises(AuthExpired):
            c.room_availability()


if __name__ == "__main__":
    unittest.main()
