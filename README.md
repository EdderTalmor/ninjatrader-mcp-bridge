# NinjaTrader MCP Bridge

A NinjaScript AddOn that exposes an HTTP REST API, allowing AI agents and MCP servers to connect to NinjaTrader 8 for strategy analysis.

## Why

The [ninjatrader-mcp](https://github.com/ozmnf4/ninjatrader-mcp) MCP server provides Claude and other AI tools with trading capabilities — but it needs a local HTTP server running inside NinjaTrader to bridge the gap. The original author didn't release their AddOn source. This project fills that gap.

## What It Does

- **HTTP Server** running on port 7890 inside NinjaTrader
- **REST API** with endpoints for positions, orders, quotes, accounts, and chart data
- **Strategy analysis** focused (not for live execution)
- **Tailscale-ready** — listens on all interfaces, accessible over your mesh VPN

## Architecture

```
┌─────────────────────┐     Tailscale      ┌─────────────────────────┐
│   WSL / Claude      │ ◄────────────────► │   Windows Laptop        │
│                     │   HTTP over WG     │                         │
│  ninjatrader-mcp    │   100.x.x.x:7890   │  NinjaTrader 8          │
│  (Node.js)          │                    │  └─ McpBridgeAddon      │
└─────────────────────┘                    │     (HTTP server :7890) │
                                           └─────────────────────────┘
```

## Quick Start

### On your Windows laptop (NinjaTrader machine)

1. Download the two `.cs` files from this repo
2. Open NinjaTrader 8
3. **Tools → Import → NinjaScript** → select `McpBridgeAddon.cs`
4. Repeat for `QMcpChartIndicator.cs` (optional, for chart data)
5. Launch the AddOn: **New → AddOns → McpBridgeAddon**
6. Verify: `Invoke-RestMethod http://localhost:7890/api/health`

### On your AI agent machine (WSL, etc.)

```bash
git clone https://github.com/ozmnf4/ninjatrader-mcp.git
cd ninjatrader-mcp
npm install
# Point it at your laptop's Tailscale IP
NINJATRADER_MODE=local NINJATRADER_LOCAL_URL=http://<laptop-tailscale-ip>:7890 node src/server.js --test
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Server status |
| GET | `/api/accounts` | Account balances |
| GET | `/api/positions` | Open positions |
| GET | `/api/orders` | All orders |
| GET | `/api/quote?symbol=ES` | Bid/ask/last |
| GET | `/api/chart?symbol=ES&count=50` | OHLCV bars (needs indicator) |
| POST | `/api/order` | Place order (simulated) |
| POST | `/api/cancel` | Cancel order |
| POST | `/api/cancelall` | Cancel all orders |

## Files

| File | Purpose |
|------|---------|
| `McpBridgeAddon.cs` | The HTTP bridge AddOn — import this into NinjaTrader |
| `QMcpChartIndicator.cs` | Chart data feeder — add to charts for OHLCV access |
| `INSTALL.md` | Detailed installation guide |

## Building

These are NinjaScript files meant to be imported into NinjaTrader 8's built-in editor. No Visual Studio or MSBuild required. Just import and compile inside NT8.

## Compatibility

- NinjaTrader 8 (recent builds)
- Windows 10/11
- Works with simulated and live accounts (use simulated for analysis!)
- Compatible with [ninjatrader-mcp](https://github.com/ozmnf4/ninjatrader-mcp)

## License

MIT — use freely for your own trading projects.
