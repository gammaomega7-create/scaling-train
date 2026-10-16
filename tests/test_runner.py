import unittest
from unittest import mock

from seatbot.client import AuthExpired, ReserveFailed, Seat
from seatbot.config import Config, ConfigError
from seatbot.runner import pick_seat, run


def seat(name, available=True, room="R1"):
    return Seat(room_id=room, seat_id=f"id-{name}", name=name, available=available)


class FakeClient:
    def __init__(self, snapshots, active=False, fail_reserve=()):
        self.snapshots = list(snapshots)  # 조회할 때마다 하나씩 꺼낸다
        self.active = active
        self.fail_reserve = set(fail_reserve)
        self.logins = 0
        self.reserved = []

    def login(self, student_id, password):
        self.logins += 1

    def has_active_reservation(self):
        return self.active

    def list_seats(self, room_id):
        item = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
        if isinstance(item, Exception):
            raise item
        return item

    def reserve(self, s):
        if s.name in self.fail_reserve:
            self.fail_reserve.discard(s.name)
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
    base = dict(student_id="1", password="p", room_ids=["R1"], interval_sec=30, duration_min=10)
    base.update(kw)
    return Config(**base)


@mock.patch("seatbot.runner.notify")
class RunTest(unittest.TestCase):
    def test_waits_then_reserves(self, _):
        client = FakeClient([[seat("A1", False)], [seat("A1", False)], [seat("A1")]])
        clock = Clock()
        got = run(cfg(), client, clock.sleep, clock.now)
        self.assertEqual(got.name, "A1")
        self.assertEqual(client.reserved, [seat("A1")])
        self.assertGreaterEqual(clock.t, 60)

    def test_gives_up_after_duration(self, _):
        client = FakeClient([[seat("A1", False)]])
        clock = Clock()
        self.assertIsNone(run(cfg(duration_min=5), client, clock.sleep, clock.now))
        self.assertEqual(client.reserved, [])

    def test_notify_only_does_not_reserve(self, notify):
        client = FakeClient([[seat("A1")]])
        clock = Clock()
        self.assertEqual(run(cfg(notify_only=True), client, clock.sleep, clock.now).name, "A1")
        self.assertEqual(client.reserved, [])
        self.assertEqual(notify.call_args.args[1], "빈자리 발견")

    def test_stops_if_already_reserved(self, _):
        client = FakeClient([[seat("A1")]], active=True)
        clock = Clock()
        self.assertIsNone(run(cfg(), client, clock.sleep, clock.now))
        self.assertEqual(client.reserved, [])

    def test_relogin_on_expired_session(self, _):
        client = FakeClient([AuthExpired(), [seat("A1")]])
        clock = Clock()
        self.assertIsNotNone(run(cfg(), client, clock.sleep, clock.now))
        self.assertEqual(client.logins, 2)

    def test_retries_when_someone_else_takes_seat(self, _):
        client = FakeClient([[seat("A1")]], fail_reserve={"A1"})
        clock = Clock()
        self.assertEqual(run(cfg(), client, clock.sleep, clock.now).name, "A1")
        self.assertEqual(len(client.reserved), 1)

    def test_aborts_after_repeated_errors(self, _):
        client = FakeClient([ConnectionError("down")])
        clock = Clock()
        with self.assertRaises(ConnectionError):
            run(cfg(), client, clock.sleep, clock.now)


class PickSeatTest(unittest.TestCase):
    def test_preference_order(self):
        seats = [seat("A1"), seat("B2"), seat("C3", False)]
        self.assertEqual(pick_seat(seats, ["C3", "B2", "A1"]).name, "B2")

    def test_no_preference_takes_any(self):
        self.assertEqual(pick_seat([seat("A1", False), seat("A2")], []).name, "A2")

    def test_none_when_preferred_taken(self):
        self.assertIsNone(pick_seat([seat("A1")], ["Z9"]))


class ConfigTest(unittest.TestCase):
    ENV = {"PNU_ID": "1", "PNU_PW": "p", "ROOM_IDS": "R1, R2", "SEATS": "A1,A2"}

    def test_parses(self):
        c = Config.from_env(self.ENV)
        self.assertEqual(c.room_ids, ["R1", "R2"])
        self.assertEqual(c.preferred_seats, ["A1", "A2"])

    def test_rejects_too_fast_interval(self):
        with self.assertRaises(ConfigError):
            Config.from_env({**self.ENV, "INTERVAL_SEC": "5"})

    def test_requires_credentials(self):
        with self.assertRaises(ConfigError):
            Config.from_env({"ROOM_IDS": "R1"})


if __name__ == "__main__":
    unittest.main()
