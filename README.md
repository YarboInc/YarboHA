# Yarbo Home Assistant Integration

Home Assistant custom integration for Yarbo robot devices. Monitor and control your Yarbo Y Series robot directly from Home Assistant.

## Features

- Real-time device status via MQTT push (no polling)
- Heartbeat-based online detection (15s timeout)
- Auto wake-up and renewal — device stays active while connected
- Selective device setup — choose which devices to add
- Flexible device management — add or remove devices anytime via Options Flow
- Automatic session persistence and token refresh

## Supported Entities

### Monitoring

| Type | Entities |
|------|----------|
| **Sensors** | Battery, Error Code, Heart Beat State, Network (Halow/Wifi/4G), Head Type, Head Serial Number, Auto Plan Status, Auto Plan Pause Status, Recharging Status, Volume, RTK Signal, Position X/Y, Heading |
| **Binary Sensors** | Online, Charging, Sound Enabled, Headlight |
| **Device Tracker** | Real-time GPS location on HA map |
| **Map Zones** | GeoJSON zone visualization from device map data |

### Controls

| Type | Entities |
|------|----------|
| **Select** | Working State (standby/working), Plan Select |
| **Switch** | Sound Switch, Headlight |
| **Number** | Volume (0-100%), Plan Start Percent (0-99%) |
| **Button** | Start Plan, Pause Plan, Resume Plan, Stop Plan, Return to Charge, Refresh Plans, Refresh GPS Reference, Refresh Map Data, Refresh Device Data |

> **Start Plan** and **Return to Charge** perform safety precondition checks before executing. If a check fails, a clear error message is shown in the HA UI.

## Installation

### HACS (Recommended)

1. Open HACS → Custom repositories → Add `https://github.com/YarboInc/YarboHA` (Integration)
2. Search for "Yarbo" and install
3. Restart Home Assistant

### Manual

1. Copy `custom_components/yarbo/` to your HA `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. **Settings** > **Devices & Services** > **Add Integration** > **Yarbo**
2. Enter your Yarbo account email and password
3. Select which devices to add

To manage devices later: find Yarbo integration → **Configure** → check/uncheck devices.

## License

MIT
