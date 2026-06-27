#region Using declarations
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Text;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    /// <summary>
    /// QMcpChartIndicator - Feeds live chart OHLCV data to shared memory for the McpBridge HTTP server.
    ///
    /// How it works:
    /// 1. Add this indicator to a chart in NinjaTrader
    /// 2. Each bar close, it pushes OHLCV data into a static ConcurrentDictionary
    /// 3. The McpBridgeAddon reads this dictionary when /chart requests come in
    ///
    /// Usage:
    /// - Drop on a chart and it automatically makes that symbol's bar data accessible
    /// - Drop on multiple charts for multi-symbol data access
    ///
    /// The shared dictionary key is the instrument's FullName (e.g., "ES 09-24").
    /// Each value is the most recent N bars as OHLCV structs.
    /// </summary>
    public class QMcpChartIndicator : Indicator
    {
        // Static shared memory - accessible from the AddOn
        // Key: instrumentFullName, Value: ring buffer of recent bars
        public static readonly ConcurrentDictionary<string, McpChartBar[]> SharedCache =
            new ConcurrentDictionary<string, McpChartBar[]>();

        private const int MAX_BARS = 500;
        const int CACHE_ARRAY_SIZE = MAX_BARS;

        private string currentSymbol;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Feeds live chart OHLCV data to McpBridgeAddOn shared memory";
                Name = "QMcpChartIndicator";
                Calculate = Calculate.OnBarClose;
                IsOverlay = true;
                DisplayInDataBox = false;
                DrawOnPrice = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = true;
            }
            else if (State == State.DataLoaded)
            {
                currentSymbol = Instrument?.FullName ?? Instrument?.Name ?? "unknown";
                // Initialize shared cache for this symbol
                if (!string.IsNullOrEmpty(currentSymbol))
                {
                    SharedCache.TryAdd(currentSymbol, new McpChartBar[0]);
                }
            }
            else if (State == State.Terminated)
            {
                // Clean up this symbol's cache
                if (!string.IsNullOrEmpty(currentSymbol))
                {
                    McpChartBar[] removed;
                    SharedCache.TryRemove(currentSymbol, out removed);
                }
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < 2) return;

            try
            {
                string symbol = Instrument?.FullName ?? Instrument?.Name ?? currentSymbol;

                // Build array of recent bars up to MAX_BARS
                int barCount = Math.Min(CurrentBar - 1, MAX_BARS);
                var bars = new McpChartBar[barCount];

                for (int i = 0; i < barCount; i++)
                {
                    int barIndex = CurrentBar - 2 - i; // Most recent completed bar first
                    if (barIndex < 1) break;

                    bars[i] = new McpChartBar
                    {
                        Time = Times[0][barIndex],
                        Open = Opens[0][barIndex],
                        High = Highs[0][barIndex],
                        Low = Lows[0][barIndex],
                        Close = Closes[0][barIndex],
                        Volume = Volumes[0][barIndex]
                    };
                }

                SharedCache[symbol] = bars;
            }
            catch (Exception)
            {
                // Silent failure - the bridge will handle missing data
            }
        }

        /// <summary>
        /// Gets chart bar data from shared memory for a given symbol.
        /// Called by the McpBridgeAddon when handling /chart requests.
        /// </summary>
        public static McpChartBar[] GetChartData(string symbol, int count = 100)
        {
            if (string.IsNullOrEmpty(symbol))
                return null;

            // Try exact match first
            McpChartBar[] bars;
            if (SharedCache.TryGetValue(symbol, out bars) && bars != null)
            {
                return bars.Take(Math.Min(count, bars.Length)).ToArray();
            }

            // Try partial match
            foreach (var kvp in SharedCache)
            {
                if (kvp.Key.IndexOf(symbol, StringComparison.OrdinalIgnoreCase) >= 0 ||
                    symbol.IndexOf(kvp.Key, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return kvp.Value.Take(Math.Min(count, kvp.Value.Length)).ToArray();
                }
            }

            return null;
        }

        /// <summary>
        /// Gets the most recent close price for a symbol from shared memory, or null if not available.
        /// </summary>
        public static double? GetLastPrice(string symbol)
        {
            var data = GetChartData(symbol, 1);
            if (data != null && data.Length > 0)
                return data[0].Close;
            return null;
        }

        /// <summary>
        /// Gets all symbols currently available in shared memory.
        /// </summary>
        public static string[] GetAvailableSymbols()
        {
            return SharedCache.Keys.ToArray();
        }
    }

    /// <summary>
    /// Lightweight struct for bar data in shared memory.
    /// Avoids holding references to NinjaScript arrays.
    /// </summary>
    public struct McpChartBar
    {
        public DateTime Time;
        public double Open;
        public double High;
        public double Low;
        public double Close;
        public double Volume;
    }
}
