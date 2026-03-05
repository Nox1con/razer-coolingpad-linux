#!/usr/bin/env python3
"""
Razer Laptop Cooling Pad fan curve controller for Linux.
Controls the Razer Cooling Pad (VID 0x1532, PID 0x0F43) fan speed
based on CPU temperature, using a configurable fan curve.

Usage:
  sudo python3 razer-coolingpad-fancurve.py              # run with default curve
  sudo python3 razer-coolingpad-fancurve.py --config curve.json  # custom curve
  sudo python3 razer-coolingpad-fancurve.py --list-sensors       # show thermal zones
  sudo python3 razer-coolingpad-fancurve.py --set-rpm 2000       # set fixed RPM
  sudo python3 razer-coolingpad-fancurve.py --off                # release control

Requires: pip install hidapi
Must run as root (or configure udev rules for unprivileged access).
"""

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

try:
    import hid
except ImportError:
    print("Error: hidapi not installed. Run: pip install hidapi", file=sys.stderr)
    sys.exit(1)

# ── Device constants (from Program.cs) ──
VID = 0x1532
PID = 0x0F43
REPORT_LEN = 91
REPORT_ID = 0x00
MIN_RPM = 500
MAX_RPM = 3200

# Byte offsets within the 91-byte report (0-indexed, buf[0] = report ID)
IDX_REPORT_CODE = 8
IDX_SUB_VER = 9
IDX_CURVE_ID = 10
IDX_RPM_L = 11
IDX_RPM_H = 12
IDX_CHK_L = 89
IDX_CHK_H = 90

# Base header (bytes 1..90 of the report)
HEADER = bytearray([
    0x00, 0x02, 0x00, 0x00, 0x00, 0x03, 0x0D, 0x10, 0x01, 0x02,
    0x36, 0x00,
] + [0x00] * 78)

# ── Default fan curve: (temp_celsius, fan_percent) ──
# Linear interpolation between points. Below first point = first point's %.
# Above last point = last point's %.
DEFAULT_CURVE = [
    (40, 0),
    (50, 20),
    (60, 40),
    (70, 60),
    (80, 80),
    (90, 100),
]

DEFAULT_POLL_INTERVAL = 3  # seconds


def find_thermal_zones():
    """Discover all thermal zones and their types."""
    zones = []
    thermal_dir = Path("/sys/class/thermal")
    if not thermal_dir.exists():
        return zones
    for zone in sorted(thermal_dir.glob("thermal_zone*")):
        type_file = zone / "type"
        temp_file = zone / "temp"
        if type_file.exists() and temp_file.exists():
            name = type_file.read_text().strip()
            zones.append({"path": str(temp_file), "name": name, "zone": zone.name})
    return zones


def read_temp(sensor_path):
    """Read temperature in Celsius from a sysfs thermal zone."""
    try:
        raw = Path(sensor_path).read_text().strip()
        return int(raw) / 1000.0
    except (OSError, ValueError):
        return None


def get_max_temp(sensor_paths):
    """Read the highest temperature across all specified sensors."""
    temps = [read_temp(p) for p in sensor_paths]
    valid = [t for t in temps if t is not None]
    return max(valid) if valid else None


def interpolate_curve(curve, temp):
    """Linearly interpolate fan percentage from the curve for a given temperature."""
    if temp <= curve[0][0]:
        return curve[0][1]
    if temp >= curve[-1][0]:
        return curve[-1][1]
    for i in range(len(curve) - 1):
        t0, p0 = curve[i]
        t1, p1 = curve[i + 1]
        if t0 <= temp <= t1:
            ratio = (temp - t0) / (t1 - t0)
            return p0 + ratio * (p1 - p0)
    return curve[-1][1]


def percent_to_rpm(percent):
    """Convert 0-100% to RPM in the 500-3200 range, rounded to nearest 50."""
    percent = max(0.0, min(100.0, percent))
    rpm = MIN_RPM + percent / 100.0 * (MAX_RPM - MIN_RPM)
    return int(round(rpm / 50.0)) * 50


def build_set_rpm_report(rpm):
    """Build a 91-byte HID feature report to set fan RPM."""
    rpm = max(MIN_RPM, min(MAX_RPM, rpm))
    buf = bytearray(REPORT_LEN)
    buf[0] = REPORT_ID
    buf[1:91] = HEADER

    buf[IDX_REPORT_CODE] = 0x01
    buf[IDX_SUB_VER] = 0x01
    buf[IDX_CURVE_ID] = 0x05

    raw = int(round(rpm / 50.0))
    buf[IDX_RPM_L] = raw & 0xFF
    buf[IDX_RPM_H] = (raw >> 8) & 0xFF

    buf[IDX_CHK_L] = buf[IDX_RPM_L] ^ 0x0B
    buf[IDX_CHK_H] = 0x00

    return bytes(buf)


def build_off_report():
    """Build a 91-byte HID feature report to release fan control."""
    buf = bytearray(REPORT_LEN)
    buf[0] = REPORT_ID
    buf[1:91] = HEADER

    buf[IDX_REPORT_CODE] = 0x10
    buf[IDX_SUB_VER] = 0x00
    buf[IDX_CURVE_ID] = 0x06
    buf[IDX_RPM_L] = 0x00
    buf[IDX_RPM_H] = 0x00
    buf[IDX_CHK_L] = 0x18
    buf[IDX_CHK_H] = 0x00

    return bytes(buf)


def open_device():
    """Open the Razer cooling pad HID device. Returns hid.device or None."""
    try:
        dev = hid.device()
        dev.open(VID, PID)
        return dev
    except IOError:
        return None


def send_feature_report(dev, report):
    """Send a feature report to the device."""
    dev.send_feature_report(report)


def read_rpm(dev):
    """Read the current RPM from the device."""
    try:
        data = dev.get_feature_report(REPORT_ID, REPORT_LEN)
        raw = data[IDX_RPM_L] | (data[IDX_RPM_H] << 8)
        return raw * 50
    except IOError:
        return None


def load_config(path):
    """Load fan curve config from JSON file."""
    with open(path) as f:
        cfg = json.load(f)
    config = {}
    if "curve" in cfg:
        config["curve"] = [(point["temp"], point["percent"]) for point in cfg["curve"]]
    if "interval" in cfg:
        config["interval"] = cfg["interval"]
    if "sensors" in cfg:
        config["sensors"] = cfg["sensors"]
    if "hysteresis" in cfg:
        config["hysteresis"] = cfg["hysteresis"]
    return config


def generate_sample_config():
    """Return a sample config dict."""
    return {
        "curve": [{"temp": t, "percent": p} for t, p in DEFAULT_CURVE],
        "interval": DEFAULT_POLL_INTERVAL,
        "hysteresis": 2,
        "sensors": "auto",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Razer Laptop Cooling Pad fan curve controller"
    )
    parser.add_argument("--config", "-c", help="Path to JSON config file")
    parser.add_argument("--list-sensors", action="store_true", help="List thermal zones and exit")
    parser.add_argument("--set-rpm", type=int, metavar="RPM", help="Set a fixed RPM (500-3200) and exit")
    parser.add_argument("--off", action="store_true", help="Release fan control (return to pad default) and exit")
    parser.add_argument("--interval", type=float, default=None, help="Poll interval in seconds (default: 3)")
    parser.add_argument("--sensor", action="append", help="Thermal zone path(s) to use (default: all)")
    parser.add_argument("--hysteresis", type=float, default=None, help="Temp hysteresis in °C (default: 2)")
    parser.add_argument("--generate-config", action="store_true", help="Print sample JSON config and exit")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    if args.list_sensors:
        zones = find_thermal_zones()
        if not zones:
            print("No thermal zones found.")
            return
        for z in zones:
            temp = read_temp(z["path"])
            print(f"  {z['zone']:20s}  {z['name']:20s}  {temp:6.1f}°C  ({z['path']})")
        return

    if args.generate_config:
        print(json.dumps(generate_sample_config(), indent=2))
        return

    # Load config
    curve = DEFAULT_CURVE
    interval = DEFAULT_POLL_INTERVAL
    hysteresis = 2.0
    sensor_paths = None

    if args.config:
        cfg = load_config(args.config)
        curve = cfg.get("curve", curve)
        interval = cfg.get("interval", interval)
        hysteresis = cfg.get("hysteresis", hysteresis)
        if "sensors" in cfg and cfg["sensors"] != "auto":
            sensor_paths = cfg["sensors"]

    if args.interval is not None:
        interval = args.interval
    if args.hysteresis is not None:
        hysteresis = args.hysteresis
    if args.sensor:
        sensor_paths = args.sensor

    # Auto-detect sensors if not specified
    if sensor_paths is None:
        zones = find_thermal_zones()
        sensor_paths = [z["path"] for z in zones]
        if not sensor_paths:
            print("Error: No thermal zones found. Specify --sensor manually.", file=sys.stderr)
            sys.exit(1)

    # Validate curve is sorted
    curve.sort(key=lambda x: x[0])

    # Open device
    dev = open_device()
    if dev is None:
        print("Error: Razer Cooling Pad not found (VID=0x1532, PID=0x0F43).", file=sys.stderr)
        print("Make sure the pad is connected and you have permissions (try sudo).", file=sys.stderr)
        sys.exit(1)

    print(f"Connected to Razer Cooling Pad")

    # One-shot modes
    if args.off:
        send_feature_report(dev, build_off_report())
        print("Fan control released (pad returns to default behavior).")
        dev.close()
        return

    if args.set_rpm is not None:
        rpm = max(MIN_RPM, min(MAX_RPM, args.set_rpm))
        rpm = int(round(rpm / 50.0)) * 50
        send_feature_report(dev, build_set_rpm_report(rpm))
        print(f"Fan set to {rpm} RPM.")
        dev.close()
        return

    # Fan curve loop
    print(f"Fan curve active (interval={interval}s, hysteresis={hysteresis}°C)")
    print(f"Curve points: {curve}")
    print(f"Sensors: {sensor_paths}")
    print("Press Ctrl+C to stop (will release fan control on exit).\n")

    last_rpm = None
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while running:
            temp = get_max_temp(sensor_paths)
            if temp is None:
                if args.verbose:
                    print("Warning: Could not read any temperature sensor")
                time.sleep(interval)
                continue

            percent = interpolate_curve(curve, temp)
            rpm = percent_to_rpm(percent)

            # Apply hysteresis: only change if RPM differs by more than one step
            # or if temperature is driving fans up
            if last_rpm is not None and abs(rpm - last_rpm) < 50 * 1:
                rpm = last_rpm

            if rpm != last_rpm:
                try:
                    send_feature_report(dev, build_set_rpm_report(rpm))
                    last_rpm = rpm
                except IOError:
                    print("Device disconnected, attempting reconnect...", file=sys.stderr)
                    dev.close()
                    dev = None
                    while running and dev is None:
                        time.sleep(2)
                        dev = open_device()
                    if dev:
                        print("Reconnected.")
                        last_rpm = None
                    continue

            if args.verbose:
                current_rpm = read_rpm(dev) if dev else None
                print(f"Temp: {temp:5.1f}°C → {percent:5.1f}% → {rpm} RPM (reported: {current_rpm})")

            time.sleep(interval)
    finally:
        if dev:
            try:
                send_feature_report(dev, build_off_report())
                print("\nFan control released.")
            except Exception:
                pass
            dev.close()


if __name__ == "__main__":
    main()
