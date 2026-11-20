import sqlite3

from station.device.fixture import OpticalObservation
from station.device.recording import DeviceRecording, ExposureRecord
from station.device.terminal import ConfirmedMissing, RawReading, Terminal
from station.rounds.repository import transaction


def apply_observation(connection: sqlite3.Connection, exposure: ExposureRecord,
                      observation: OpticalObservation) -> Terminal | None:
    if observation.head != 'left':
        raise ValueError('the station exposure uses the left head')
    with transaction(connection, write=True):
        recordings = DeviceRecording(connection)
        if recordings.find_exposure(exposure.order_id) != exposure:
            raise ValueError('observation targets an unknown or conflicting exposure')
        if observation.kind == 'empty':
            return None
        binding = (f'original:{exposure.trigger_id}', exposure.round_id, exposure.piece,
                   exposure.order_id, exposure.trigger_id)
        if observation.kind == 'reading':
            terminal = RawReading(*binding, observation.tick, observation.raw_code)
        else:
            terminal = ConfirmedMissing(*binding)
        return recordings.finalize(terminal)
