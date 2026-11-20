from dataclasses import replace

from station.device.fixture import FailOnceBeforeCommit, InjectedCommitFailure
from station.device.recording import DeviceRecording
from station.intake.accept import accept
from station.intake.grading import Grade
from station.intake.repository import IntakeRepository
from station.lookup.sample import get_sample
from tests.support import StationCase


class IntakeTests(StationCase):
    def test_wrong_piece_and_conflicting_replay(self):
        self.open_round(('A', 'B'))
        original = self.reading('A', 48, 10)
        saved = accept(self.connection, 'day/A', original)
        other = self.reading('B', 49, 11)
        for delivered in (other, replace(original, raw_code=61), replace(original, trigger_id='foreign')):
            with self.subTest(delivered=delivered):
                with self.assertRaises(ValueError):
                    accept(self.connection, 'day/A', delivered)
        sample = get_sample(self.connection, saved.receipt.number)
        self.assertEqual((sample.raw_code, sample.tick, sample.grade), (48, 10, Grade.PASS))
        self.assertIsNone(IntakeRepository(self.connection).find_for_piece('day', 'B'))
        self.assertEqual(accept(self.connection, 'day/A', original), saved)

    def test_cross_piece_before_accept(self):
        self.open_round(('A', 'B'))
        first = self.reading('A', 48)
        second = self.reading('B', 61)
        with self.assertRaises(ValueError):
            accept(self.connection, 'day/A', second)
        self.assertEqual(IntakeRepository(self.connection).list_for_round('day'), ())
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM sample_receipts').fetchone(), (0,))
        for original in (first, second):
            result = accept(self.connection, original.order_id, original)
            self.assertEqual(get_sample(self.connection, result.receipt.number).piece, original.piece)

    def test_commit_failure_retries_without_exposure(self):
        self.open_round(('A',))
        terminal = self.reading('A', 48, 10)
        fault = FailOnceBeforeCommit()
        with self.assertRaises(InjectedCommitFailure):
            accept(self.connection, 'day/A', terminal, before_commit=fault)
        self.assertIsNone(IntakeRepository(self.connection).find_for_piece('day', 'A'))
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM sample_receipts').fetchone(), (0,))
        resumed = DeviceRecording(self.connection).get_terminal(terminal.trigger_id)
        self.assertEqual(resumed, terminal)
        saved = accept(self.connection, 'day/A', resumed, before_commit=fault)
        again = accept(self.connection, 'day/A', terminal)
        self.assertIsNotNone(saved.receipt)
        self.assertEqual(saved, again)
        self.assertEqual(self.connection.execute('SELECT trigger_id FROM exposures').fetchall(),
                         [(terminal.trigger_id,)])

    def test_atomic_visibility(self):
        self.open_round(('A', 'B'))
        first = accept(self.connection, 'day/A', self.reading('A', 49))
        terminal = self.reading('B', 48, 12)
        view = self.offline()
        fault = FailOnceBeforeCommit()
        with self.assertRaises(InjectedCommitFailure):
            accept(self.connection, 'day/B', terminal, before_commit=fault)
        self.assertIsNone(IntakeRepository(view).find_for_piece('day', 'B'))
        self.assertEqual(view.execute('SELECT number FROM sample_receipts').fetchall(),
                         [(first.receipt.number,)])
        saved = accept(self.connection, 'day/B', terminal, before_commit=fault)
        self.assertEqual(accept(self.connection, 'day/B', terminal), saved)
        self.assertEqual(view.execute('SELECT COUNT(*) FROM piece_conclusions').fetchone(), (2,))
        self.assertEqual(view.execute('SELECT COUNT(*) FROM sample_receipts').fetchone(), (2,))
        sample = get_sample(view, saved.receipt.number)
        self.assertEqual((sample.tick, sample.trigger_id, sample.raw_code), (12, terminal.trigger_id, 48))
