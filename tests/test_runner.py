import unittest
from unittest import mock

from seatbot.client import AuthExpired, ReserveFailed, Seat
from seatbot.config import (
    Config, ConfigError, Room, SeatPref, parse_seat_prefs, resolve_rooms,
)
from seatbot.runner import MAX_BACKOFF_SEC, candidates, next_delay, run

R1 = Room("제1열람실", "101")
R2 = Room("제2열람실", "102")
CATALOG = {R1.name: R1.id, R2.name: R2.id, "노트북열람실": "103"}


def seat(name, available=True, room=R1):
    return Seat(room_id=room.id, seat_id=f"{room.id}-{name}", name=name, available=available)


class FakeClient:
    """열람실 ID별로 조회 결과를 순서대로 돌려준다. 마지막 결과는 계속 반복한다."""

    def __init__(self, snapshots, active=False, fail_reserve=()):
        self.snapshots = {k: list(v) for k, v in snapshots.items()}
        self.active = active
        self.fail_reserve = set(fail_reserve)
        self.logins = 0
        self.reserved = []
        self.queried = []

    def login(self, student_id, password):
        self.logins += 1

    def has_active_reservation(self):
        return self.active

    def list_seats(self, room_id):
        self.queried.append(room_id)
        queue = self.snapshots[room_id]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    def reserve(self, s):
        if s.seat_id in self.fail_reserve:
            self.fail_reserve.discard(s.seat_id)
            raise ReserveFailed("taken")
        self.reserved.append(s)


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

    def test_only_queries_selected_rooms(self, _):
        client = FakeClient({"101": [[seat("A1", False)]], "102": [[seat("B1", room=R2)]],
                             "103": [[seat("C1", room=Room("노트북열람실", "103"))]]})
        got, _ = self.go(cfg(rooms=[R1, R2]), client)
        self.assertEqual(got.name, "B1")
        self.assertNotIn("103", client.queried)

    def test_room_order_is_priority(self, _):
        client = FakeClient({"101": [[seat("A1")]], "102": [[seat("B1", room=R2)]]})
        got, _ = self.go(cfg(rooms=[R2, R1]), client)
        self.assertEqual(got.name, "B1")

    def test_notify_only_does_not_reserve(self, notify):
        client = FakeClient({"101": [[seat("A1")]]})
        got, _ = self.go(cfg(notify_only=True), client)
        self.assertEqual(got.name, "A1")
        self.assertEqual(client.reserved, [])
        self.assertEqual(notify.call_args.args[1], "빈자리 발견")
        self.assertIn("제1열람실 A1", notify.call_args.args[2])

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
