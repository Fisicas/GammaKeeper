"""Read-only SNTP offset measurements; never change the computer clock."""
import concurrent.futures
import datetime as dt
import socket
import struct
import time
import math

NTP_EPOCH = 2208988800


def ntp_stamp(unix_seconds):
    value = unix_seconds + NTP_EPOCH
    integer = int(value)
    return struct.pack('!II', integer, int((value - integer) * 2**32))


def unix_stamp(payload):
    integer, fraction = struct.unpack('!II', payload)
    return integer - NTP_EPOCH + fraction / 2**32


def parse_reply(reply, request_stamp, t1, t4):
    if len(reply) < 48 or reply[24:32] != request_stamp:
        raise ValueError('Invalid NTP length/origin timestamp')
    leap, version, mode = reply[0] >> 6, (reply[0] >> 3) & 7, reply[0] & 7
    if leap == 3 or version < 3 or mode != 4 or not 1 <= reply[1] <= 15:
        raise ValueError('NTP server is not synchronized or returned an invalid mode/stratum')
    t2, t3 = unix_stamp(reply[32:40]), unix_stamp(reply[40:48])
    delay = (t4 - t1) - (t3 - t2)
    if delay < -.01 or delay > 2 or t3 < t2:
        raise ValueError('Invalid/excessive NTP round-trip delay')
    offset = ((t2 - t1) + (t3 - t4)) / 2
    dispersion = struct.unpack('!I', reply[8:12])[0] / 65536
    root_delay = struct.unpack('!i', reply[4:8])[0] / 65536
    if dispersion > 10 or abs(root_delay) > 10 or not math.isfinite(offset):
        raise ValueError('Excessive NTP server dispersion/root delay')
    return dict(offset_s=offset, round_trip_s=max(0., delay), root_dispersion_s=dispersion,
                root_delay_s=root_delay,
                estimated_uncertainty_s=max(0., delay) / 2 + dispersion + max(0., root_delay) / 2,
                stratum=reply[1])


def query(server):
    samples = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1.5)
        # Resolve outside the time-critical request/response timestamps.
        address = socket.gethostbyname(server)
        sock.connect((address, 123))
        for _ in range(3):
            packet = bytearray(48)
            packet[0] = 0x23  # NTPv4 client, leap indicator 0.
            t1 = time.time()
            m1 = time.perf_counter()
            stamp = ntp_stamp(t1)
            packet[40:48] = stamp
            sock.send(packet)
            reply = sock.recv(512)
            t4 = time.time()
            m4 = time.perf_counter()
            if abs((t4 - t1) - (m4 - m1)) > .05:
                raise ValueError('Host clock changed during NTP exchange')
            sample = parse_reply(reply, stamp, t1, t4)
            sample.update(reference_unix_s=t4+sample['offset_s'], reference_monotonic_s=m4)
            samples.append(sample)
    best = min(samples, key=lambda x: x['round_trip_s'])
    return dict(best, server=server, samples=samples, checked_host_utc=dt.datetime.now(dt.timezone.utc).isoformat())


def select_measurements(values, errors=None):
    errors = errors or []
    if values:
        best = min(values, key=lambda x: x['estimated_uncertainty_s'])
        disagreement = max(x['offset_s'] for x in values) - min(x['offset_s'] for x in values)
        tolerance = max(.1, sum(x['estimated_uncertainty_s'] for x in values))
        if disagreement > tolerance or best['estimated_uncertainty_s'] > .25:
            return dict(offset_s=0., verified=False, errors=errors, samples=values,
                        note='UTC reference rejected: server disagreement or excessive uncertainty; retain monotonic holdover')
        result = dict(best, verified=True, errors=errors, server_measurements=values,
                      source_count=len(values), server_spread_s=disagreement,
                      note='SNTP reference bound to system monotonic time. Error estimate includes RTT/2, root dispersion/delay and source spread; not a certified timing guarantee.')
        result['estimated_uncertainty_s'] = max(best['estimated_uncertainty_s'], disagreement)
        return result
    return dict(offset_s=0., verified=False, errors=errors, estimated_uncertainty_s=None,
                note='NTP unavailable: preserve previous monotonic reference or explicit unverified host-clock anchor')


def check_clock():
    values, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(query, server): server for server in ['time.windows.com', 'time.cloudflare.com']}
        for future in concurrent.futures.as_completed(futures):
            try:
                values.append(future.result())
            except Exception as error:
                errors.append(dict(server=futures[future], error=f'{type(error).__name__}: {error}'))
    return select_measurements(values, errors)


if __name__ == '__main__':
    import json
    print(json.dumps(check_clock(), indent=2))
