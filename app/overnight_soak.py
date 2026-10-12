"""Run supervised 24h validation and save a read-only audit on completion."""
import bootstrap
import datetime as dt
from pathlib import Path
import subprocess
import traceback
from runtime import python_command
from storage import immutable_json
from saved_status import load_status
from audit_live import audit
from export_run import export_run

ROOT=Path(__file__).resolve().parent

def main():
    run=ROOT/'runs'/('soak_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'_RC-103-006489')
    (ROOT/'validation').mkdir(exist_ok=True)
    request=dict(run_path=str(run),duration_s=86400,interval_s=10,
                 started_host_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                 planned_host_end_utc=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat(),
                 label='v0.2 supervised 24h reliability soak; no planned fault injection')
    marker=ROOT/'validation'/('soak_request_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
    immutable_json(marker,request)
    with marker.with_suffix('.log').open('xb') as output:
        process=subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'supervisor.py'),'--run',str(run),
                                  '--seconds','86400','--interval','10','--serial','RC-103-006489',
                                  '--label',request['label']],cwd=ROOT,stdout=output,stderr=output,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        code=process.wait()
    result=dict(request,supervisor_exit_code=code,finished_host_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    try:
        result['final_state']=load_status(run)
        result['audit']=audit(run)
        target,export=export_run(run)
        result.update(export_folder=str(target),export_report=export,
                      completion='completed' if code==0 and result['final_state']['state']=='completed' else 'stopped_or_failed')
    except Exception as error:
        result.update(completion='audit_or_run_failed',error=f'{type(error).__name__}: {error}',traceback=traceback.format_exc())
    immutable_json(marker.with_name(marker.stem.replace('soak_request','soak_result')+'.json'),result)
    return code

if __name__=='__main__':
    raise SystemExit(main())
