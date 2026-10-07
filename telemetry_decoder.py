"""Configuration-aware decoder based on cdump/radiacode (MIT notice preserved).

Unlike upstream 0.4.0, group 4 is selected from the device's declared schema.
Valid records before a malformed/unknown frame remain available. All raw bytes
and exact error offsets remain in the saved batch; no speculative resync.
"""
import datetime as dt
import math
import struct


def group4_schema(configuration):
    if '[[GRP_DoseCounter]]\nId=4' in configuration:
        return 'dose_counter'
    if '[[GRP_UserData]]\nId=4' in configuration:
        return 'user_data'
    return 'unknown'


def decode_buffer(raw, base_time, configuration):
    records, warnings = [], []
    offset, next_seq = 0, None
    schema = group4_schema(configuration.replace('\r\n','\n'))
    def take(fmt):
        nonlocal offset
        size = struct.calcsize(fmt)
        if offset + size > len(raw):
            raise ValueError(f'{size} bytes needed for {fmt}; {len(raw)-offset} available')
        value = struct.unpack_from(fmt,raw,offset)
        offset += size
        return value
    while offset < len(raw):
        frame_start = offset
        try:
            seq,eid,gid,ts = take('<BBBi')
            if next_seq is not None and seq != next_seq:
                raise ValueError(f'Sequence gap: expected {next_seq}, received {seq}')
            next_seq = (seq+1) % 256
            row = dict(dt=(base_time+dt.timedelta(milliseconds=ts*10)).isoformat(),
                       sequence=seq, event_group=eid, group_id=gid, timestamp_offset_10ms=ts)
            if eid == 0 and gid == 0:
                cr,dr,ce,de,flags,rt = take('<ffHHHB')
                row.update(type='RealTimeData',count_rate=cr,dose_rate=dr,count_rate_err=ce/10,
                           dose_rate_err=de/10,flags=flags,real_time_flags=rt)
            elif eid == 0 and gid == 1:
                cr,dr = take('<ff')
                row.update(type='RawData',count_rate=cr,dose_rate=dr)
            elif eid == 0 and gid == 2:
                count,cr,dr,de,flags = take('<IffHH')
                row.update(type='DoseRateDB',count=count,count_rate=cr,dose_rate=dr,dose_rate_err=de/10,flags=flags)
            elif eid == 0 and gid == 3:
                duration,dose,temp,charge,flags = take('<IfHHH')
                row.update(type='RareData',duration=duration,dose=dose,temperature=(temp-2000)/100,
                           charge_level=charge/100,flags=flags)
            elif eid == 0 and gid == 4 and schema == 'dose_counter':
                count,flags = take('<IH')
                row.update(type='DoseCounter',dose_counter_raw=count,flags=flags,
                           declared_unit='uR',scale_validation='raw value retained; not used for dose integration')
            elif eid == 0 and gid in (4,5) and (gid == 5 or schema == 'user_data'):
                count,cr,dr,de,flags = take('<IffHH')
                row.update(type='UserData' if gid==4 else 'ScheduleData',count=count,count_rate=cr,
                           dose_rate=dr,dose_rate_err=de/10,flags=flags)
            elif eid == 0 and gid == 6:
                x,y,z = take('<HHH')
                row.update(type='AccelerometerRaw',x_raw=x,y_raw=y,z_raw=z)
            elif eid == 0 and gid == 7:
                event,param,flags = take('<BBH')
                row.update(type='Event',event=event,event_param1=param,flags=flags)
            elif eid == 0 and gid in (8,9):
                value,flags = take('<fH')
                row.update(type='RawCountRate' if gid==8 else 'RawDoseRate',flags=flags,
                           **({'count_rate':value} if gid==8 else {'dose_rate':value}))
            elif eid == 1 and gid in (1,2,3):
                count,period = take('<HI')
                size = count * {1:8,2:16,3:14}[gid]
                payload = take(f'<{size}s')[0]
                row.update(type='RawSeries',samples=count,period_ms=period,sample_payload_hex=payload.hex())
            else:
                raise ValueError(f'Unsupported frame eid={eid}, gid={gid}, group4_schema={schema}')
            if any(isinstance(v,float) and not math.isfinite(v) for v in row.values()):
                raise ValueError('Nonfinite telemetry value')
            records.append(row)
        except (ValueError,struct.error,OverflowError) as error:
            warnings.append(dict(offset=frame_start,error=f'{type(error).__name__}: {error}',
                                 remaining_hex=raw[frame_start:].hex()))
            break
    return dict(raw_hex=raw.hex(),records=records,decode_warning=json_warning(warnings),
                decode_diagnostics=warnings,decoder_version='configuration-aware-v2',
                group4_schema=schema,parsed_prefix_bytes=frame_start if warnings else offset)


def json_warning(warnings):
    if not warnings:
        return None
    return f"Byte {warnings[0]['offset']}: {warnings[0]['error']}; prefix records and complete raw payload retained"
