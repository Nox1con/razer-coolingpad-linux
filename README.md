# Razer Laptop Cooling Pad - Linux Fan Curve Controller

A Python script that gives you full fan curve control over the **Razer Laptop Cooling Pad** on Linux, bypassing Razer Synapse entirely.

The Razer Cooling Pad (USB `1532:0F43`) only officially supports Windows via Razer Synapse, which provides limited built-in fan profiles with non-linear behavior. This tool communicates directly with the pad over HID to set precise fan speeds based on your CPU temperature.

> HID protocol reverse-engineered from [FanControl.RazerCoolingPadPlugin](https://github.com/Benson5650/FanControl.RazerCoolingPadPlugin) (Windows/C#).

## Features

- Temperature-based fan curve with linear interpolation between points
- Auto-detects all thermal zones (`/sys/class/thermal/`)
- Hysteresis to prevent rapid fan speed toggling
- Auto-reconnects if the pad is unplugged and replugged
- Releases fan control back to the pad on exit (Ctrl+C / SIGTERM)
- Systemd service file included for running as a background daemon

## Supported Hardware

| Device | USB ID | Tested Firmware |
|--------|--------|----------------|
| Razer Laptop Cooling Pad | `1532:0F43` | v1.10.00_r1 |

Fan speed range: **500 - 3200 RPM** (50 RPM increments).

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/razer-coolingpad-linux.git
cd razer-coolingpad-linux
pip install -r requirements.txt
```

### System dependency

The `hidapi` Python package requires the `hidapi` C library. Install it for your distro:

```bash
# Arch / Manjaro
sudo pacman -S hidapi

# Debian / Ubuntu
sudo apt install libhidapi-hidraw0

# Fedora
sudo dnf install hidapi
```

### Permissions

The script needs access to the HID device. You can either:

**Option A:** Run with `sudo` (simplest)

**Option B:** Add a udev rule for unprivileged access:

```bash
echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1532", ATTRS{idProduct}=="0f43", MODE="0666"' | \
  sudo tee /etc/udev/rules.d/99-razer-coolingpad.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

After adding the udev rule, unplug and replug the pad (or reboot).

## Usage

### Quick commands

```bash
# List available temperature sensors
python3 razer-coolingpad-fancurve.py --list-sensors

# Run with a fan curve config
sudo python3 razer-coolingpad-fancurve.py -c curves/balanced.json -v

# Set a fixed RPM
sudo python3 razer-coolingpad-fancurve.py --set-rpm 2000

# Release control (pad returns to its default behavior)
sudo python3 razer-coolingpad-fancurve.py --off

# Generate a sample config to customize
python3 razer-coolingpad-fancurve.py --generate-config > my-curve.json
```

### Command-line options

| Flag | Description |
|------|-------------|
| `-c`, `--config FILE` | Path to a JSON fan curve config |
| `--list-sensors` | Show all thermal zones and their temperatures |
| `--set-rpm RPM` | Set a fixed fan speed (500-3200) and exit |
| `--off` | Release fan control and exit |
| `--interval SEC` | Polling interval in seconds (default: 3) |
| `--sensor PATH` | Specific thermal zone path (repeatable) |
| `--hysteresis DEG` | Temperature hysteresis in °C (default: 2) |
| `--generate-config` | Print a sample JSON config to stdout |
| `-v`, `--verbose` | Print temperature and RPM each cycle |

## Fan Curve Configs

Three example curves are provided in the `curves/` directory:

### `silent.json` - Prioritize quiet operation

Fans stay off until 65°C. Best for light workloads where you want silence.

| Temp | Fan % | ~RPM |
|------|-------|------|
| ≤65°C | 0% | 500 |
| 75°C | 15% | 900 |
| 85°C | 40% | 1600 |
| ≥95°C | 70% | 2400 |

### `balanced.json` - General-purpose daily use

Fans start ramping at 55°C. Good balance between noise and cooling.

| Temp | Fan % | ~RPM |
|------|-------|------|
| ≤55°C | 0% | 500 |
| 65°C | 25% | 1175 |
| 75°C | 55% | 1950 |
| 85°C | 80% | 2650 |
| ≥90°C | 100% | 3200 |

### `performance.json` - Maximum cooling

Fans start early and ramp aggressively. Best for gaming or sustained heavy loads.

| Temp | Fan % | ~RPM |
|------|-------|------|
| ≤45°C | 10% | 770 |
| 55°C | 35% | 1450 |
| 65°C | 60% | 2100 |
| 75°C | 85% | 2800 |
| ≥80°C | 100% | 3200 |

### Config format

```json
{
  "curve": [
    { "temp": 55, "percent": 0 },
    { "temp": 65, "percent": 25 },
    { "temp": 75, "percent": 55 },
    { "temp": 85, "percent": 80 },
    { "temp": 90, "percent": 100 }
  ],
  "interval": 3,
  "hysteresis": 2,
  "sensors": "auto"
}
```

| Field | Description |
|-------|-------------|
| `curve` | Array of `{temp, percent}` points. Temps between points are linearly interpolated. |
| `interval` | How often to poll temperature, in seconds. |
| `hysteresis` | Ignore temperature changes smaller than this (reduces fan speed flickering). |
| `sensors` | `"auto"` to use all thermal zones, or an array of sysfs paths. |

## Running as a systemd service

```bash
# Edit the service file to match your install path and preferred curve
nano razer-coolingpad.service

# Install and start
sudo cp razer-coolingpad.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now razer-coolingpad.service

# Check status / logs
sudo systemctl status razer-coolingpad.service
sudo journalctl -u razer-coolingpad.service -f

# Stop and disable
sudo systemctl disable --now razer-coolingpad.service
```

## How it works

The script sends 91-byte HID feature reports to the cooling pad. The protocol was reverse-engineered from the [Windows FanControl plugin](https://github.com/Benson5650/FanControl.RazerCoolingPadPlugin):

- Fan speed is encoded as `RPM / 50` in the report payload
- A XOR checksum is computed over the RPM byte
- A separate "off" command returns the pad to its default built-in behavior
- The pad has no physical RPM sensor — reported RPM reflects the last commanded speed

## Troubleshooting

**"Razer Cooling Pad not found"**
- Check `lsusb | grep 1532` — the pad should show as `1532:0f43`
- Make sure you're running with `sudo` or have the udev rule in place
- If another program (e.g. OpenRazer) has claimed the device, close it first

**"Could not read any temperature sensor"**
- Run `--list-sensors` to see what's available
- Use `--sensor /sys/class/thermal/thermal_zone0/temp` to target a specific one

**Fans not changing speed**
- The pad rounds to 50 RPM increments, small % changes may not produce a visible difference
- Use `-v` to see what's being sent each cycle

## Credits

The HID protocol used in this project was reverse-engineered by [Benson5650](https://github.com/Benson5650) in their Windows FanControl plugin:
[FanControl.RazerCoolingPadPlugin](https://github.com/Benson5650/FanControl.RazerCoolingPadPlugin). Without their work figuring out the USB communication, this Linux port would not exist.

## License

MIT
