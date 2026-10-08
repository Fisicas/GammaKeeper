"""Select live status without treating returned historical records as resets."""
from analysis import timestamp


def select_rare(previous, incoming, received_utc):
    observation=dict(incoming)
    if timestamp(incoming['dt'])>timestamp(received_utc)+1:
        observation.update(original_device_dt=incoming['dt'],dt=received_utc,
                           timestamp_quality='reception time; projected device timestamp ahead; exact sample time unverified')
    if previous:
        # Both the sample time and independent accumulation duration are older.
        # This exact pattern occurred when the device returned overnight history
        # after reopening USB. Keep it in raw telemetry, not as the latest status.
        if (timestamp(observation['dt'])<timestamp(previous['dt'])-5
                and incoming['duration']<previous['duration']):
            return previous,'historical_status_ignored'
    elif timestamp(observation['dt'])<timestamp(received_utc)-180:
        return None,'historical_status_before_baseline'
    return observation,None
