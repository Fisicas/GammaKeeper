"""Derived observation-time bins; original device timestamps remain unchanged.

The firmware clock is an extrapolated device timebase. Reception UTC is a
separate, explicitly named observation-time coordinate, not a claim about the
exact photon/sample time. One latest real-time estimator per received batch is
used; every original record remains in the raw journal.
"""
from bisect import bisect_left,bisect_right
from collections import deque
import json
from analysis import timestamp,weighted_telemetry

BASIS='host_reception_utc'


def observation_from_batch(batch):
    candidates=[r for r in batch['result']['records'] if r['type']=='RealTimeData']
    if not candidates:
        return None
    latest=max(candidates,key=lambda r:timestamp(r['dt']))
    received=batch['utc_after']
    observation=dict(latest,device_dt=latest['dt'],dt=received,received_utc=received,
                     timestamp_basis=BASIS,connection_id=batch.get('connection_id'),
                     device_projection_lead_s=timestamp(latest['dt'])-timestamp(received),
                     timing_note='Observation at reception; physical sample/filter latency not calibrated',
                     batch_realtime_records=len(candidates))
    return observation


def read_observations(db,start=None,end=None):
    conditions=[];args=[]
    if start is not None:
        conditions.append('utc>=?');args.append(start)
    if end is not None:
        conditions.append('utc<=?');args.append(end)
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    rows=[]
    for payload, in db.execute('SELECT payload FROM telemetry'+where+' ORDER BY utc,id',args):
        observation=observation_from_batch(json.loads(payload))
        if observation:
            rows.append(observation)
    return rows


class ObservationIndex:
    def __init__(self,records):
        self.records=sorted(records,key=lambda r:timestamp(r['dt']))
        self.times=[timestamp(r['dt']) for r in self.records]

    def bin(self,start,end):
        first=bisect_left(self.times,start-3)
        last=bisect_right(self.times,end+3)
        result=weighted_telemetry(self.records[first:last],start,end)
        result.update(timestamp_basis=BASIS,
                      timing_note='UTC reception observations; exact device sample/filter timing remains unverified',
                      selection='latest RealTimeData per received batch; original records retained',
                      finalized=bool(self.times and self.times[-1]>=end))
        return result

    def latest(self):
        return self.records[-1] if self.records else None


class LiveJournalTelemetry:
    """Bounded preview memory and incremental cumulative reception-time integral."""
    def __init__(self,start_utc,preview_seconds=2400):
        self.start=start_utc
        self.preview_seconds=preview_seconds
        self.last_id=0
        self.records=deque()
        self.previous=None
        self.dose_uSv=0.
        self.covered_s=0.
        self.latest_batch_id=0

    def update(self,db,limit=5000):
        self.latest_batch_id=db.execute('SELECT coalesce(max(id),0) FROM telemetry').fetchone()[0]
        for bid,payload in db.execute('SELECT id,payload FROM telemetry WHERE id>? ORDER BY id LIMIT ?', (self.last_id,limit)):
            self.last_id=bid
            observation=observation_from_batch(json.loads(payload))
            if observation is None:
                continue
            current_time=timestamp(observation['dt'])
            if self.previous:
                old_time=timestamp(self.previous['dt'])
                a=max(self.start,old_time)
                b=min(current_time,old_time+3)
                if b>a:
                    self.covered_s+=b-a
                    self.dose_uSv+=(b-a)*self.previous['dose_rate']*10000/3600
            self.previous=observation
            self.records.append(observation)
            cutoff=current_time-self.preview_seconds-4
            while self.records and timestamp(self.records[0]['dt'])<cutoff:
                self.records.popleft()
        return self.last_id>=self.latest_batch_id

    def coverage(self):
        end=timestamp(self.previous['dt']) if self.previous else self.start
        return min(1.,self.covered_s/(end-self.start)) if end>self.start else 0.

    def index(self):
        return ObservationIndex(list(self.records))
