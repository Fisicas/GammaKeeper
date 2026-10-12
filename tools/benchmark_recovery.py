"""Measure full-byte recovery on a fresh ten-day synthetic fixture; no device access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('capacity',ROOT/'tools'/'benchmark_ten_day.py')
capacity=importlib.util.module_from_spec(spec);spec.loader.exec_module(capacity)


def main():
    output=ROOT/'app'/'validation'/'recovery_ten_day_v01.json'
    if output.exists():raise FileExistsError(output)
    parent=ROOT/'app'/'validation'/'capacity_temp'
    with tempfile.TemporaryDirectory(prefix='recovery_',dir=parent) as folder:
        root=Path(folder).resolve();assert root.is_relative_to(parent.resolve())
        run,expected,prep,telemetry=capacity.fixture(root,86400,3600)
        print('Verifying fresh ten-day fixture with bounded prefetch',flush=True)
        started=time.perf_counter();store=capacity.RunStore(run)
        elapsed=time.perf_counter()-started
        assert store.next_seq==86401
        assert store.summary['total_counts']==int(expected.sum())
        assert store.summary['device_elapsed_s']==864000
        store.close()
        result=dict(intervals=86400,channels=1024,preparation_s=prep,integrity_recovery_s=elapsed,
                    expected_counts=int(expected.sum()),endpoint_count_conservation=True,device_exposure_s=864000,
                    telemetry_batches=telemetry,read_threads=8,prefetched_payload_limit=16,
                    analysis_build=capacity.identity(),synthetic=True,hardware_access=False,
                    scope='Fresh fixture; all spectrum bytes hashed and reconstructed before acquisition. Filesystem cache is uncontrolled; sparse telemetry here does not exercise export.')
        assert root.is_relative_to(parent.resolve())
    with output.open('x',encoding='utf-8') as handle:json.dump(result,handle,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='analysis_build'},indent=2),flush=True)


if __name__=='__main__':main()
