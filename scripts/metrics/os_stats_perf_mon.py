import psutil
import time
import csv
import argparse
import subprocess
import sys
import os
import platform

def ping_host(host):
    """Ping the host and return latency in ms."""
    try:
        if platform.system() == 'Windows':
            output = subprocess.check_output(["ping", host, "-n", "1"], universal_newlines=True)
            for line in output.split("\n"):
                if "TTL=" in line or "Zeit=" in line:
                    if "time=" in line:
                        latency = line.split('time=')[1].split('ms')[0]
                    elif "Zeit=" in line:  # Deutsche Windows
                        latency = line.split('Zeit=')[1].split('ms')[0]
                    return float(latency.strip())
        else:
            output = subprocess.check_output(["ping", "-c", "1", host], universal_newlines=True)
            for line in output.split("\n"):
                if "ttl=" in line.lower() and "time=" in line.lower():
                    latency = line.split('time=')[1].split('ms')[0]
                    return float(latency.strip())
    except Exception as e:
        print(f"Fehler beim Pingen des Hosts {host}: {e}")
    return None

def measure_io_latency(disk):
    """Measure IO latency by writing and reading a 2KB block without OS caching."""
    data_size = 2048  # 2KB
    data = b'\0' * data_size

    # Prepare file path
    file_path = os.path.join(disk, "io_latency_test.tmp")
    if platform.system() != 'Windows':
        # Ensure disk path ends with '/'
        if not disk.endswith('/'):
            disk += '/'
        file_path = os.path.join(disk, "io_latency_test.tmp")

    try:
        if platform.system() == 'Windows':
            # Import necessary modules
            import win32file
            import win32con
            import ctypes

            # Use Windows APIs for unbuffered IO
            handle = win32file.CreateFile(
                file_path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                None,
                win32file.CREATE_ALWAYS,
                win32file.FILE_ATTRIBUTE_NORMAL | win32file.FILE_FLAG_NO_BUFFERING | win32file.FILE_FLAG_WRITE_THROUGH,
                None
            )

            # Align the data to sector size
            sector_size = ctypes.windll.kernel32.GetDiskFreeSpaceW(ctypes.c_wchar_p(disk), None, None, None, None)
            sector_size = ctypes.c_ulong()
            ctypes.windll.kernel32.GetDiskFreeSpaceW(ctypes.c_wchar_p(disk), None, None, ctypes.byref(sector_size), None)
            sector_size = sector_size.value or 512

            alignment = (sector_size - (len(data) % sector_size)) % sector_size
            if alignment != 0:
                data += b'\0' * alignment

            start_time = time.perf_counter()

            # Write data
            win32file.WriteFile(handle, data)

            # Move file pointer to beginning
            win32file.SetFilePointer(handle, 0, win32con.FILE_BEGIN)

            # Read data
            _, _ = win32file.ReadFile(handle, len(data))

            end_time = time.perf_counter()

            # Close handle and delete the test file
            win32file.CloseHandle(handle)
            os.remove(file_path)

            # Calculate latency in milliseconds
            io_latency_ms = (end_time - start_time) * 1000

        else:
            # For Linux and macOS
            import fcntl
            import mmap
            import errno

            # Open file with O_DIRECT and O_SYNC
            fd = os.open(file_path, os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_DIRECT | os.O_SYNC)

            # Get filesystem block size
            stat = os.statvfs(disk)
            block_size = stat.f_bsize

            # Align the data to block size
            alignment = (block_size - (len(data) % block_size)) % block_size
            if alignment != 0:
                data += b'\0' * alignment

            # Use mmap to ensure buffer alignment
            buf = mmap.mmap(-1, len(data))
            buf.write(data)

            # Write and read operations
            start_time = time.perf_counter()

            # Write data
            os.write(fd, buf)

            # Move file pointer to beginning
            os.lseek(fd, 0, os.SEEK_SET)

            # Read data
            os.read(fd, len(data))

            end_time = time.perf_counter()

            # Clean up
            os.close(fd)
            os.remove(file_path)
            buf.close()

            # Calculate latency in milliseconds
            io_latency_ms = (end_time - start_time) * 1000

        return io_latency_ms

    except OSError as e:
        if e.errno == errno.EINVAL:
            print("O_DIRECT wird auf diesem Dateisystem nicht unterstützt.")
        else:
            print(f"Fehler bei der Messung der IO-Latenz: {e}")
        return None

    except Exception as e:
        print(f"Fehler bei der Messung der IO-Latenz: {e}")
        return None

def collect_metrics(args):
    fieldnames = [
        'Time', 'CPU%', 'Mem_Used%', 'Disk_Used%',
        'R_IOPS', 'W_IOPS', 'R_MB/s', 'W_MB/s',
        'IO_Lat(ms)', 'Ping(ms)', 'Net_MB/s_Sent', 'Net_MB/s_Recv',
        'Pkts_Sent/s', 'Pkts_Recv/s'
    ]

    # Print legend and example usage
    print("PC Performance Metrics Logger")
    print("Sammelt Performance-Metriken in konfigurierbaren Intervallen.")
    print("\nBeispielaufrufe:")
    print("  python performance_metrics.py --interval 10 --disk D:\\ --ping_target google.com")
    print("  python performance_metrics.py --interval 10 --disk /mnt/data --ping_target 8.8.8.8")
    print("\nSpalten:")
    print("  " + " | ".join(fieldnames))
    print("-" * (len(fieldnames) * 10))

    # Print header
    print("{:<19}".format(fieldnames[0]), end='')
    for field in fieldnames[1:]:
        print("{:>12}".format(field), end='')
    print()

    with open(args.csv_output, mode='w', newline='') as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(fieldnames)

        while True:
            start_time = time.time()
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_time))

            # CPU usage
            cpu_percent = psutil.cpu_percent(interval=None)

            # Memory usage
            mem = psutil.virtual_memory()
            mem_used_percent = mem.percent

            # Disk usage
            try:
                disk_usage = psutil.disk_usage(args.disk)
                disk_usage_percent = disk_usage.percent
            except Exception as e:
                print(f"Fehler beim Abrufen der Festplattennutzung: {e}")
                disk_usage_percent = 'N/A'

            # Disk IO and Network IO counters before the interval
            disk_io_start = psutil.disk_io_counters(perdisk=True)
            net_io_start = psutil.net_io_counters()

            # Measure IO latency
            io_latency = measure_io_latency(args.disk)

            # Wait for the interval
            time.sleep(args.interval)

            # Disk IO and Network IO counters after the interval
            disk_io_end = psutil.disk_io_counters(perdisk=True)
            net_io_end = psutil.net_io_counters()

            elapsed_time = time.time() - start_time

            # Disk IO calculations
            if platform.system() == 'Windows':
                disk_name = args.disk.replace(':', '').replace('\\', '').upper()
            else:
                # On Linux, use the mount point to get the device name
                disk_name = ''
                partitions = psutil.disk_partitions()
                for partition in partitions:
                    if os.path.abspath(args.disk) == os.path.abspath(partition.mountpoint):
                        disk_name = os.path.basename(partition.device)
                        break

            if disk_name in disk_io_start and disk_name in disk_io_end:
                start_disk = disk_io_start[disk_name]
                end_disk = disk_io_end[disk_name]

                read_bytes = end_disk.read_bytes - start_disk.read_bytes
                write_bytes = end_disk.write_bytes - start_disk.write_bytes
                read_mb_s = (read_bytes / (1024 * 1024)) / elapsed_time
                write_mb_s = (write_bytes / (1024 * 1024)) / elapsed_time

                read_iops = (end_disk.read_count - start_disk.read_count) / elapsed_time
                write_iops = (end_disk.write_count - start_disk.write_count) / elapsed_time
            else:
                read_mb_s = write_mb_s = read_iops = write_iops = None

            # Network IO calculations
            bytes_sent = net_io_end.bytes_sent - net_io_start.bytes_sent
            bytes_recv = net_io_end.bytes_recv - net_io_start.bytes_recv
            net_mb_s_sent = (bytes_sent / (1024 * 1024)) / elapsed_time
            net_mb_s_recv = (bytes_recv / (1024 * 1024)) / elapsed_time

            packets_sent = net_io_end.packets_sent - net_io_start.packets_sent
            packets_recv = net_io_end.packets_recv - net_io_start.packets_recv
            net_packets_sent_per_sec = packets_sent / elapsed_time
            net_packets_recv_per_sec = packets_recv / elapsed_time

            # Ping latency
            ping_latency = ping_host(args.ping_target)

            data = [
                timestamp,
                "{:.1f}".format(cpu_percent),
                "{:.1f}".format(mem_used_percent),
                "{:.1f}".format(disk_usage_percent) if disk_usage_percent != 'N/A' else 'N/A',
                "{:.1f}".format(read_iops) if read_iops is not None else 'N/A',
                "{:.1f}".format(write_iops) if write_iops is not None else 'N/A',
                "{:.2f}".format(read_mb_s) if read_mb_s is not None else 'N/A',
                "{:.2f}".format(write_mb_s) if write_mb_s is not None else 'N/A',
                "{:.2f}".format(io_latency) if io_latency is not None else 'N/A',
                "{:.2f}".format(ping_latency) if ping_latency is not None else 'N/A',
                "{:.2f}".format(net_mb_s_sent),
                "{:.2f}".format(net_mb_s_recv),
                "{:.1f}".format(net_packets_sent_per_sec),
                "{:.1f}".format(net_packets_recv_per_sec)
            ]

            # Output to console in one line
            print("{:<19}".format(data[0]), end='')
            for value in data[1:]:
                print("{:>12}".format(value), end='')
            print()

            # Write to CSV
            writer.writerow(data)
            csv_file.flush()

def main():
    parser = argparse.ArgumentParser(description='PC Performance Metrics Logger')
    parser.add_argument('--interval', type=int, default=10, help='Intervall in Sekunden zwischen den Messungen')
    parser.add_argument('--disk', type=str, default='C:\\' if platform.system() == 'Windows' else '/', help='Festplattenpfad zur Überwachung (z.B., C:\\ oder /mnt/data)')
    parser.add_argument('--ping_target', type=str, default='google.com', help='Host für die Messung der Netzwerklatenz')
    parser.add_argument('--csv_output', type=str, default='performance_metrics.csv', help='CSV-Datei zum Speichern der Metriken')

    args = parser.parse_args()

    # Check for necessary modules on Windows
    if platform.system() == 'Windows':
        try:
            import win32file
            import win32con
        except ImportError:
            print("Das erforderliche Modul 'pywin32' wurde nicht gefunden.")
            print("Installiere es mit: pip install pywin32")
            sys.exit(1)

    # Check for root privileges on Linux for O_DIRECT
    if platform.system() == 'Linux':
        if os.geteuid() != 0:
            print("Dieses Skript muss unter Linux als Root ausgeführt werden, um die IO-Latenz zu messen.")
            sys.exit(1)

    try:
        collect_metrics(args)
    except KeyboardInterrupt:
        print("\nÜberwachung vom Benutzer gestoppt.")
        sys.exit(0)

if __name__ == "__main__":
    main()