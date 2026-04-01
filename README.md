# Yarbo Home Assistant Integration

Home Assistant custom integration for Yarbo robot devices. Monitor and control your Yarbo Y Series robot directly from Home Assistant.

## Features

- Real-time device status via MQTT push (with REST polling fallback)
- Battery level, charging status, and temperature monitoring
- Working state monitoring and control
- RTK satellite status
- Head type detection (snow blower, leaf blower, mower, smart cover)
- Position tracking and ultrasonic sensor data
- Automatic session persistence and token refresh

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click the three dots menu in the top right corner
3. Select **Custom repositories**
4. Add the repository URL: `https://github.com/YarboInc/YarboHA`
5. Select **Integration** as the category
6. Click **Add**
7. Search for "Yarbo" in the HACS store and install it
8. Restart Home Assistant

### Manual Installation

1. Download the latest release from [GitHub](https://github.com/YarboInc/YarboHA/releases)
2. Copy the `custom_components/yarbo/` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration**
3. Search for **Yarbo**
4. Enter your Yarbo account email and password
5. The integration will automatically discover your devices and create entities

## Supported Entities

### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| Battery | Battery capacity level | % |
| Battery Status | Battery health status (normal/low/critical) | - |
| Working State | Current working state (idle/working/paused/error/returning) | - |
| Heart Beat State | Heart beat working state (standby/working) | - |
| Error Code | Current error code | - |
| Base Status | Base station status | - |
| Head Type | Attached head type (snow blower/leaf blower/mower/smart cover) | - |
| Head Serial Number | Attached head serial number | - |
| RTK Status | RTK satellite fix status (no fix/single/float/fixed) | - |
| RTK Heading Status | RTK heading status | - |
| RTCM Age | RTCM correction data age | s |
| Position X | Device X coordinate | m |
| Position Y | Device Y coordinate | m |
| Heading | Device heading angle | ° |
| Position Confidence | Position confidence level | - |
| Chute Angle | Snow chute rotation angle | ° |
| Ultrasonic Left Front | Left front ultrasonic distance | mm |
| Ultrasonic Middle | Middle ultrasonic distance | mm |
| Ultrasonic Right Front | Right front ultrasonic distance | mm |

### Binary Sensors

| Entity | Description |
|--------|-------------|
| Online | Device connectivity status |
| Battery Temp Error | Battery temperature error |
| Charging | Charging status |

### Select (Controls)

| Entity | Description | Options |
|--------|-------------|---------|
| Working State | Set device working state | standby, working |

## How It Works

The integration uses the [Yarbo Data SDK](https://github.com/YarboInc/YarboDataSDK) to communicate with Yarbo devices:

1. **Authentication** — Logs in via the Yarbo cloud API with RSA-encrypted credentials
2. **Device Discovery** — Retrieves your device list from the cloud API
3. **Real-time Updates** — Connects to the MQTT broker for live status updates
4. **REST Fallback** — Polls the REST API every 5 minutes if MQTT is unavailable
5. **Session Persistence** — Saves tokens to avoid re-login on Home Assistant restart

## License

MIT
