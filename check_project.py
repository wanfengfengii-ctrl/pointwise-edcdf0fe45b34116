"""Run this project's full unittest suite from the repository root."""
import sys
import unittest

from station.device.gain_fixture import check_fixture, load_head
from station.rounds.gain_demo import run_gain_scenario
from tempfile import TemporaryDirectory
from pathlib import Path


def run_suite() -> unittest.TestResult:
    suite = unittest.defaultTestLoader.discover('tests', top_level_dir='.')
    if not suite.countTestCases():
        print('No tests discovered in tests/', file=sys.stderr)
        result = unittest.TestResult()
        result.testsRun = 0
        return result
    return unittest.TextTestRunner(verbosity=2).run(suite)


def run_capture_checks() -> None:
    """Exercise the raw gain head and the scripted interval example end to end."""
    checked = check_fixture(load_head('examples/gain.json'))
    if checked['counts_after_lost_replies'] != [1, 2]:
        raise RuntimeError('gain fixture lost-reply checks failed')
    with TemporaryDirectory(prefix='optical-gain-check-') as directory:
        evidence = run_gain_scenario(Path(directory) / 'station.sqlite3',
                                     'examples/gain-shift.json', echo=False)
    rounds = {entry['round_id']: entry['actual'] for entry in evidence['rounds']}
    if rounds['gain-pending']['pending'] != ['C', 'U', 'N']:
        raise RuntimeError('undecided pieces must stay pending without receipts')
    after = rounds['gain-lost-after']
    if not after['rejections'] or not after['recovered_exposure_id']:
        raise RuntimeError('wrong-original rejection or lost-reply recovery did not run')
    if after['samples']['X']['effective_interval'] != [48, 48]:
        raise RuntimeError('the recovered interval did not sign the expected intersection')
    print('GAIN CAPTURE CHECK OK')


def main():
    result = run_suite()
    for status, failed_cases in (
        ('FAIL', (test for test, _ in result.failures)),
        ('ERROR', (test for test, _ in result.errors)),
        ('UNEXPECTED_SUCCESS', result.unexpectedSuccesses),
    ):
        for test in failed_cases:
            print(f'{status} {test.id()}')
    successful = result.testsRun > 0 and result.wasSuccessful()
    print(f'TEST SUMMARY total={result.testsRun} failures={len(result.failures)} '
          f'errors={len(result.errors)} skipped={len(result.skipped)} '
          f'expected_failures={len(result.expectedFailures)} '
          f'unexpected_successes={len(result.unexpectedSuccesses)} '
          f'result={"PASS" if successful else "FAIL"}')
    if not successful:
        return 1
    try:
        run_capture_checks()
    except Exception as error:  # noqa: BLE001 - the check entry must report any mismatch
        print(f'GAIN CAPTURE CHECK FAILED: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
