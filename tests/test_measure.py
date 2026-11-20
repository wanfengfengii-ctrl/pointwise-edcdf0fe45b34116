from dataclasses import replace

from station.device.fixture import DeliverySchedule, OpticalFixture, OpticalObservation
from station.device.optical import apply_observation
from station.device.recording import DeviceRecording
from station.device.terminal import RawReading
from station.device.trigger import trigger
from station.intake.accept import accept
from station.intake.grading import Grade
from station.lookup.sample import get_sample
from station.rounds.authorization import authorize_read
from station.rounds.opening import open
from tests.support import StationCase


class MeasureTests(StationCase):
    def test_threshold_equality_and_excess(self):
        self.open_round(('A', 'B'))
        first = accept(self.connection, 'day/A', self.reading('A', 48, 10))
        second = accept(self.connection, 'day/B', self.reading('B', 49, 12))
        self.assertEqual((first.conclusion.grade, second.conclusion.grade), (Grade.PASS, Grade.OVER_LIMIT))
        self.assertIsNotNone(first.receipt)
        self.assertIsNotNone(second.receipt)
        self.assertNotEqual(first.receipt.number, second.receipt.number)
        for result, tick, raw_code in ((first, 10, 48), (second, 12, 49)):
            sample = get_sample(self.connection, result.receipt.number)
            self.assertEqual((sample.tick, sample.raw_code, sample.trigger_id),
                             (tick, raw_code, result.conclusion.trigger_id))

    def test_zero_is_reading(self):
        self.open_round(('A',))
        terminal = self.reading('A', 0)
        self.assertIsInstance(terminal, RawReading)
        result = accept(self.connection, 'day/A', terminal)
        sample = get_sample(self.connection, result.receipt.number)
        self.assertEqual((sample.raw_code, sample.grade), (0, Grade.PASS))

    def test_physical_tick(self):
        self.open_round(('A',))
        fixture = OpticalFixture((OpticalObservation('right', 9, 'empty'),
                                  OpticalObservation('left', 9, 'empty'),
                                  OpticalObservation('left', 10, 'reading', 48),
                                  OpticalObservation('right', 11, 'reading', 61)))
        exposure = self.start('A')
        blank, reading = fixture.observations_for('left')
        self.assertIsNone(apply_observation(self.connection, exposure, blank))
        terminal = apply_observation(self.connection, exposure, reading)
        schedule = DeliverySchedule()
        schedule.schedule(31, terminal)
        self.assertEqual(schedule.take_due(30), ())
        delivered, = schedule.take_due(31)
        result = accept(self.connection, exposure.order_id, delivered)
        self.assertEqual(get_sample(self.connection, result.receipt.number).tick, 10)

    def test_reopen_reuses_frozen_roster(self):
        expected = self.open_round(('C', 'A', 'B'))
        self.assertEqual(self.open_round(['C', 'A', 'B']), expected)
        with self.assertRaises(ValueError):
            self.open_round(('C', 'A', 'B'), 50)
        actual = self.open_round(('C', 'A', 'B'))
        self.assertEqual(actual.roster.pieces, ('C', 'A', 'B'))
        self.assertEqual(actual.policy.limit, 48)

    def test_frozen_limit(self):
        self.open_round(('A',))
        with self.assertRaises(ValueError):
            self.open_round(('A',), 50)
        result = accept(self.connection, 'day/A', self.reading('A', 49))
        self.assertEqual(result.conclusion.grade, Grade.OVER_LIMIT)
        self.assertEqual(get_sample(self.connection, result.receipt.number).grade, Grade.OVER_LIMIT)

    def test_roster_order(self):
        baseline = self.open_round(('C', 'A', 'B'))
        with self.assertRaises(ValueError):
            self.open_round(('A', 'C', 'B'))
        with self.assertRaises(ValueError):
            self.open_round(('C', 'A', 'C'))
        self.assertEqual(self.open_round(('C', 'A', 'B')), baseline)

    def test_unlisted_piece_never_triggers(self):
        self.open_round(('A',))
        with self.assertRaises(ValueError):
            authorize_read(self.connection, 'day', 'X', 'day/X')
        with self.assertRaises(ValueError):
            trigger(self.connection, 'day', 'X', 'day/X')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM exposures').fetchone(), (0,))

    def test_trigger_replay(self):
        self.open_round(('A', 'B'))
        original = self.start('A')
        self.assertEqual(trigger(self.connection, 'day', 'A', 'day/A'), original)
        with self.assertRaises(ValueError):
            trigger(self.connection, 'day', 'B', 'day/A')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM exposures').fetchone(), (1,))

    def test_no_fallback_trigger(self):
        self.open_round(('A',))
        authorize_read(self.connection, 'day', 'A', 'day/A')
        with self.assertRaises(ValueError):
            trigger(self.connection, 'day', 'X', 'day/A')
        recordings = DeviceRecording(self.connection)
        self.assertIsNone(recordings.find_exposure('day/A'))
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM exposures').fetchone(), (0,))
        original = trigger(self.connection, 'day', 'A', 'day/A')
        self.assertEqual(recordings.find_exposure('day/A'), original)

    def test_exact_integers_outside_sqlite_integer_range(self):
        limit = -(1 << 18000)
        self.open_round(('A', 'B'), limit)
        first = accept(self.connection, 'day/A', self.reading('A', limit, -(1 << 100)))
        second = accept(self.connection, 'day/B', self.reading('B', limit + 1, 1 << 100))
        sample_a = get_sample(self.connection, first.receipt.number)
        sample_b = get_sample(self.connection, second.receipt.number)
        self.assertEqual((sample_a.tick, sample_a.grade), (-(1 << 100), Grade.PASS))
        self.assertEqual((sample_b.tick, sample_b.grade), (1 << 100, Grade.OVER_LIMIT))
        self.assertTrue(sample_a.raw_code == limit)
        self.assertTrue(sample_b.raw_code == limit + 1)

    def test_invalid_policy_and_conflicting_device_terminal(self):
        with self.assertRaises(ValueError):
            open(self.connection, 'day', ('A',), True)
        self.open_round(('A',))
        exposure = self.start('A')
        observation = OpticalObservation('left', 10, 'reading', 48)
        first = apply_observation(self.connection, exposure, observation)
        self.assertEqual(apply_observation(self.connection, exposure, observation), first)
        with self.assertRaises(ValueError):
            apply_observation(self.connection, exposure, replace(observation, raw_code=49))
        self.assertEqual(DeviceRecording(self.connection).get_terminal(exposure.trigger_id), first)
