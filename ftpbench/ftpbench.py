"""
FTP benchmark.

Usage:
    ftpbench --help
    ftpbench -h <host> -u <user> -p <password> [options] login
    ftpbench -h <host> -u <user> -p <password> [options] upload <workdir> [-s <size>]
    ftpbench -h <host> -u <user> -p <password> [options] download <workdir> [-s <size>] [--files <count>]

Connection options:
    -h <host>, --host=<host>              FTP host
                                          Auto-detection of dns round-robin records is supported.
                                          For IPv6 use brackets,
                                          e.g.: -h [2001:db8::216:cbff::42].
    --port=<[port]>                       FTP server port
    -u <user>, --user=<user>              FTP user
    -p <password>, --password=<password>  FTP password

Timing options:
    -t <sec>, --timeout=<sec>             Timeout for operation [default: 10]
    --maxrun=<minutes>                    Duration of benchmarking in minutes [default: 5]
    --fixevery=<sec>                      Recording period for stat values [default: 5]

Benchmark options:
    -c <count>, --concurrent=<count>      Concurrent operations [default: 10]
    --csv=<file>                          Save result to csv file
    <workdir>                             Base ftp dir to store test files
    -s <size>, --size=<size>              Size of test files in MB [default: 10]
    --files <count>                       Number of files generated for download test [default: 10]
    --datamode=<datamode>                 Make testing upload file mode [default: null]
                                          You can choose between the null mode and the random mode.
                                          When using the null mode for benchmarking uploads, the transferred data will be empty.
                                          When using the random mode for benchmarking uploads, the transferred data will be randomly generated.
"""
# ------------------------------------------------------------------------------
from __future__ import absolute_import
from __future__ import print_function

from builtins import range
from builtins import object
__author__ = "Konstantin Enchant <sirkonst@gmail.com>"

try:
    import docopt
except ImportError:  # for standalone
    from . import docopt

from gevent.monkey import patch_all
patch_all()
import gevent
from gevent import Timeout
from gevent.pool import Pool

from contextlib import contextmanager
from ftplib import FTP as _FTP, error_temp, error_perm
from itertools import cycle
import uuid
import os
import random
import string
from socket import error as sock_error

try:
    import dns.resolver as resolver
except ImportError:
    resolver = None

try:
    from timecard import *
except ImportError:  # for standalone
    from .timecard import *
# ------------------------------------------------------------------------------

class Data(object):
    chunk = "x" * 65536

    def __init__(self, size):
        self.size = size
        self.read = 0

    def __iter__(self):
        return self

    def __next__(self):
        tosend = self.size - self.read
        if tosend == 0:
            raise StopIteration

        if tosend > 65536:
            self.read += 65536
            return self.chunk
        else:
            self.read += tosend
            return self.chunk[:tosend]

## Make Random String
def generate_random_string(length):
   return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(length))

randomData = generate_random_string(65536)

class DataRandom(object):
    def __init__(self, size):
        self.chunk = randomData
        self.size = size
        self.read = 0

    def __iter__(self):
        return self

    def __next__(self):
        tosend = self.size - self.read
        if tosend == 0:
            raise StopIteration

        if tosend > 65536:
            self.read += 65536
            return self.chunk
        else:
            self.read += tosend
            return self.chunk[:tosend]



class FTP(object):

    def __init__(self, host, port, user, password, timeout, stats):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.timeout = timeout
        self.stats = stats
        self.upload_files = []

    @contextmanager
    def connect(self):
        with Timeout(self.timeout):
            ftp = _FTP()
            ftp.connect(self.host, self.port)
            ftp.login(self.user, self.password)

        try:
            yield ftp
        finally:
            ftp.close()

    def upload(self, path, data):
        with self.connect() as ftp:
            self.upload_files.append(path)

            with Timeout(self.timeout):
                ftp.voidcmd("TYPE I")  # binary mode
                channel = ftp.transfercmd("STOR " + path)

            for chunk in data:
                with Timeout(self.timeout):
                    channel.sendall(chunk.encode())
                    self.stats.traffic += len(chunk)

            with Timeout(self.timeout):
                channel.close()
                ftp.voidresp()

    def donwload(self, path):
        with self.connect() as ftp:
            with Timeout(self.timeout):
                ftp.voidcmd("TYPE I")  # binary mode
                channel = ftp.transfercmd("RETR " + path)

            while True:
                with Timeout(self.timeout):
                    chunk = channel.recv(65536)
                    if not chunk:
                        break
                    self.stats.traffic += len(chunk)

            with Timeout(self.timeout):
                channel.close()
                ftp.voidresp()

    def clean(self):
        with self.connect() as ftp:
            for path in self.upload_files:
                ftp.delete(path)


def run_bench_login(opts):
    stats = Timecard(opts["csvfilename"])
    stats.time = AutoDateTime(show_date=False)
    stats.requests = TotalAndSec("request")
    stats.success = TotalAndSec("success")
    stats.latency = Timeit("latency", limits=[1, 2, 5])
    stats.fail = MultiMetric("fails-total")
    stats.fail.timeout = Int("timeout")
    stats.fail.rejected = Int("rejected")

    ftp = FTP(
        opts["host"],opts["port"], opts["user"], opts["password"],
        opts["timeout"], stats=stats)

    print("\n\rStart login benchmark: concurrent={} timeout={}s\n\r".format(
        opts["concurrent"], opts["timeout"]
    ))

    stats.write_headers()

    def _print_stats():
        i = 0
        while True:
            i += 1
            if i == opts["fixevery"]:
                stats.write_line(fix=True)
                i = 0
            else:
                stats.write_line(fix=False)
            gevent.sleep(1)

    def _check():
        stats.requests += 1
        try:
            with stats.latency():
                with ftp.connect():
                    pass
        except Timeout:
            stats.fail.timeout += 1
        except (error_temp, error_perm, sock_error):
            stats.fail.rejected += 1
        else:
            stats.success += 1

    gr_stats = gevent.spawn(_print_stats)
    gr_pool = Pool(size=opts["concurrent"])
    try:
        with Timeout(opts["maxrun"] * 60 or None):
            while True:
                gr_pool.wait_available()
                gr_pool.spawn(_check)
    except (KeyboardInterrupt, Timeout):
        pass
    finally:
        print("\n")
        gr_stats.kill()
        gr_pool.kill()


def run_bench_upload(opts):
    stats = Timecard(opts["csvfilename"])
    stats.time = AutoDateTime(show_date=False)
    stats.request = MultiMetric("request")
    stats.request.total = Int("total")
    stats.request.complete = Int("complete")
    stats.request.timeout = Int("timeout")
    stats.request.rejected = Int("rejected")
    stats.traffic = Traffic("traffic")
    stats.uploadtime = Timeit("upload-time")

    ftp = FTP(
        opts["host"],opts["port"], opts["user"], opts["password"],
        opts["timeout"], stats=stats
    )

    print (
        "\n\rStart upload benchmark: concurrent={} timeout={}s size={}MB dataMode={}\n\r"
        "".format(opts["concurrent"], opts["timeout"], opts["size"],opts["datamode"])
    )

    stats.write_headers()

    def _print_stats():
        i = 0
        while True:
            i += 1
            if i == opts["fixevery"]:
                stats.write_line(fix=True)
                i = 0
            else:
                stats.write_line(fix=False)
            gevent.sleep(1)
    
    def _check_random():
        stats.request.total += 1
        try:
            path = os.path.join(
                opts["workdir"], "bench_write-%s" % uuid.uuid1().hex)
            data = DataRandom(opts["size"] * 1024 * 1024)
            with stats.uploadtime():
                ftp.upload(path, data)
        except Timeout:
            stats.request.timeout += 1
        except (error_temp, error_perm, sock_error):
            stats.request.rejected += 1
        else:
            stats.request.complete += 1

    def _check():
        stats.request.total += 1
        try:
            path = os.path.join(
                opts["workdir"], "bench_write-%s" % uuid.uuid1().hex)
            data = Data(opts["size"] * 1024 * 1024)
            with stats.uploadtime():
                ftp.upload(path, data)
        except Timeout:
            stats.request.timeout += 1
        except (error_temp, error_perm, sock_error):
            stats.request.rejected += 1
        else:
            stats.request.complete += 1

    gr_stats = gevent.spawn(_print_stats)
    gr_pool = Pool(size=opts["concurrent"])
    try:
        if opts["datamode"] == "random":
            with Timeout(opts["maxrun"] * 60 or None):
                while True:
                    gr_pool.wait_available()
                    gr_pool.spawn(_check_random)
        else:
            with Timeout(opts["maxrun"] * 60 or None):
                while True:
                    gr_pool.wait_available()
                    gr_pool.spawn(_check)
    except (KeyboardInterrupt, Timeout):
        pass
    finally:
        print("\n")
        gr_stats.kill()
        gr_pool.kill()


def run_bench_download(opts):
    stats = Timecard(opts["csvfilename"])
    stats.time = AutoDateTime(show_date=False)
    stats.request = MultiMetric("request")
    stats.request.total = Int("total")
    stats.request.complete = Int("complete")
    stats.request.timeout = Int("timeout")
    stats.request.rejected = Int("rejected")
    stats.traffic = Traffic("traffic")
    stats.downloadtime = Timeit("download-time")

    ftp = FTP(
        opts["host"],opts["port"], opts["user"], opts["password"],
        opts["timeout"], stats=stats
    )

    print("Preparing for testing...")
    ftp.timeout = 60
    if opts["datamode"] == "random":
        for _ in range(opts["countfiles"]):
            path = os.path.join(opts["workdir"], "bench_read-%s" % uuid.uuid1().hex)
            data = DataRandom(opts["size"] * 1024 * 1024)
            ftp.upload(path, data)
    else:
        for _ in range(opts["countfiles"]):
            path = os.path.join(opts["workdir"], "bench_read-%s" % uuid.uuid1().hex)
            data = Data(opts["size"] * 1024 * 1024)
            ftp.upload(path, data)
    ftp.timeout = opts["timeout"]
    filesiter = cycle(ftp.upload_files)

    print (
        "\n\rStart download benchmark: concurrent={} timeout={}s size={}MB"
        " filecount={}\n\r"
        "".format(
            opts["concurrent"], opts["timeout"], opts["size"],
            opts["countfiles"])
    )

    stats.write_headers()

    def _print_stats():
        i = 0
        while True:
            i += 1
            if i == opts["fixevery"]:
                stats.write_line(fix=True)
                i = 0
            else:
                stats.write_line(fix=False)
            gevent.sleep(1)

    def _check():
        stats.request.total += 1
        try:
            with stats.downloadtime():
                ftp.donwload(next(filesiter))
        except Timeout:
            stats.request.timeout += 1
        except (error_temp, error_perm, sock_error):
            stats.request.rejected += 1
        else:
            stats.request.complete += 1

    gr_stats = gevent.spawn(_print_stats)
    gr_pool = Pool(size=opts["concurrent"])
    try:
        with Timeout(opts["maxrun"] * 60 or None):
            while True:
                gr_pool.wait_available()
                gr_pool.spawn(_check)
    except (KeyboardInterrupt, Timeout):
        pass
    finally:
        print("\n")
        gr_stats.kill()
        gr_pool.kill()


def main():
    try:
        arguments = docopt.docopt(__doc__)
        opts = dict()

        opts["host"] = arguments["--host"]
        if resolver and "," not in opts["host"]:
            try:
                hosts = []
                for x in resolver.resolve(opts["host"], "A"):
                    hosts.append(x.to_text())
                opts["host"] = ",".join(hosts)
            except:
                pass
        opts["port"] = int(arguments["--port"])
        opts["user"] = arguments["--user"]
        opts["password"] = arguments["--password"]
        opts["concurrent"] = int(arguments["--concurrent"])
        opts["timeout"] = int(arguments["--timeout"])
        opts["maxrun"] = int(arguments["--maxrun"])
        opts["size"] = int(arguments["--size"])
        opts["workdir"] = arguments["<workdir>"]
        opts["csvfilename"] = arguments["--csv"]
        opts["fixevery"] = int(arguments["--fixevery"])
        opts["countfiles"] = int(arguments["--files"])
        opts["datamode"] = arguments["--datamode"]
    except docopt.DocoptExit as e:
        print(e.message)
    else:
        if arguments["login"]:
            run_bench_login(opts)
        elif arguments["upload"]:
            run_bench_upload(opts)
        elif arguments["download"]:
            run_bench_download(opts)


if __name__ == "__main__":
    main()
