from station.closing.seal import close
from station.closing.tally import CategoryCounts
from station.intake.accept import accept
from station.intake.grading import Grade
from station.lookup.round import get_round
from station.lookup.sample import get_sample
from station.lookup.verification import ReferenceIntegrityError
from station.rounds.workstation import initialize_station
from tests.support import StationCase


class LookupTests(StationCase):
    def _signed_round(self):
        self.open_round(('A', 'B'))
        reading = self.reading('A', 48, 10)
        signed = accept(self.connection, 'day/A', reading)
        accept(self.connection, 'day/B', self.missing('B'))
        report = close(self.connection, 'day').report
        return signed, report

    def test_source_offline_reopen(self):
        signed, report = self._signed_round()
        self.connection.close()
        offline = self.offline()
        sample = get_sample(offline, signed.receipt.number)
        self.assertEqual((sample.tick, sample.raw_code, sample.grade), (10, 48, Grade.PASS))
        self.assertEqual((sample.source_id, sample.trigger_id),
                         (signed.conclusion.source_id, signed.conclusion.trigger_id))
        reopened = get_round(offline, report.number)
        self.assertEqual(reopened, report)
        self.assertEqual(reopened.counts, CategoryCounts(1, 0, 1))
        with self.assertRaises(KeyError):
            get_sample(offline, 'unknown-receipt')
        with self.assertRaises(KeyError):
            get_round(offline, 'unknown-report')

    def test_repeat_initialization(self):
        signed, report = self._signed_round()
        initialize_station(self.connection)
        self.assertEqual(self.open_round(('A', 'B')).phase, 'sealed')
        self.assertEqual(self.open_round(('A', 'B')).policy.limit, 48)
        with self.assertRaises(ValueError):
            self.open_round(('B', 'A'))
        sample = get_sample(self.connection, signed.receipt.number)
        self.assertEqual((sample.raw_code, sample.tick), (48, 10))
        self.assertEqual(get_round(self.connection, report.number), report)
        self.assertEqual(close(self.connection, 'day').report.number, report.number)

    def test_same_piece_two_rounds(self):
        first, _ = self._signed_round()
        self.open_round(('A',), round_id='next')
        new_reading = self.reading('A', 61, 24, round_id='next')
        next_signed = accept(self.connection, 'next/A', new_reading)
        close(self.connection, 'next')
        self.connection.close()
        offline = self.offline()
        older = get_sample(offline, first.receipt.number)
        newer = get_sample(offline, next_signed.receipt.number)
        self.assertEqual((older.round_id, older.tick, older.raw_code, older.grade), ('day', 10, 48, Grade.PASS))
        self.assertEqual(older.trigger_id, first.conclusion.trigger_id)
        self.assertEqual((newer.round_id, newer.tick, newer.raw_code, newer.grade),
                         ('next', 24, 61, Grade.OVER_LIMIT))
        self.assertEqual(newer.trigger_id, new_reading.trigger_id)

    def test_missing_original(self):
        signed, _ = self._signed_round()
        copy = self.damaged_copy('DELETE FROM device_terminals WHERE source_id = ?',
                                 (signed.conclusion.source_id,))
        with self.assertRaisesRegex(ReferenceIntegrityError, 'missing original'):
            get_sample(copy, signed.receipt.number)
        self.assertEqual(get_sample(self.connection, signed.receipt.number).raw_code, 48)

    def test_missing_report_line(self):
        signed, report = self._signed_round()
        copy = self.damaged_copy('DELETE FROM piece_conclusions WHERE conclusion_id = ?',
                                 (signed.conclusion.conclusion_id,))
        with self.assertRaisesRegex(ReferenceIntegrityError, 'lacks conclusion'):
            get_round(copy, report.number)
        self.assertEqual(get_round(self.connection, report.number), report)

    def test_report_counts(self):
        _, report = self._signed_round()
        copy = self.damaged_copy('UPDATE round_reports SET passed = 2 WHERE number = ?', (report.number,))
        with self.assertRaisesRegex(ReferenceIntegrityError, 'inconsistent category counts'):
            get_round(copy, report.number)
        self.assertEqual(get_round(self.connection, report.number).counts, CategoryCounts(1, 0, 1))
