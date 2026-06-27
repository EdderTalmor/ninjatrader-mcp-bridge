# NinjaTrader McpBridge AddOn - Installation Guide

## What You Need

- NinjaTrader 8 (any recent version)
- Windows laptop with Tailscale running
- The two `.cs` files:
  - `McpBridgeAddon.cs` - The HTTP bridge server AddOn
  - `QMcpChartIndicator.cs` - The chart data indicator (optional, for OHLCV access)

## Installation Steps

### Step 1: Import the AddOn into NinjaTrader

1. Open NinjaTrader 8
2. Go to **Control Center** → **Tools** → **Import** → **NinjaScript**
3. Select `McpBridgeAddon.cs` file
4. Click **Import**
5. Repeat for `QMcpChartIndicator.cs` (optional)

### Step 2: Enable the AddOn

1. Go to **Control Center** → **New** → **AddOns**
2. You should see "McpBridgeAddon" in the list
3. Click it to "launch" the AddOn (it starts the HTTP server)

Alternatively:
1. Go to **Tools** → **Options** → **NinjaScript** → **AddOns**
2. Ensure McpBridgeAddon is checked/enabled

### Step 3: Verify the Server is Running

Open a browser or PowerShell on the Windows laptop:

```powershell
# Test health endpoint
Invoke-RestMethod -Uri "http://localhost:7890/health"

# Test accounts
Invoke-RestMethod -Uri "http://localhost:7890/accounts"

# Test positions
Invoke-RestMethod -Uri "http://localhost:7890/positions"
```

You should see JSON responses.

### Step 4: Add the Chart Indicator (Optional, for OHLCV data)

1. Open a chart in NinjaTrader (e.g., ES 09-24, 5-minute timeframe)
2. Right-click the chart → **Indicators** → **Add**
3. Find "QMcpChartIndicator" in the list
4. Add it with default settings
5. The indicator will start feeding bar data to shared memory automatically

### Step 5: Configure for Tailscale Access

By default, the server listens on `http://+:7890/` which means all interfaces including Tailscale.

**Important:** If Windows Firewall prompts for access, allow it for port 7890.

To verify Tailscale connectivity from WSL:
```bash
# From WSL, test the laptop's Tailscale IP
curl http://100.91.249.72:7890/health
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Server status, counts |
| GET | `/accounts` | Trading accounts with balances |
| GET | `/positions` | All open positions |
| GET | `/orders` | All orders (working, pending, filled) |
| GET | `/quote?symbol=ES` | Current bid/ask/last price |
| GET | `/chart?symbol=ES&count=50&timeframe=5m` | OHLCV bar data (requires indicator) |
| POST | `/order` | Place an order (JSON body) |
| POST | `/cancel` | Cancel an order (JSON body with orderId) |
| POST | `/cancelall` | Cancel all working orders |

### Order Placement Example

```json
POST /order
{
  "symbol": "ES",
  "orderType": "market",
  "quantity": 1,
  "tif": "day"
}
```

For sell/short, use negative quantity:
```json
{
  "symbol": "ES",
  "orderType": "market",
  "quantity": -1
}
```

## Troubleshooting

### "Connection refused" from WSL
- Check Windows Firewall: allow incoming TCP on port 7890
- Verify Tailscale is running on both machines
- Test `ping 100.91.249.72` from WSL

### AddOn doesn't appear in NinjaTrader
- Make sure the file compiles: open NinjaScript Editor, check for compilation errors
- The namespace must be `NinjaTrader.NinjaScript.AddOns` for AddOns

### No data in /positions or /orders
- Make sure you have an account connected in NinjaTrader
- The AddOn reads from `Core.Globals.Accounts` which requires an active connection

### /chart returns error
- QMcpChartIndicator must be added to a chart for the symbol
- The indicator only works when the chart is active and receiving data

## Architecture

```
┌─────────────────────┐     Tailscale      ┌─────────────────────────┐
│   WSL (Hermes)      │ ◄────────────────► │   Windows Laptop        │
│                     │   100.91.249.72:7890│                         │
│  NinjaTrader MCP    │  HTTP over Tailscale│  NinjaTrader 8          │
│  Server             │                    │  ├─ McpBridgeAddon      │
│  (Node.js)          │                    │  │  (HTTP server :7890)  │
│                     │                    │  └─ QMcpChartIndicator  │
└─────────────────────┘                    └─────────────────────────┘
```

## Security Notes

- The HTTP server binds to all interfaces (`+`) — this is intentional for Tailscale access
- No authentication is implemented — rely on Tailscale's WireGuard encryption
- If exposing beyond Tailscale, add authentication or bind to specific interface
- Order placement is possible from any machine that can reach port 7890
