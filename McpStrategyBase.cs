#region Using declarations
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Globalization;
using NinjaTrader.Cbi;
using NinjaTrader.Core;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
using Newtonsoft.Json;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    /// <summary>
    /// McpStrategyBase — Base class for strategies that auto-export results.
    /// 
    /// Inherit from this class instead of Strategy to get automatic
    /// result export when running in the Strategy Analyzer.
    /// 
    /// Usage:
    ///   public class MyStrategy : McpStrategyBase
    ///   {
    ///       protected override void OnBarUpdate() { ... }
    ///   }
    /// 
    /// When the backtest completes, results are exported to:
    ///   Documents\NinjaTrader 8\bin\Custom\output\<StrategyName>\results.json
    /// </summary>
    public abstract class McpStrategyBase : Strategy
    {
        private string outputDir;
        private bool exported = false;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                OutputDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    @"NinjaTrader 8\bin\Custom\output");
            }
            else if (State == State.Terminated)
            {
                // Export on termination (which happens after backtest completes)
                if (!exported && IsInStrategyAnalyzer)
                {
                    ExportResults();
                }
            }
        }

        protected override void OnBarUpdate()
        {
            // Subclasses implement this
        }

        /// <summary>
        /// Called after the backtest completes and results are available.
        /// Override OnBarUpdate() instead of this.
        /// </summary>
        protected virtual void OnBacktestComplete()
        {
            ExportResults();
        }

        private void ExportResults()
        {
            try
            {
                exported = true;
                string strategyName = Name ?? "UnknownStrategy";
                string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
                string strategyDir = Path.Combine(OutputDir, strategyName);
                Directory.CreateDirectory(strategyDir);

                var result = new
                {
                    strategy = strategyName,
                    timestamp = timestamp,
                    trades = new List<object>()
                };

                // Try to read trades from the account
                if (Account != null)
                {
                    var trades = Account.GetOrders();
                    if (trades != null)
                    {
                        foreach (var order in trades)
                        {
                            if (order.Filled > 0)
                            {
                                result.trades.Add(new
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

                string jsonPath = Path.Combine(strategyDir, $"results_{timestamp}.json");
                string json = JsonConvert.SerializeObject(result, Formatting.Indented);
                File.WriteAllText(jsonPath, json);

                Print($"McpStrategyBase: Exported {result.trades.Count} trades to {jsonPath}");
            }
            catch (Exception ex)
            {
                Print($"McpStrategyBase Export Error: {ex.Message}");
            }
        }

        public string OutputDir
        {
            get { return outputDir; }
            set { outputDir = value; }
        }
    }
}
