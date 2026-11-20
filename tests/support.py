import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from station.device.fixture import OpticalObservation
from station.device.optical import apply_observation
from station.device.trigger import trigger
from station.rounds.authorization import authorize_read
from station.rounds.opening import open
from station.rounds.workstation import initialize_station


class StationCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='optical-tests-')
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'station.sqlite3'
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        initialize_station(self.connection)

    def open_round(self, pieces, limit=48, round_id='day'):
        return open(self.connection, round_id, pieces, limit)

    def order_id(self, piece, round_id='day'):
        return f'{round_id}/{piece}'

    def start(self, piece, round_id='day'):
        order_id = self.order_id(piece, round_id)
        authorize_read(self.connection, round_id, piece, order_id)
        return trigger(self.connection, round_id, piece, order_id)

    def reading(self, piece, raw_code, tick=10, round_id='day'):
        return apply_observation(self.connection, self.start(piece, round_id),
                                 OpticalObservation('left', tick, 'reading', raw_code))

    def missing(self, piece, round_id='day'):
        exposure = self.start(piece, round_id)
        self.assertIsNone(apply_observation(self.connection, exposure,
                                           OpticalObservation('left', 10, 'empty')))
        return apply_observation(self.connection, exposure, OpticalObservation('left', 11, 'end'))

    def offline(self):
        connection = sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True)
        self.addCleanup(connection.close)
        return connection

    def damaged_copy(self, mutation, params):
        path = self.database.with_name('audit-copy.sqlite3')
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        self.connection.backup(connection)
        connection.execute('PRAGMA foreign_keys = OFF')
        with connection:
            cursor = connection.execute(mutation, params)
            self.assertEqual(cursor.rowcount, 1)
        return connection
