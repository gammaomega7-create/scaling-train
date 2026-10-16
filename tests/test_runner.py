import unittest
from unittest import mock

from seatbot.client import AuthExpired, LoginFailed, Reservation, ReserveFailed, Seat
from seatbot.config import (
    Config, ConfigError, Room, SeatPref, parse_seat_prefs, resolve_rooms,
)
from seatbot.runner import MAX_BACKOFF_SEC, candidates, check, next_delay, run, same_seat

R1 = Room("제1열람실", "101")
R2 = Room("제2열람실", "102")
CATALOG = {R1.name: R1.id, R2.name: R2.id, "노트북열람실": "103"}


def seat(name, available=True, room=R1):
    return Seat(room_id=room.id, seat_id=f"{room.id}-{name}", name=name, available=available)


class FakeClient:
    """열람실 ID별로 "한 번 확인할 때마다" 바뀌는 좌석 상태를 흉내 낸다.

    room_availability()를 부를 때마다 다음 회차로 넘어가고, 마지막 회차는 계속 반복한다.
    회차 값이 예외면 그 회차의 조회에서 그 예외를 던진다.
    """

    def __init__(self, rounds, active=False, fail_reserve=(), bad_login=False):
        self.rounds = rounds
        self.round = -1
        self.active = active
        self.fail_reserve = set(fail_reserve)
        self.bad_login = bad_login
        self.logins = 0
        self.reserved = []
        self.queried = []

    def _current(self, room_id):
        q = self.rounds[room_id]
        return q[min(self.round, len(q) - 1)]

    def login(self, student_id, password):
        self.logins += 1
        if self.bad_login:
            raise LoginFailed("error.login")

    def has_active_reservation(self):
        return self.active

    def room_availability(self):
        self.round += 1
        out = {}
        for room_id in self.rounds:
            item = self._current(room_id)
            if isinstance(item, Exception):
                raise item
            out[room_id] = sum(s.available for s in item)
        return out

    def list_seats(self, room_id):
        self.queried.append(room_id)
        return self._current(room_id)

    def reserve(self, s):
        if s.seat_id in self.fail_reserve:
            self.fail_reserve.discard(s.seat_id)
            raise ReserveFailed("taken")
        self.reserved.append(s)
        return Reservation("방", s.name, "2026-10-16 15:49:00", "2026-10-16 19:49:00", "2026-10-16 16:04:00")


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, sec):
        self.t += sec


def cfg(**kw):
    base = dict(student_id="1", password="p", rooms=[R1], interval_sec=3, duration_min=10)
    base.update(kw)
    return Config(**base)


@mock.patch("seatbot.runner.notify")
class RunTest(unittest.TestCase):
    def go(self, config, client):
        clock = Clock()
        return run(config, client, clock.sleep, clock.now), clock

    def test_waits_then_reserves(self, _):
        client = FakeClient({"101": [[seat("A1", False)], [seat("A1", False)], [seat("A1")]]})
        got, clock = self.go(cfg(), client)
        self.assertEqual(got.name, "A1")
        self.assertEqual(client.reserved, [seat("A1")])
        self.assertGreaterEqual(clock.t, 6)

    def test_gives_up_after_duration(self, _):
        client = FakeClient({"101": [[seat("A1", False)]]})
        got, _ = self.go(cfg(duration_min=5), client)
        self.assertIsNone(got)
        self.assertEqual(client.reserved, [])

    def test_only_opens_selected_rooms_with_free_seats(self, _):
        client = FakeClient({"101": [[seat("A1", False)]], "102": [[seat("B1", room=R2)]],
                             "103": [[seat("C1", room=Room("노트북열람실", "103"))]]})
        got, _ = self.go(cfg(rooms=[R1, R2]), client)
        self.assertEqual(got.name, "B1")
        # 남은 좌석이 0인 제1열람실과 고르지 않은 노트북열람실은 도면을 열지 않는다.
        self.assertEqual(client.queried, ["102"])

    def test_success_message_has_checkin_deadline(self, notify):
        client = FakeClient({"101": [[seat("A1")]]})
        self.go(cfg(), client)
        self.assertIn("16:04까지", notify.call_args.args[2])

    def test_stops_when_seat_taken_meanwhile_by_user(self, _):
        client = FakeClient({"101": [[seat("A1")]]}, fail_reserve={"101-A1"})
        client.has_active_reservation = iter([False, True]).__next__
        got, _ = self.go(cfg(), client)
        self.assertIsNone(got)
        self.assertEqual(client.reserved, [])

    def test_login_failure_aborts(self, notify):
        client = FakeClient({"101": [[seat("A1")]]}, bad_login=True)
        with self.assertRaises(LoginFailed):
            self.go(cfg(), client)
        self.assertEqual(notify.call_args.args[1], "로그인 실패")

    def test_check_mode_reports_counts(self, notify):
        client = FakeClient({"101": [[seat("A1"), seat("A2", False)]]})
        self.assertTrue(check(cfg(), client))
        self.assertIn("제1열람실: 남은 좌석 1", notify.call_args.args[2])
        self.assertEqual(client.reserved, [])

    def test_room_order_is_priority(self, _):
        client = FakeClient({"101": [[seat("A1")]], "102": [[seat("B1", room=R2)]]})
        got, _ = self.go(cfg(rooms=[R2, R1]), client)
        self.assertEqual(got.name, "B1")

    def test_notify_only_does_not_reserve(self, notify):
        client = FakeClient({"101": [[seat("A1")]]})
        got, _ = self.go(cfg(mode="notify"), client)
        self.assertEqual(got.name, "A1")
        self.assertEqual(client.reserved, [])
        self.assertEqual(notify.call_args.args[1], "빈자리 발견")
        self.assertIn("제1열람실 A1번", notify.call_args.args[2])

    def test_stops_if_already_reserved(self, _):
        client = FakeClient({"101": [[seat("A1")]]}, active=True)
        got, _ = self.go(cfg(), client)
        self.assertIsNone(got)
        self.assertEqual(client.reserved, [])

    def test_relogin_on_expired_session(self, _):
        client = FakeClient({"101": [AuthExpired(), [seat("A1")]]})
        got, _ = self.go(cfg(), client)
        self.assertIsNotNone(got)
        self.assertEqual(client.logins, 2)

    def test_tries_next_candidate_when_seat_taken(self, _):
        client = FakeClient({"101": [[seat("A1"), seat("A2")]]}, fail_reserve={"101-A1"})
        got, _ = self.go(cfg(), client)
        self.assertEqual(got.name, "A2")

    def test_aborts_after_repeated_errors(self, _):
        client = FakeClient({"101": [ConnectionError("down")]})
        with self.assertRaises(ConnectionError):
            self.go(cfg(), client)


class CandidatesTest(unittest.TestCase):
    ROOMS = [(R1, [seat("A1"), seat("A2"), seat("A3", False)]),
             (R2, [seat("A1", room=R2), seat("B5", room=R2)])]

    def names(self, prefs):
        return [(r.name, s.name) for r, s in candidates(self.ROOMS, prefs)]

    def test_no_preference_takes_any_in_room_order(self):
        self.assertEqual(self.names([])[0], ("제1열람실", "A1"))
        self.assertEqual(len(self.names([])), 4)

    def test_plain_seat_matches_any_selected_room(self):
        self.assertEqual(self.names([SeatPref("A1")]), [("제1열람실", "A1"), ("제2열람실", "A1")])

    def test_room_scoped_seat(self):
        self.assertEqual(self.names([SeatPref("A1", "제2열람실")]), [("제2열람실", "A1")])

    def test_room_wildcard(self):
        self.assertEqual(self.names([SeatPref("*", "제2열람실")]),
                         [("제2열람실", "A1"), ("제2열람실", "B5")])

    def test_preference_order_across_rooms(self):
        prefs = [SeatPref("B5", "제2열람실"), SeatPref("A2", "제1열람실")]
        self.assertEqual(self.names(prefs), [("제2열람실", "B5"), ("제1열람실", "A2")])

    def test_seat_number_ignores_leading_zeros(self):
        rooms = [(R1, [seat("050"), seat("051")])]
        self.assertEqual([s.name for _, s in candidates(rooms, [SeatPref("50")])], ["050"])
        self.assertTrue(same_seat("7", "007"))
        self.assertFalse(same_seat("70", "007"))

    def test_taken_preferred_seat_is_skipped(self):
        self.assertEqual(self.names([SeatPref("A3")]), [])


class NextDelayTest(unittest.TestCase):
    def test_normal_delay_stays_near_interval(self):
        for _ in range(100):
            self.assertTrue(2 <= next_delay(2, 0) <= 3)

    def test_backs_off_on_errors(self):
        self.assertGreaterEqual(next_delay(2, 3), 16)
        self.assertLessEqual(next_delay(2, 10), MAX_BACKOFF_SEC + 1)


class RoomConfigTest(unittest.TestCase):
    def test_resolves_names_and_ids_in_order(self):
        self.assertEqual(resolve_rooms(["제2열람실", "101"], CATALOG), [R2, R1])

    def test_unknown_room_lists_choices(self):
        with self.assertRaises(ConfigError) as e:
            resolve_rooms(["제9열람실"], CATALOG)
        self.assertIn("노트북열람실", str(e.exception))

    def test_empty_catalog_uses_raw_ids(self):
        self.assertEqual(resolve_rooms(["101"], {}), [Room("101", "101")])

    def test_seat_prefs(self):
        prefs = parse_seat_prefs("A1, 제2열람실:B5, 제1열람실:*", [R1, R2])
        self.assertEqual(prefs, [SeatPref("A1"), SeatPref("B5", "제2열람실"), SeatPref("*", "제1열람실")])

    def test_seat_pref_for_unselected_room_is_error(self):
        with self.assertRaises(ConfigError):
            parse_seat_prefs("노트북열람실:C1", [R1])


class ConfigTest(unittest.TestCase):
    ENV = {"PNU_ID": "1", "PNU_PW": "p", "ROOMS": "제1열람실, 제2열람실", "SEATS": "A1,제2열람실:B5"}

    def test_parses(self):
        c = Config.from_env(self.ENV, catalog=CATALOG)
        self.assertEqual(c.rooms, [R1, R2])
        self.assertEqual(c.preferred_seats, [SeatPref("A1"), SeatPref("B5", "제2열람실")])

    def test_accepts_fractional_interval(self):
        c = Config.from_env({**self.ENV, "INTERVAL_SEC": "2.5"}, catalog=CATALOG)
        self.assertEqual(c.interval_sec, 2.5)

    def test_rejects_too_fast_interval(self):
        with self.assertRaises(ConfigError):
            Config.from_env({**self.ENV, "INTERVAL_SEC": "1"}, catalog=CATALOG)

    def test_requires_credentials(self):
        with self.assertRaises(ConfigError):
            Config.from_env({"ROOMS": "제1열람실"}, catalog=CATALOG)


if __name__ == "__main__":
    unittest.main()
