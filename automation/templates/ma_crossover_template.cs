// <reference path="NinjaTrader.Core.dll"/>
// <reference path="NinjaTrader.Data.dll"/>
// <reference path="NinjaTrader.NinjaScript.dll"/>
// <reference path="Newtonsoft.Json.dll"/>

using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
using Newtonsoft.Json;

namespace NinjaTrader.NinjaScript.Strategies
    public class {{CLASS_NAME}} : Strategy
    {
        private SMA fastSMA;
        private SMA slowSMA;
        private string outputDir;
        private bool exported = false;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "{{DESCRIPTION}}";
                Name = "{{CLASS_NAME}}";
                Calculate = Calculate.OnBarClose;
                BarsRequiredToTrade = {{BARS_REQUIRED}};
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                DefaultQuantity = 1;
                outputDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    @"NinjaTrader 8\bin\Custom\output");
            }
            else if (State == State.Configure)
            {
                fastSMA = SMA({{FAST_PERIOD}});
                slowSMA = SMA({{SLOW_PERIOD}});
                fastSMA.Plots[0].Brush = Brushes.Gold;
                slowSMA.Plots[0].Brush = Brushes.RoyalBlue;
                AddChartIndicator(fastSMA);
                AddChartIndicator(slowSMA);
            }
            else if (State == State.Historical)
            {
                // Running in Strategy Analyzer backtest
                Print($"MCP_EXPORT: Historical backtest started for {{CLASS_NAME}}");
            }
            else if (State == State.Realtime)
            {
                // Backtest completed — transition from Historical to Realtime
                Print($"MCP_EXPORT: Backtest complete for {{CLASS_NAME}}, exporting...");
                ExportResults();
            }
            else if (State == State.Terminated)
            {
                // Safety net: export if not already done
                if (!exported)
                {
                    ExportResults();
                }
            }
        }

        protected override void OnBarUpdate()
        {
            if (Current < {{SLOW_PERIOD}} + 1)
                return;

            // Fast crosses above slow → Buy
            if (fastSMA[1] <= slowSMA[1] && fastSMA[0] > slowSMA[0])
            {
                EnterLong(1, "Long");
            }
            // Fast crosses below slow → Sell
            else if (fastSMA[1] >= slowSMA[1] && fastSMA[0] < slowSMA[0])
            {
                EnterShort(1, "Short");
            }
        }

        private void ExportResults()
        {
            try
            {
                exported = true;
                string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
                string strategyDir = Path.Combine(outputDir, Name);
                Directory.CreateDirectory(strategyDir);

                var trades = new List<object>();

                // Read trades from the account
                if (Account != null)
                {
                    var orders = Account.GetOrders();
                    if (orders != null)
                    {
                        foreach (var order in orders)
                        {
                            if (order.Filled > 0)
                            {
                                trades.Add(new
                                {
                                    symbol = order.Instrument?.FullName ?? "",
                                    direction = order.IsLong ? "Long" : "Short",
                                    quantity = order.Quantity,
                                    filled = order.Filled,
                                    avgFillPrice = order.AverageFillPrice.ToString("F2"),
                                    entryTime = order.Time.ToString("yyyy-MM-dd HH:mm"),
                                    commission = order.Commission.ToString("C"),
                                    realizedPnL = order.RealizedProfitLoss.ToString("C")
                                });
                            }
                        }
                    }
                }

                var result = new
                {
                    strategy = Name,
                    timestamp = timestamp,
                    totalTrades = trades.Count,
                    trades = trades
                };

                string jsonPath = Path.Combine(strategyDir, $"results_{timestamp}.json");
                string json = Newtonsoft.Json.JsonConvert.SerializeObject(result, Newtonsoft.Json.Formatting.Indented);
                File.WriteAllText(jsonPath, json);

                Print($"MCP_EXPORT: Exported {trades.Count} trades to {jsonPath}");
            }
            catch (Exception ex)
            {
                Print($"MCP_EXPORT_ERROR: {ex.Message}");
            }
        }
    }
}
