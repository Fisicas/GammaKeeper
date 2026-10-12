"""Bounded reception-time telemetry integration against a frozen SQLite horizon."""
import json
from analysis import timestamp, DOSE_R_TO_USV
from telemetry_analysis import observation_from_batch, BASIS


class JournalObservationIndex:
    def __init__(self, db):
        self.db = db

    def bin(self, start, end):
        dose = count = coverage = 0.
        previous = None
        latest = None
        def integrate(record, next_time):
            nonlocal dose, count, coverage
            t = timestamp(record['dt'])
            a, b = max(start, t), min(end, next_time, t+3)
            if b>a:
                width = b-a
                coverage += width
                dose += width*record['dose_rate']*DOSE_R_TO_USV
                count += width*record['count_rate']
        for payload, in self.db.execute('SELECT payload FROM telemetry WHERE utc>=? AND utc<=? ORDER BY utc,id',
                                        (start-3,end+3)):
            current = observation_from_batch(json.loads(payload))
            if current is None: continue
            if previous is not None: integrate(previous,timestamp(current['dt']))
            previous = latest = current
        if previous is not None: integrate(previous,end)
        fraction = min(1.,coverage/(end-start)) if end>start else 0.
        return dict(coverage_fraction=fraction, dose_rate_uSv_h=dose/coverage if coverage else None,
                    device_count_rate_cps=count/coverage if coverage else None,
                    integrated_dose_uSv=dose/3600 if fraction>=.999 else None,
                    observed_integrated_dose_uSv=dose/3600,observed_duration_s=coverage,
                    timestamp_basis=BASIS, finalized=bool(latest and timestamp(latest['dt'])>=end),
                    estimator='zero-order hold; 3s maximum hold; means cover observed duration only',
                    timing_note='UTC reception observations; physical sample/filter latency uncalibrated')
