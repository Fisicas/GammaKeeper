"""Read-only SNTP offset measurements; never change the computer clock."""
import concurrent.futures
import datetime as dt
import socket
import struct
import time

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
    return dict(offset_s=offset, round_trip_s=max(0., delay), root_dispersion_s=dispersion,
                estimated_uncertainty_s=max(0., delay) / 2 + dispersion, stratum=reply[1])


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
            if abs((t4 - t1) - (time.perf_counter() - m1)) > .05:
                raise ValueError('Host clock changed during NTP exchange')
            samples.append(parse_reply(reply, stamp, t1, t4))
    best = min(samples, key=lambda x: x['round_trip_s'])
    return dict(best, server=server, samples=samples, checked_host_utc=dt.datetime.now(dt.timezone.utc).isoformat())


def check_clock():
    values, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(query, server): server for server in ['time.windows.com', 'time.cloudflare.com']}
        for future in concurrent.futures.as_completed(futures):
            try:
                values.append(future.result())
            except Exception as error:
                errors.append(dict(server=futures[future], error=f'{type(error).__name__}: {error}'))
    if values:
        best = min(values, key=lambda x: x['estimated_uncertainty_s'])
        disagreement = max(x['offset_s'] for x in values) - min(x['offset_s'] for x in values)
        if disagreement > .5:
            return dict(offset_s=0., verified=False, errors=errors, samples=values,
                        note='NTP servers disagree by >0.5s; host UTC retained without correction')
        return dict(best, verified=True, errors=errors, server_measurements=values,
                    note='UTC = saved host UTC + measured SNTP offset; uncertainty estimate includes RTT/2 and root dispersion, not a certified timing guarantee')
    return dict(offset_s=0., verified=False, errors=errors, estimated_uncertainty_s=None,
                note='NTP unavailable: host UTC retained; independent accuracy unverified')


if __name__ == '__main__':
    import json
    print(json.dumps(check_clock(), indent=2))
