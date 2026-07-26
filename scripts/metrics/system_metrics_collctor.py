#!/usr/bin/env python3
"""
System Metrics Collector

This script collects various system metrics such as CPU usage, memory usage,
disk I/O metrics, and optionally network latency. It also measures disk latency
using file operations with Direct I/O and synchronous reads to bypass the OS cache.

Please note that the use of Direct I/O and synchronous reads is operating system-specific.
Under Linux, `O_DIRECT` is used to enable Direct I/O. Under Windows, this method is not
implemented due to the complexity of using Direct I/O with standard Python libraries.

Installation Instructions:

Required Python Package:
- psutil

Install the package:

Windows:
- Open Command Prompt as Administrator and run:

"""

import psutil
import time
import csv
import datetime
import socket
import platform
import argparse
import subprocess
import os
import sys

def parse_arguments():
  """
  Parses command-line arguments.
  """
  parser = argparse.ArgumentParser(description='System Metrics Collector')
  parser.add_argument('--interval', type=int, default=60,
                      help='Interval in seconds between measurements (default: 60)')
  parser.add_argument('--outputfile', type=str, default='system_metrics.csv',
                      help='Output CSV file name (default: system_metrics.csv)')
  parser.add_argument('--stdout', type=str, choices=['true', 'false'], default='true',
                      help='Print output to console (default: true)')
  parser.add_argument('--pinghost', type=str, default='',
                      help='Host/IP to ping for network latency measurement (optional)')
  parser.add_argument('--testfile', type=str, default='/tmp/io_testfile',
                      help='Path to the test file for I/O latency measurement (default: /tmp/io_testfile)')
  args = parser.parse_args()
  return args

def collect_metrics():
  """
  Collects system metrics without time intervals.
  Returns a dictionary of collected metrics.
  """
  metrics = {}

  # Timestamp and system information
  timestamp = datetime.datetime.now().isoformat()
  hostname = socket.gethostname()
  system = platform.system()
  metrics['timestamp'] = timestamp
  metrics['hostname'] = hostname
  metrics['system'] = system

  # CPU usage (%)
  metrics['cpu'] = psutil.cpu_percent(interval=None)

  # Memory usage (%)
  memory = psutil.virtual_memory()
  metrics['ram'] = memory.percent

  # Disk I/O counters
  disk_io = psutil.disk_io_counters()
  metrics['disk_read_bytes'] = disk_io.read_bytes
  metrics['disk_write_bytes'] = disk_io.write_bytes
  metrics['disk_read_count'] = disk_io.read_count
  metrics['disk_write_count'] = disk_io.write_count

  # Disk I/O times (milliseconds or nanoseconds)
  time_unit = 'ns (approx)'
  if hasattr(disk_io, 'read_time_ns') and hasattr(disk_io, 'write_time_ns'):
      # Use nanoseconds if available
      metrics['disk_read_time'] = disk_io.read_time_ns
      metrics['disk_write_time'] = disk_io.write_time_ns
      time_unit = 'ns'
  else:
      # Convert milliseconds to nanoseconds for higher precision
      metrics['disk_read_time'] = disk_io.read_time * 1_000_000
      metrics['disk_write_time'] = disk_io.write_time * 1_000_000

  metrics['time_unit'] = time_unit

  # Network I/O counters
  net_io = psutil.net_io_counters()
  metrics['net_bytes_sent'] = net_io.bytes_sent
  metrics['net_bytes_recv'] = net_io.bytes_recv
  metrics['net_packets_sent'] = net_io.packets_sent
  metrics['net_packets_recv'] = net_io.packets_recv

  return metrics

def calculate_differences(start_metrics, end_metrics, interval):
  """
  Calculates differences between two metric snapshots.
  """
  differences = {}
  differences['timestamp'] = end_metrics['timestamp']
  differences['hostname'] = end_metrics['hostname']
  differences['system'] = end_metrics['system']

  # CPU usage (%)
  differences['cpu'] = end_metrics['cpu']

  # Memory usage (%)
  differences['ram'] = end_metrics['ram']

  # Disk throughput (MB/s)
  read_bytes_diff = end_metrics['disk_read_bytes'] - start_metrics['disk_read_bytes']
  write_bytes_diff = end_metrics['disk_write_bytes'] - start_metrics['disk_write_bytes']
  differences['d/r MB_s'] = (read_bytes_diff / (1024 ** 2)) / interval
  differences['d/w MB_s'] = (write_bytes_diff / (1024 ** 2)) / interval

  # Disk IOPS
  read_count_diff = end_metrics['disk_read_count'] - start_metrics['disk_read_count']
  write_count_diff = end_metrics['disk_write_count'] - start_metrics['disk_write_count']
  differences['d/r IOPS'] = read_count_diff / interval
  differences['d/w IOPS'] = write_count_diff / interval

  # Disk latency per operation
  read_time_diff = end_metrics['disk_read_time'] - start_metrics['disk_read_time']
  write_time_diff = end_metrics['disk_write_time'] - start_metrics['disk_write_time']
  time_unit = end_metrics['time_unit']

  # Prevent division by zero and consider parallel I/O operations
  if read_count_diff > 0:
      differences['d/r/lag_psutil'] = read_time_diff / read_count_diff
  else:
      differences['d/r/lag_psutil'] = 0

  if write_count_diff > 0:
      differences['d/w/lag_psutil'] = write_time_diff / write_count_diff
  else:
      differences['d/w/lag_psutil'] = 0

  differences['time_unit'] = time_unit

  # Network throughput (MB/s)
  bytes_sent_diff = end_metrics['net_bytes_sent'] - start_metrics['net_bytes_sent']
  bytes_recv_diff = end_metrics['net_bytes_recv'] - start_metrics['net_bytes_recv']
  differences['net/s MB_s'] = (bytes_sent_diff / (1024 ** 2)) / interval
  differences['net/r MB_s'] = (bytes_recv_diff / (1024 ** 2)) / interval

  # Network packets per second
  packets_sent_diff = end_metrics['net_packets_sent'] - start_metrics['net_packets_sent']
  packets_recv_diff = end_metrics['net_packets_recv'] - start_metrics['net_packets_recv']
  differences['net/s pkt_s'] = packets_sent_diff / interval
  differences['net/r pkt_s'] = packets_recv_diff / interval

  return differences

def measure_network_latency(ping_host):
  """
  Measures network latency to a specified host.

  Parameters:
  - ping_host: The host or IP address to ping.

  Returns:
  - The latency in milliseconds, or None if the ping failed.
  """
  if not ping_host:
      return None
  param = '-n' if platform.system().lower() == 'windows' else '-c'
  count = '1'
  try:
      output = subprocess.check_output(['ping', param, count, ping_host],
                                       universal_newlines=True, stderr=subprocess.STDOUT)
      if platform.system().lower() == 'windows':
          # Parse Windows ping output
          for line in output.split('\n'):
              if 'Average =' in line:
                  latency_ms = line.split('Average =')[-1].replace('ms', '').strip()
                  return float(latency_ms)
              elif 'Minimum = ' in line:
                  # For some locales
                  latency_ms = line.split('Minimum =')[-1].split(',')[0].replace('ms', '').strip()
                  return float(latency_ms)
      else:
          # Parse Linux ping output
          for line in output.split('\n'):
              if 'time=' in line:
                  latency_ms = line.split('time=')[1].split(' ms')[0].strip()
                  return float(latency_ms)
      return None
  except subprocess.CalledProcessError as e:
      print(f"Error pinging {ping_host}: {e.output}")
      return None
  except Exception as e:
      print(f"Error measuring network latency: {e}")
      return None

def measure_disk_latency_direct_io(testfile_path):
  """
  Measures disk latency using Direct I/O and synchronous reads (Linux only).

  Parameters:
  - testfile_path: The path to the test file used for the I/O operation.

  Returns:
  - The latency in nanoseconds, or None if the measurement failed.
  """
  system = platform.system()
  if system != 'Linux':
      print("Direct I/O latency measurement is only available on Linux.")
      return None

  try:
      import ctypes
      libc = ctypes.CDLL("libc.so.6")
  except Exception as e:
      print(f"Error loading libc: {e}")
      return None

  block_size = 4096  # 4KB block size
  align = 512  # Alignment for O_DIRECT

  # Allocate aligned memory
  buf = ctypes.c_void_p()
  ret = libc.posix_memalign(ctypes.byref(buf), align, block_size)
  if ret != 0:
      print("Error in posix_memalign")
      return None

  fd = None
  latency_ns = None
  try:
      # Open the file with O_DIRECT and O_SYNC
      fd = os.open(testfile_path, os.O_RDONLY | os.O_DIRECT | os.O_SYNC)
      # Read a block
      start_time = time.perf_counter()
      n = os.read(fd, block_size)
      end_time = time.perf_counter()
      latency_ns = (end_time - start_time) * 1e9  # Convert to nanoseconds
  except Exception as e:
      print(f"Error during Direct I/O read: {e}")
  finally:
      if fd:
          os.close(fd)
      libc.free(buf)
  return latency_ns

def write_to_csv(data, filename):
  """
  Writes data to a CSV file.

  Parameters:
  - data: A dictionary containing the data to write.
  - filename: The name of the CSV file.
  """
  fieldnames = data.keys()
  file_exists = os.path.isfile(filename)

  try:
      with open(filename, 'a', newline='') as csvfile:
          writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
          if not file_exists:
              writer.writeheader()
          writer.writerow(data)
  except Exception as e:
      print(f"Error writing to CSV file: {e}")

def main():
  args = parse_arguments()
  interval = args.interval
  output_file = args.outputfile
  stdout_flag = args.stdout.lower() == 'true'
  ping_host = args.pinghost.strip()
  testfile_path = args.testfile

  # Display script information
  print("System Metrics Collector")
  print("This script collects system metrics and measures disk latency using file operations with Direct I/O and synchronous reads (Linux only).")
  print("Usage: python script.py [--interval N] [--outputfile filename.csv] [--stdout true|false] [--pinghost hostname_or_ip] [--testfile /path/to/testfile]")
  print("Options:")
  print("  --interval       Interval in seconds between measurements (default: 60)")
  print("  --outputfile     Output CSV file name (default: system_metrics.csv)")
  print("  --stdout         Print output to console (true or false, default: true)")
  print("  --pinghost       Host/IP to ping for network latency measurement (optional)")
  print("  --testfile       Path to the test file for I/O latency measurement (default: /tmp/io_testfile)")
  print("Example:")
  print("  python script.py --interval 60 --outputfile metrics.csv --stdout true --pinghost 8.8.8.8 --testfile /tmp/io_testfile")
  print("Collecting system metrics...\n")

  # Check if the test file exists
  if not os.path.exists(testfile_path):
      print(f"The test file '{testfile_path}' does not exist. Please create a test file.")
      sys.exit(1)

  # Initial measurement
  start_metrics = collect_metrics()

  while True:
      # Measure network latency (optional)
      net_latency_ms = measure_network_latency(ping_host) if ping_host else None

      # Measure disk latency using Direct I/O (only on Linux)
      disk_latency_direct_io_ns = measure_disk_latency_direct_io(testfile_path)

      # Wait for the specified interval
      time.sleep(interval)

      # Next measurement
      end_metrics = collect_metrics()

      # Calculate differences
      differences = calculate_differences(start_metrics, end_metrics, interval)

      # Add network latency
      differences['net/lag'] = net_latency_ms if net_latency_ms is not None else 0

      # Add disk latency from Direct I/O read
      differences['d/lag_directio'] = disk_latency_direct_io_ns if disk_latency_direct_io_ns is not None else 0

      # Write data to CSV
      write_to_csv(differences, output_file)

      # Console output
      if stdout_flag:
          time_unit = differences.get('time_unit', 'ns')
          d_r_lag = differences.get('d/r/lag_psutil', 0)
          d_w_lag = differences.get('d/w/lag_psutil', 0)
          d_lag_directio = differences.get('d/lag_directio', 0)
          net_latency_str = f"{net_latency_ms:.2f} ms" if net_latency_ms is not None else "N/A"

          print(f"{differences['timestamp']} | CPU: {differences['cpu']}% | RAM: {differences['ram']}% | "
                f"d/r MB/s: {differences['d/r MB_s']:.2f} | d/w MB/s: {differences['d/w MB_s']:.2f} | "
                f"d/r IOPS: {differences['d/r IOPS']:.2f} | d/w IOPS: {differences['d/w IOPS']:.2f} | "
                f"d/r/lag_psutil: {d_r_lag:.2f} {time_unit}/op | d/w/lag_psutil: {d_w_lag:.2f} {time_unit}/op | "
                f"d/lag_directio: {d_lag_directio:.2f} ns | net/lag: {net_latency_str}")

      # Use the end metrics as start metrics for the next iteration
      start_metrics = end_metrics

if __name__ == '__main__':
  main()