# Yarbo Home Assistant Integration

Home Assistant custom integration for Yarbo robot devices. Monitor and control your Yarbo Y Series robot directly from Home Assistant.

## Features

- **Selective device setup** — choose which devices to add during initial configuration
- **Flexible device management** — add or remove devices anytime via Options Flow
- Real-time device status via MQTT push (no polling)
- Battery level, charging status, and RTK signal monitoring
- Working state monitoring and control
- Network status detection (Halow/Wifi/4G)
- Head type and serial number detection
- Auto plan management (select plan, set start progress, start/pause/resume/stop)
- Sound control (on/off switch, volume adjustment)
- Headlight control (on/off switch)
- One-click return to charge with safety preconditions
- Detailed error reporting with WP/REC error codes
- **Real-time GPS location on HA map** (device tracker with coordinate conversion)
- Map zone data with GeoJSON visualization
- **Heartbeat-based online detection** (15-second timeout)
- **Auto wake-up and renewal** — device stays active while connected
- GPS reference origin and map data refresh on demand
- Full DeviceMSG snapshot refresh
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

### Initial Setup

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration**
3. Search for **Yarbo**
4. Enter your Yarbo account email and password
5. Select which devices to add to Home Assistant (multi-select)
6. Only selected devices will have entities created and MQTT subscriptions active

### Managing Devices After Setup

1. Go to **Settings** > **Devices & Services**
2. Find the **Yarbo** integration and click **Configure**
3. The Options Flow shows all devices on your account with current selection
4. Check/uncheck devices to add or remove them
5. Submit to apply changes — the integration reloads automatically

> **Note:** Existing automations, dashboards, and history data for previously selected devices are preserved across changes (entity IDs are stable based on device serial number).

## Supported Entities

### Sensors (14)

| Entity | Description | Unit | Default |
|--------|-------------|------|---------|
| Battery | Battery capacity level | % | Enabled |
| Error Code | Current error code (0 = normal) | - | Enabled |
| Heart Beat State | Heart beat working state (standby/working) | - | Enabled |
| Network | Active network type (Halow/Wifi/4G) | - | Enabled |
| Head Type | Attached head type (None/Snow Blower/Blower/Mower/Smart Cover/Mower Pro) | - | Enabled |
| Head Serial Number | Attached head serial number | - | Enabled |
| Auto Plan Status | Plan execution status with detailed error codes (28 states including WP002-WP025) | - | Enabled |
| Auto Plan Pause Status | Plan pause reason (Not Paused/Manual Pause/Low Battery/E-Stop/Bumper/Stuck/Error) | - | Enabled |
| Recharging Status | Return-to-charge status with detailed error codes (14 states including REC errors) | - | Enabled |
| Volume | Current sound volume | % | Enabled |
| RTK Signal | RTK/GPS signal strength (Strong/Medium/Weak) | - | Enabled |
| Position X | Device local X coordinate | m | Disabled |
| Position Y | Device local Y coordinate | m | Disabled |
| Heading | Device heading angle | ° | Disabled |

> **Map Zones** sensor is also created per device, showing the number of map zone features with GeoJSON data in attributes.

### Binary Sensors (4)

| Entity | Description | Default |
|--------|-------------|---------|
| Online | Device connectivity (heartbeat-based, 15s timeout) | Enabled |
| Charging | Charging status (BatteryMSG.status > 1) | Enabled |
| Sound Enabled | Sound on/off state | Enabled |
| Headlight | Headlight on/off state (led_head > 0) | Enabled |

### Device Tracker (1)

| Entity | Description | Attributes |
|--------|-------------|------------|
| Location | Real-time GPS position on HA map | heading, position_x, position_y, gps_ref_latitude, gps_ref_longitude, rtk_fix_type |

The device tracker converts the robot's local odometry coordinates (CombinedOdom.x/y) to absolute GPS coordinates using a GPS reference origin fetched from the device. The entity is **unavailable** if the GPS reference has not been obtained or if `rtkFixType != 1` (device not initialized — complete setup via the Yarbo app first).

### Select Controls (2)

| Entity | Description | Options |
|--------|-------------|---------|
| Working State | Set device working state | standby, working |
| Plan Select | Select auto plan to execute | (dynamic from device) |

### Number Controls (2)

| Entity | Description | Range |
|--------|-------------|-------|
| Volume | Adjust sound volume | 0-100% |
| Plan Start Percent | Set plan start progress | 0-99% |

### Switch Controls (2)

| Entity | Description |
|--------|-------------|
| Sound Switch | Toggle sound on/off |
| Headlight | Toggle headlight on/off |

### Buttons (9)

| Entity | Description | Preconditions |
|--------|-------------|---------------|
| Start Plan | Start the selected auto plan | Online, plan selected, not charging, RTK OK, no plan running, not recharging |
| Pause Plan | Pause the current plan | - |
| Resume Plan | Resume the paused plan | - |
| Stop Plan | Stop the current plan | - |
| Return to Charge | Send device back to charging station | Online, not charging, not already recharging, RTK OK |
| Refresh Plans | Re-fetch auto plan list from device | - |
| Refresh GPS Reference | Re-fetch GPS reference origin from device | - |
| Refresh Map Data | Re-fetch map/zone data from device | - |
| Refresh Device Data | Re-fetch full device status snapshot | - |

> **Start Plan** and **Return to Charge** buttons perform safety checks before executing. If a precondition fails, a clear error message is shown in the HA UI (e.g., "Cannot start plan: RTK/GPS signal is weak").

## Auto Wake-up and Renewal

When the integration connects to a device, it automatically sends a wake-up command (`set_working_state {state:1, source:smart_home}`) and renews it every 4 minutes. This keeps the device actively reporting data.

If you manually set the working state to **standby**, the renewal stops. Setting it back to **working** resumes the renewal immediately.

## Error Reporting

The **Auto Plan Status** and **Recharging Status** sensors provide detailed error information when operations fail:

### Auto Plan Errors (on_going_planning)
| Code | Display |
|------|---------|
| -2 | Error: Create Plan History Failed (WP002) |
| -10 | Error: Plan Not Found (WP003) |
| -11 | Error: Failed to Read Plan (WP004) |
| -12 | Error: Failed to Calculate Route (WP005) |
| -20 | Error: Outside Mapped Area (WP006) |
| -21 | Error: Area Data Error (WP007) |
| -22 | Error: Route Data Error (WP008) |
| -23 | Error: In No-Go Zone |
| -24 | Error: Low Battery |
| -26 | Error: Module Position Failure (WP012) |
| -30 | Error: Location Data Exception (WP013) |
| -31 | Error: Docking Station Exception (WP014) |
| -43 | Error: Unable to Navigate Obstacle (WP016) |
| -44 | Error: Exceeded Boundary (WP017) |

### Recharging Errors (on_going_recharging)
| Code | Display |
|------|---------|
| -2 | Error: Server Error |
| -3 | Error: Direction Uninitialized |
| -4 | Error: Docking Station Uninitialized |
| -5 | Error: Recharge Failed (REC005) |
| -6 | Error: Failed to Park |
| -8 | Error: Docking Connection Failed |
| -9 | Error: Stuck |
| -20 | Error: Outside Mapped Area |

## How It Works

The integration uses the [Yarbo Data SDK](https://github.com/YarboInc/YarboDataSDK) to communicate with Yarbo devices:

1. **Authentication** — Logs in via the Yarbo cloud API with RSA-encrypted credentials
2. **Device Discovery** — Retrieves your device list from the cloud API
3. **Device Selection** — User selects which devices to monitor (stored in config entry options)
4. **Auto Wake-up** — Sends wake-up command on connect, renews every 4 minutes
5. **Initial Data Fetch** — Fetches plans, full device status snapshot, GPS reference, and map data
6. **Real-time Updates** — Connects to the MQTT broker for live status updates (deep merge preserves data across incremental pushes)
7. **Heartbeat Monitoring** — Tracks device online/offline status via heartbeat (15s timeout, 5s check interval)
8. **Token Lifecycle** — SDK auto-refreshes tokens on 401; if refresh token expires, HA triggers re-authentication
9. **Session Persistence** — Saves tokens to avoid re-login on Home Assistant restart

## License

MIT
