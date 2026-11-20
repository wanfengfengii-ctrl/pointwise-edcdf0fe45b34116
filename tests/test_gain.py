import sqlite3
from dataclasses import replace

from station.closing.seal import close
from station.closing.tally import CategoryCounts
from station.device.fixture import FailOnceBeforeCommit, InjectedCommitFailure
from station.device.gain_fixture import (GainExposure, GainHead, GainOriginal,
                                         LostExposureReply, load_head)
from station.device.trigger import trigger_interval
from station.device.trigger import trigger
from station.intake.accept import accept
from station.intake.gain import GainStore, canonical_interval, classify
from station.intake.grading import Grade
from station.intake.accept import accept_interval
from station.lookup.round import get_round
from station.lookup.sample import get_interval_sample, get_sample
from station.lookup.verification import ReferenceIntegrityError
from station.rounds.authorization import authorize_read
from tests.support import StationCase


FIXTURE = 'examples/gain.json'


class IntervalHelpers(StationCase):
    def setUp(self):
        super().setUp()
        self.head = load_head(FIXTURE)

    def interval_round(self, pieces, limit=48, round_id='gain'):
        self.open_round(pieces, limit, round_id)
        orders = {}
        for piece in pieces:
            order_id = self.order_id(piece, round_id)
            orders[piece] = authorize_read(self.connection, round_id, piece, order_id,
                                           mode='interval')
        return orders

    def fire(self, piece, gain, round_id='gain'):
        order_id = self.order_id(piece, round_id)
        return trigger_interval(self.connection, self.head, round_id, piece, order_id, gain)

    def deliver(self, piece, gain, round_id='gain', *, commit=None):
        exposure = self.fire(piece, gain, round_id)
        original = self.head.complete(exposure.exposure_id)
        return accept_interval(self.connection, self.head, self.order_id(piece, round_id),
                               original, before_commit=commit), original


class AuthorizationTests(IntervalHelpers):
    def test_modes_share_uniqueness_and_reject_changes(self):
        self.interval_round(('X', 'P'))
        order = authorize_read(self.connection, 'gain', 'X', 'gain/X', mode='interval')
        self.assertEqual(order.mode, 'interval')
        self.assertEqual(authorize_read(self.connection, 'gain', 'X', 'gain/X', mode='interval'),
                         order)
        with self.assertRaises(ValueError):
            authorize_read(self.connection, 'gain', 'X', 'gain/X', mode='point')
        with self.assertRaises(ValueError):
            authorize_read(self.connection, 'gain', 'X', 'other', mode='interval')
        with self.assertRaises(ValueError):
            authorize_read(self.connection, 'gain', 'P', 'gain/X', mode='interval')

    def test_existing_orders_default_to_point(self):
        self.open_round(('A',))
        authorize_read(self.connection, 'day', 'A', 'day/A')
        self.assertEqual(authorize_read(self.connection, 'day', 'A', 'day/A').mode, 'point')
        with self.assertRaises(ValueError):
            authorize_read(self.connection, 'day', 'A', 'day/A', mode='interval')


class TriggerTests(IntervalHelpers):
    def test_low_requires_a_crossing_high_original(self):
        self.interval_round(('X', 'P', 'Y', 'M'))
        with self.assertRaises(ValueError):
            self.fire('P', 'low')
        self.deliver('P', 'high')
        with self.assertRaises(ValueError):
            self.fire('P', 'low')
        self.deliver('Y', 'high')
        with self.assertRaises(ValueError):
            self.fire('Y', 'low')
        self.deliver('M', 'high')
        with self.assertRaises(ValueError):
            self.fire('M', 'low')
        self.deliver('X', 'high')
        first = self.fire('X', 'low')
        self.assertEqual(self.fire('X', 'low'), first)
        self.assertEqual(len(self.head.history()), 5)

    def test_low_may_be_requested_before_the_high_original_is_delivered(self):
        self.interval_round(('X',))
        high = self.fire('X', 'high')
        self.head.complete(high.exposure_id)
        low = self.fire('X', 'low')
        self.assertEqual(low.gain, 'low')
        high_original = self.head.read(high.exposure_id)
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X',
                                         high_original)['status'], 'need_low')
        low_original = self.head.complete(low.exposure_id)
        signed = accept_interval(self.connection, self.head, 'gain/X', low_original)
        self.assertEqual(signed['status'], 'pass')
        self.assertEqual(signed['effective_interval'], (48, 48))

    def test_high_before_low_requested(self):
        """The caller may request the low trigger before delivering the high original."""
        self.interval_round(('X',))
        self.fire('X', 'high')
        original = self.head.read('exposure:1')
        self.assertIsNone(original)
        with self.assertRaises(ValueError):
            self.fire('X', 'low')
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X',
                                         self.head.complete('exposure:1')),
                         {'status': 'need_low', 'receipt': None, 'effective_interval': None})
        low = self.fire('X', 'low')
        self.assertEqual(low.gain, 'low')

    def test_point_entries_refuse_interval_orders(self):
        self.interval_round(('X',))
        with self.assertRaises(ValueError):
            trigger(self.connection, 'gain', 'X', 'gain/X')
        self.fire('X', 'high')
        original = self.head.complete('exposure:1')
        with self.assertRaises(ValueError):
            accept(self.connection, 'gain/X', original)
        self.assertEqual(GainStore(self.connection).get_conclusion(1), None)

    def test_interval_entry_refuses_point_orders(self):
        self.open_round(('A',))
        authorize_read(self.connection, 'day', 'A', 'day/A')
        terminal = self.reading('A', 48)
        with self.assertRaises(ValueError):
            trigger_interval(self.connection, self.head, 'day', 'A', 'day/A', 'high')
        evidence = GainOriginal(GainExposure('h', 'e1', 'A', 'high', 10, 't'), (0, 1))
        with self.assertRaises(ValueError):
            accept_interval(self.connection, self.head, 'day/A', evidence)
        self.assertIsNotNone(get_sample(self.connection, accept(
            self.connection, 'day/A', terminal).receipt.number))


class GradingTests(IntervalHelpers):
    def test_direct_grades_without_low_actions(self):
        self.interval_round(('Y', 'P', 'M'))
        over, over_original = self.deliver('Y', 'high')
        self.assertEqual(over, {'status': 'over_limit', 'receipt': 'interval:1',
                                'effective_interval': (49, None)})
        passed, _ = self.deliver('P', 'high')
        self.assertEqual(passed['status'], 'pass')
        self.assertEqual(passed['effective_interval'], (45, 48))
        missing, _ = self.deliver('M', 'high')
        self.assertEqual(missing, {'status': 'missing', 'receipt': None,
                                   'effective_interval': None})
        self.assertEqual(len(self.head.history()), 3)
        report = close(self.connection, 'gain').report
        self.assertEqual(report.counts, CategoryCounts(1, 1, 1))
        self.assertEqual(tuple(entry.grade for entry in report.conclusions),
                         (Grade.OVER_LIMIT, Grade.PASS, Grade.MISSING))
        sample = get_interval_sample(self.connection, over['receipt'])
        self.assertEqual(sample['originals'], (over_original,))
        self.assertEqual(sample['grade'], 'over_limit')

    def test_crossing_interval_needs_low_and_signs_intersection(self):
        self.interval_round(('X',))
        high, high_original = self.deliver('X', 'high')
        self.assertEqual(high['status'], 'need_low')
        low, low_original = self.deliver('X', 'low')
        self.assertEqual(low['status'], 'pass')
        self.assertEqual(low['effective_interval'], (48, 48))
        sample = get_interval_sample(self.connection, low['receipt'])
        self.assertEqual(sample['originals'], (high_original, low_original))
        self.assertEqual([item.exposure.gain for item in sample['originals']],
                         ['high', 'low'])

    def test_conflict_and_indeterminate_stay_pending(self):
        self.interval_round(('C', 'U', 'N'))
        self.deliver('C', 'high')
        conflict, _ = self.deliver('C', 'low')
        self.assertEqual(conflict, {'status': 'conflict', 'receipt': None,
                                    'effective_interval': None})
        self.deliver('U', 'high')
        self.assertEqual(self.deliver('U', 'low')[0]['status'], 'indeterminate')
        self.deliver('N', 'high')
        self.assertEqual(self.deliver('N', 'low')[0]['status'], 'indeterminate')
        self.assertEqual(close(self.connection, 'gain').pending, ('C', 'U', 'N'))
        self.assertIsNone(GainStore(self.connection).get_conclusion(1))
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM gain_receipts').fetchone(),
                         (0,))

    def test_low_without_high_original_is_refused(self):
        self.interval_round(('X',))
        self.fire('X', 'high')
        low = self.head.fire('X', 'low', 'gain/X/low')
        store = GainStore(self.connection)
        store.insert_exposure(authorize_read(self.connection, 'gain', 'X', 'gain/X',
                                            mode='interval'), low)
        self.connection.commit()
        with self.assertRaises(ValueError):
            accept_interval(self.connection, self.head, 'gain/X',
                            GainOriginal(low, (44, 48)))
        self.assertIsNone(store.find_original('gain/X', 'low'))


class ClassificationTableTests(StationCase):
    def _original(self, interval):
        exposure = GainExposure('head', 'e', 'P', 'high', 10, 't')
        return GainOriginal(exposure, interval)

    def test_high_side_rules(self):
        rows = [((48, 52), 'need_low', None, None), ((45, 48), 'pass', Grade.PASS, (45, 48)),
                ((49, None), 'over_limit', Grade.OVER_LIMIT, (49, None)),
                ((48, 48), 'pass', Grade.PASS, (48, 48)),
                ((49, 60), 'over_limit', Grade.OVER_LIMIT, (49, 60)),
                ((0, 48), 'pass', Grade.PASS, (0, 48))]
        for interval, status, grade, effective in rows:
            with self.subTest(interval=interval):
                self.assertEqual(classify(self._original(interval), None, 48),
                                 (status, grade, effective))

    def test_missing_high_is_not_the_same_as_unfinished(self):
        self.assertEqual(classify(self._original(None), None, 48),
                         ('missing', Grade.MISSING, None))

    def test_intersection_rules(self):
        high = self._original((48, 52))
        rows = [((44, 48), 'pass', Grade.PASS, (48, 48)), ((53, 56), 'conflict', None, None),
                ((47, 51), 'indeterminate', None, None),
                ((49, 60), 'over_limit', Grade.OVER_LIMIT, (49, 52)),
                ((48, None), 'indeterminate', None, None), (None, 'indeterminate', None, None),
                ((0, None), 'indeterminate', None, None)]
        for interval, status, grade, effective in rows:
            with self.subTest(interval=interval):
                low = GainOriginal(GainExposure('head', 'e2', 'P', 'low', 11, 't2'), interval)
                self.assertEqual(classify(high, low, 48), (status, grade, effective))


class IntervalValueTests(IntervalHelpers):
    def test_boolean_endpoints_are_invalid(self):
        self.interval_round(('X',))
        self.fire('X', 'high')
        original = self.head.complete('exposure:1')
        for interval in ((True, 52), (48, False), (48, 47)):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    accept_interval(self.connection, self.head, 'gain/X',
                                    replace(original, interval=interval))
        self.assertIsNone(GainStore(self.connection).find_original('gain/X', 'high'))

    def test_interval_endpoint_validation(self):
        limit = -(1 << 18000)
        self.assertEqual(canonical_interval((limit, None)), (limit, None))
        for value in ((1,), (1, 2, 3), [1, 2], (1.0, 2), (2, 1), (1, True)):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonical_interval(value)

    def test_huge_high_interval_signs_and_round_trips(self):
        limit = -(1 << 18000)
        self.head = GainHead('gain-head-01', {'X': {'high': [limit + 1, limit + (1 << 100)],
                                                    'low': None}})
        self.open_round(('X',), limit, 'huge')
        authorize_read(self.connection, 'huge', 'X', 'huge/X', mode='interval')
        exposure = trigger_interval(self.connection, self.head, 'huge', 'X', 'huge/X', 'high')
        original = self.head.complete(exposure.exposure_id)
        signed = accept_interval(self.connection, self.head, 'huge/X', original)
        self.assertEqual(signed['status'], 'over_limit')
        sample = get_interval_sample(self.connection, signed['receipt'])
        self.assertEqual(sample['effective_interval'],
                         (limit + 1, limit + (1 << 100)))
        self.assertEqual(sample['originals'][0].interval, (limit + 1, limit + (1 << 100)))


class IntakeTests(IntervalHelpers):
    def test_low_original_may_arrive_before_the_high_original(self):
        self.interval_round(('X',))
        high_exposure = self.fire('X', 'high')
        high_original = self.head.complete(high_exposure.exposure_id)
        low_exposure = self.fire('X', 'low')
        low_original = self.head.complete(low_exposure.exposure_id)
        early = accept_interval(self.connection, self.head, 'gain/X', low_original)
        self.assertEqual(early, {'status': 'need_low', 'receipt': None,
                                 'effective_interval': None})
        signed = accept_interval(self.connection, self.head, 'gain/X', high_original)
        self.assertEqual(signed['status'], 'pass')
        self.assertEqual(signed['effective_interval'], (48, 48))
        sample = get_interval_sample(self.connection, signed['receipt'])
        self.assertEqual(sample['originals'], (high_original, low_original))
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X', low_original),
                         signed)

    def test_low_original_without_a_completed_high_is_refused(self):
        self.interval_round(('X',))
        high = self.fire('X', 'high')
        self.head.complete(high.exposure_id)
        low = self.fire('X', 'low')
        low_original = self.head.complete(low.exposure_id)
        # The station has the low physical original but no accepted high terminal yet.
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X', low_original),
                         {'status': 'need_low', 'receipt': None, 'effective_interval': None})
        self.assertIsNone(GainStore(self.connection).find_original('gain/X', 'high'))

    def test_uncompleted_original_is_refused(self):
        self.interval_round(('X',))
        high = self.fire('X', 'high')
        with self.assertRaises(ValueError):
            accept_interval(self.connection, self.head, 'gain/X',
                            GainOriginal(high, (48, 52)))
        self.assertIsNone(GainStore(self.connection).find_original('gain/X', 'high'))

    def test_wrong_and_foreign_originals_are_refused(self):
        self.interval_round(('X', 'P'))
        first = self.fire('X', 'high')
        high = self.head.complete(first.exposure_id)
        second = self.fire('P', 'high')
        foreign = self.head.complete(second.exposure_id)
        forged = GainOriginal(replace(high.exposure, tick=99), high.interval)
        for delivered in (foreign, forged, replace(high, interval=(0, 1))):
            with self.subTest(delivered=delivered):
                with self.assertRaises(ValueError):
                    accept_interval(self.connection, self.head, 'gain/X', delivered)
        self.assertIsNone(GainStore(self.connection).find_original('gain/X', 'high'))
        self.assertIsNone(GainStore(self.connection).get_conclusion(1))

    def test_replay_returns_the_current_state(self):
        self.interval_round(('X',))
        high, high_original = self.deliver('X', 'high')
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X', high_original),
                         high)
        low, low_original = self.deliver('X', 'low')
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X', high_original),
                         low)
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X', low_original), low)
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM gain_receipts').fetchone(),
                         (1,))

    def test_missing_high_seals_the_piece_and_replays(self):
        self.interval_round(('M',))
        result, original = self.deliver('M', 'high')
        self.assertEqual(result, {'status': 'missing', 'receipt': None,
                                  'effective_interval': None})
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/M', original), result)
        report = close(self.connection, 'gain').report
        self.assertEqual(report.counts, CategoryCounts(0, 0, 1))
        with self.assertRaises(ValueError):
            self.fire('M', 'high')
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM gain_receipts').fetchone(),
                         (0,))

    def test_commit_failure_leaves_no_result_and_resumes(self):
        self.interval_round(('X', 'P'))
        exposure = self.fire('X', 'high')
        original = self.head.complete(exposure.exposure_id)
        fault = FailOnceBeforeCommit()
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X', original,
                                         before_commit=fault)['status'], 'need_low')
        self.assertFalse(fault.failed)
        direct = self.fire('P', 'high')
        direct_original = self.head.complete(direct.exposure_id)
        with self.assertRaises(InjectedCommitFailure):
            accept_interval(self.connection, self.head, 'gain/P', direct_original,
                            before_commit=fault)
        self.assertIsNone(GainStore(self.connection).get_conclusion(2))
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/P', direct_original,
                                         before_commit=fault)['status'], 'pass')
        low_exposure = self.fire('X', 'low')
        low_original = self.head.complete(low_exposure.exposure_id)
        faulty = FailOnceBeforeCommit()
        with self.assertRaises(InjectedCommitFailure):
            accept_interval(self.connection, self.head, 'gain/X', low_original,
                            before_commit=faulty)
        self.assertIsNone(GainStore(self.connection).find_conclusion_for_piece('gain', 'X'))
        self.assertEqual(self.connection.execute('SELECT COUNT(*) FROM gain_receipts').fetchone(),
                         (1,))
        saved = accept_interval(self.connection, self.head, 'gain/X', low_original,
                                before_commit=faulty)
        self.assertEqual(accept_interval(self.connection, self.head, 'gain/X', low_original),
                         saved)

    def test_mixed_round_keeps_point_and_interval_apart(self):
        self.open_round(('A', 'P', 'X'), 48, 'mix')
        authorize_read(self.connection, 'mix', 'A', 'mix/A')
        authorize_read(self.connection, 'mix', 'P', 'mix/P', mode='interval')
        authorize_read(self.connection, 'mix', 'X', 'mix/X', mode='interval')
        point = accept(self.connection, 'mix/A', self.reading('A', 49, round_id='mix'))
        self.deliver('P', 'high', round_id='mix')
        saved, _ = self.deliver('X', 'high', round_id='mix')
        self.assertEqual(saved['status'], 'need_low')
        low, _ = self.deliver('X', 'low', round_id='mix')
        self.assertEqual(low['status'], 'pass')
        report = close(self.connection, 'mix').report
        self.assertEqual(report.counts, CategoryCounts(2, 1, 0))
        self.assertEqual([entry.piece for entry in report.conclusions], ['A', 'P', 'X'])
        point_sample = get_sample(self.connection, point.receipt.number)
        interval_sample = get_interval_sample(self.connection, low['receipt'])
        self.assertEqual(point_sample.raw_code, 49)
        self.assertEqual(interval_sample['effective_interval'], (48, 48))
        get_round(self.connection, report.number)


class LossTests(IntervalHelpers):
    def _reconnect(self):
        self.connection.close()
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)

    def test_lost_before_reply_may_fire_again(self):
        self.interval_round(('X',))
        self.deliver('X', 'high')
        history = len(self.head.history())
        self.head.lose_next_reply('before')
        with self.assertRaises(LostExposureReply):
            self.fire('X', 'low')
        self.assertEqual(len(self.head.history()), history)
        self.assertIsNone(GainStore(self.connection).find_exposure('gain/X', 'low'))
        self._reconnect()
        low = self.fire('X', 'low')
        self.assertEqual(len(self.head.history()), history + 1)
        result = accept_interval(self.connection, self.head, 'gain/X',
                                 self.head.complete(low.exposure_id))
        self.assertEqual(result['status'], 'pass')

    def test_lost_after_reply_reuses_the_exposure(self):
        self.interval_round(('X',))
        self.deliver('X', 'high')
        self.head.lose_next_reply('after')
        with self.assertRaises(LostExposureReply):
            self.fire('X', 'low')
        fired = self.head.history()[-1]
        self.assertEqual(len(self.head.history()), 2)
        self.assertIsNone(GainStore(self.connection).find_exposure('gain/X', 'low'))
        self._reconnect()
        recovered = trigger_interval(self.connection, self.head, 'gain', 'X', 'gain/X', 'low')
        self.assertEqual(recovered, fired)
        self.assertEqual(len(self.head.history()), 2)
        result = accept_interval(self.connection, self.head, 'gain/X',
                                 self.head.complete(recovered.exposure_id))
        self.assertEqual(result['status'], 'pass')


class LookupTests(IntervalHelpers):
    def _sealed(self):
        self.interval_round(('X', 'Y', 'M'))
        self.deliver('X', 'high')
        saved, _ = self.deliver('X', 'low')
        over, _ = self.deliver('Y', 'high')
        self.deliver('M', 'high')
        return close(self.connection, 'gain').report, saved, over

    def test_offline_sources_and_errors(self):
        report, saved, over = self._sealed()
        offline = self.offline()
        sample = get_interval_sample(offline, saved['receipt'])
        self.assertEqual(sample['effective_interval'], (48, 48))
        self.assertEqual([item.exposure.gain for item in sample['originals']],
                         ['high', 'low'])
        self.assertEqual(get_round(offline, report.number), report)
        with self.assertRaises(KeyError):
            get_interval_sample(offline, 'missing')
        with self.assertRaises(KeyError):
            get_interval_sample(offline, 'interval:999')
        point_receipt = self._point_receipt()
        with self.assertRaises(ValueError):
            get_interval_sample(offline, point_receipt)
        with self.assertRaises(ValueError):
            get_sample(offline, saved['receipt'])

    def _point_receipt(self):
        self.open_round(('A',), round_id='point')
        saved = accept(self.connection, 'point/A', self.reading('A', 48, round_id='point'))
        close(self.connection, 'point')
        return saved.receipt.number

    def test_missing_evidence_is_an_integrity_error(self):
        report, saved, _ = self._sealed()
        copy = self.damaged_copy(
            'DELETE FROM gain_originals WHERE order_id = ? AND gain = ?', ('gain/X', 'low'))
        with self.assertRaises(ReferenceIntegrityError):
            get_interval_sample(copy, saved['receipt'])
        copy2 = self.damaged_copy(
            'UPDATE interval_conclusions SET grade = ? WHERE order_id = ?',
            ('over_limit', 'gain/X'))
        with self.assertRaises(ReferenceIntegrityError):
            get_interval_sample(copy2, saved['receipt'])
        missing_originals = self.damaged_copy(
            'DELETE FROM gain_originals WHERE order_id = ? AND gain = ?', ('gain/X', 'high'))
        with self.assertRaises(ReferenceIntegrityError):
            get_interval_sample(missing_originals, saved['receipt'])
