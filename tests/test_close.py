from station.closing.repository import ClosingRepository
from station.closing.seal import close
from station.closing.tally import CategoryCounts
from station.device.trigger import trigger
from station.intake.accept import accept
from station.intake.grading import Grade
from station.lookup.round import get_round
from station.rounds.authorization import authorize_read
from tests.support import StationCase


class CloseTests(StationCase):
    def test_pending_then_confirmed_missing(self):
        self.open_round(('A', 'B'))
        accept(self.connection, 'day/A', self.reading('A', 48))
        self.start('B')
        waiting = close(self.connection, 'day')
        self.assertEqual(waiting.pending, ('B',))
        self.assertIsNone(waiting.report)
        self.assertIsNone(ClosingRepository(self.connection).find_by_round('day'))
        missing = self.missing('B')
        finished = accept(self.connection, 'day/B', missing)
        self.assertIsNone(finished.receipt)
        self.assertEqual(finished.conclusion.grade, Grade.MISSING)
        self.assertEqual(accept(self.connection, 'day/B', missing), finished)
        result = close(self.connection, 'day')
        self.assertEqual(result.pending, ())
        self.assertEqual(result.report.counts, CategoryCounts(1, 0, 1))
        self.assertEqual(tuple(item.piece for item in result.report.conclusions), ('A', 'B'))

    def test_device_terminal_unaccepted(self):
        self.open_round(('C', 'A', 'B'))
        accept(self.connection, 'day/A', self.reading('A', 49))
        missing = self.missing('B')
        waiting = close(self.connection, 'day')
        self.assertEqual(waiting.pending, ('C', 'B'))
        self.assertIsNone(waiting.report)
        self.assertEqual(self.open_round(('C', 'A', 'B')).phase, 'open')
        accept(self.connection, 'day/B', missing)
        self.assertEqual(close(self.connection, 'day').pending, ('C',))

    def test_close_replay(self):
        self.open_round(('A',))
        original = self.reading('A', 48)
        saved = accept(self.connection, 'day/A', original)
        first = close(self.connection, 'day')
        self.assertIsNotNone(first.report)
        self.assertEqual(close(self.connection, 'day'), first)
        self.assertEqual(self.open_round(('A',)).phase, 'sealed')
        with self.assertRaises(ValueError):
            trigger(self.connection, 'day', 'A', 'day/A')
        with self.assertRaises(ValueError):
            authorize_read(self.connection, 'day', 'A', 'other')
        self.assertEqual(accept(self.connection, 'day/A', original), saved)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM round_reports').fetchone(), (1,))

    def test_summary_in_roster_order(self):
        self.open_round(('C', 'A', 'B'))
        a = accept(self.connection, 'day/A', self.reading('A', 49)).conclusion
        b = accept(self.connection, 'day/B', self.missing('B')).conclusion
        c = accept(self.connection, 'day/C', self.reading('C', 0, 12)).conclusion
        result = close(self.connection, 'day').report
        self.assertEqual(result.conclusions, (c, a, b))
        self.assertEqual(tuple(entry.grade for entry in result.conclusions),
                         (Grade.PASS, Grade.OVER_LIMIT, Grade.MISSING))
        self.assertEqual(result.counts, CategoryCounts(1, 1, 1))
        stored = ClosingRepository(self.connection).find_by_number(result.number)
        self.assertEqual([(line.position, line.piece, line.conclusion_id) for line in stored.lines],
                         [(0, 'C', c.conclusion_id), (1, 'A', a.conclusion_id), (2, 'B', b.conclusion_id)])
        self.assertEqual(get_round(self.connection, result.number), result)

    def test_empty_roster(self):
        self.open_round(())
        result = close(self.connection, 'day')
        self.assertEqual(result.pending, ())
        self.assertEqual(result.report.conclusions, ())
        self.assertEqual(result.report.counts, CategoryCounts(0, 0, 0))
        self.assertEqual(get_round(self.connection, result.report.number), result.report)
