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
using NinjaTrader.Gui;
using NinjaTrader.Gui.Tools;
using Newtonsoft.Json;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    /// McpResultExporter — Reads Strategy Analyzer results directly from NT8's
    /// internal data model and exports to JSON/CSV on backtest completion.
    /// 
    /// Usage: Add this AddOn to any NinjaTrader window. When a backtest completes
    /// in the Strategy Analyzer, it automatically exports results to:
    ///   Documents\NinjaTrader 8\bin\Custom\output\<StrategyName>\results.json
    /// </summary>
    public class McpResultExporter : AddOnBase
    {
        private string outputDir;
        private DateTime lastExport = DateTime.MinValue;
        private readonly TimeSpan debounceInterval = TimeSpan.FromSeconds(5);

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Exports Strategy Analyzer backtest results to JSON/CSV";
                Name = "McpResultExporter";
                OutputDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    @"NinjaTrader 8\bin\Custom\output");
            }
            else if (State == State.DataLoaded)
            {
                Directory.CreateDirectory(OutputDir);
            }
            else if (State == State.Terminated)
            {
                // Cleanup if needed
            }
        }

        protected override void OnWindowCreated(Window window)
        {
            // Watch for Strategy Analyzer window
            StrategyAnalyzer sa = window as StrategyAnalyzer;
            if (sa != null)
            {
                PrintOut("McpResultExporter: Found Strategy Analyzer window");
                
                // Subscribe to backtest completion events
                sa.StateChanged += OnAnalyzerStateChanged;
            }
        }

        protected override void OnWindowDestroyed(Window window)
        {
            StrategyAnalyzer sa = window as StrategyAnalyzer;
            if (sa != null)
            {
                sa.StateChanged -= OnAnalyzerStateChanged;
            }
        }

        private void OnAnalyzerStateChanged(object sender, EventArgs e)
        {
            try
            {
                var analyzer = sender as StrategyAnalyzer;
                if (analyzer == null) return;

                // Debounce to avoid multiple rapid triggers
                if (DateTime.Now - lastExport < debounceInterval)
                    return;

                // Check if a backtest just completed
                if (analyzer.CurrentState == StrategyAnalyzerState.BacktestComplete ||
                    analyzer.CurrentState == StrategyAnalyzerState.Idle)
                {
                    // Only export if we have results
                    if (analyzer.PerformanceMetrics == null || 
                        analyzer.PerformanceMetrics.Count == 0)
                        return;

                    lastExport = DateTime.Now;
                    ExportResults(analyzer);
                }
            }
            catch (Exception ex)
            {
                PrintOut($"McpResultExporter Error: {ex.Message}");
            }
        }

        private void ExportResults(StrategyAnalyzer analyzer)
        {
            try
            {
                string strategyName = analyzer.StrategyName ?? "UnknownStrategy";
                string timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
                
                // Create output directory for this strategy
                string strategyDir = Path.Combine(OutputDir, strategyName);
                Directory.CreateDirectory(strategyDir);

                // Build result object
                var result = new
                {
                    strategy = strategyName,
                    timestamp = timestamp,
                    instrument = analyzer?.Instrument?.FullName ?? "",
                    barType = analyzer?.BarsPeriod?.ToString() ?? "",
                    startDate = analyzer?.StartDate.ToString("yyyy-MM-dd") ?? "",
                    endDate = analyzer?.EndDate.ToString("yyyy-MM-dd") ?? "",
                    performance = new Dictionary<string, object>(),
                    trades = new List<object>()
                };

                // Extract performance metrics
                if (analyzer.PerformanceMetrics != null)
                {
                    var perf = analyzer.PerformanceMetrics;
                    result.performance["totalNetProfit"] = perf.TotalNetProfit.ToString("C");
                    result.performance["grossProfit"] = perf.GrossProfit.ToString("C");
                    result.performance["grossLoss"] = perf.GrossLoss.ToString("C");
                    result.performance["commission"] = perf.Commission.ToString("C");
                    result.performance["profitFactor"] = perf.ProfitFactor.ToString("F2");
                    result.performance["maxDrawdown"] = perf.MaxDrawdown.ToString("C");
                    result.performance["sharpeRatio"] = perf.SharpeRatio.ToString("F2");
                    result.performance["sortinoRatio"] = perf.SortinoRatio.ToString("F2");
                    result.performance["ulcerIndex"] = perf.UlcerIndex.ToString("F2");
                    result.performance["rSquared"] = perf.RSquared.ToString("F2");
                    result.performance["totalTrades"] = perf.TotalTrades.ToString();
                    result.performance["percentProfitable"] = perf.PercentProfitable.ToString("F2") + "%";
                    result.performance["winningTrades"] = perf.WinningTrades.ToString();
                    result.performance["losingTrades"] = perf.LosingTrades.ToString();
                    result.performance["avgTrade"] = perf.AvgTrade.ToString("C");
                    result.performance["avgWinningTrade"] = perf.AvgWinningTrade.ToString("C");
                    result.performance["avgLosingTrade"] = perf.AvgLosingTrade.ToString("C");
                    result.performance["maxConsecWinners"] = perf.MaxConsecutiveWinners.ToString();
                    result.performance["maxConsecLosers"] = perf.MaxConsecutiveLosers.ToString();
                    result.performance["largestWinningTrade"] = perf.LargestWinningTrade.ToString("C");
                    result.performance["largestLosingTrade"] = perf.LargestLosingTrade.ToString("C");
                    result.performance["avgTradesPerDay"] = perf.AvgTradesPerDay.ToString("F2");
                    result.performance["avgTimeInMarket"] = perf.AvgTimeInMarket.ToString();
                    result.performance["profitPerMonth"] = perf.ProfitPerMonth.ToString("C");
                    result.performance["totalFees"] = perf.TotalFees.ToString("C");
                }

                // Extract trade data
                if (analyzer.Trades != null)
                {
                    foreach (var trade in analyzer.Trades)
                    {
                        result.trades.Add(new
                        {
                            symbol = trade.Symbol,
                            direction = trade.IsLong ? "Long" : "Short",
                            quantity = trade.Quantity,
                            entryPrice = trade.EntryPrice.ToString("F2"),
                            exitPrice = trade.ExitPrice.ToString("F2"),
                            entryTime = trade.EntryTime.ToString("yyyy-MM-dd HH:mm"),
                            exitTime = trade.ExitTime.ToString("yyyy-MM-dd HH:mm"),
                            profitCurrency = trade.ProfitCurrency.ToString("C"),
                            commission = trade.Commission.ToString("C"),
                            slippage = trade.Slippage.ToString("F2"),
                            barsInTrade = trade.BarsInTrade.ToString(),
                            mae = trade.MAE.ToString("C"),
                            mfe = trade.MFE.ToString("C")
                        });
                    }
                }

                // Write JSON
                string jsonPath = Path.Combine(strategyDir, $"results_{timestamp}.json");
                string json = JsonConvert.SerializeObject(result, Formatting.Indented);
                File.WriteAllText(jsonPath, json);

                // Write CSV (summary only)
                string csvPath = Path.Combine(strategyDir, $"summary_{timestamp}.csv");
                WriteCsv(csvPath, analyzer);

                PrintOut($"McpResultExporter: Exported results to {jsonPath}");
                PrintOut($"  Strategy: {strategyName}, Trades: {analyzer.Trades?.Count ?? 0}");
            }
            catch (Exception ex)
            {
                PrintOut($"McpResultExporter Export Error: {ex.Message}");
                PrintOut(ex.StackTrace);
            }
        }

        private void WriteCsv(string path, StrategyAnalyzer analyzer)
        {
            using (var writer = new StreamWriter(path))
            {
                // Header
                writer.WriteLine("Metric,All Trades,Long Trades,Short Trades");

                // Performance rows
                if (analyzer.PerformanceMetrics != null)
                {
                    var p = analyzer.PerformanceMetrics;
                    writer.WriteLine($"Total net profit,{p.TotalNetProfit:C},,");
                    writer.WriteLine($"Gross profit,{p.GrossProfit:C},,");
                    writer.WriteLine($"Gross loss,{p.GrossLoss:C},,");
                    writer.WriteLine($"Commission,{p.Commission:C},,");
                    writer.WriteLine($"Profit factor,{p.ProfitFactor:F2},,");
                    writer.WriteLine($"Max. drawdown,{p.MaxDrawdown:C},,");
                    writer.WriteLine($"Sharpe ratio,{p.SharpeRatio:F2},,");
                    writer.WriteLine($"Sortino ratio,{p.SortinoRatio:F2},,");
                    writer.WriteLine($"Total # of trades,{p.TotalTrades},,");
                    writer.WriteLine($"Percent profitable,{p.PercentProfitable:F2}%,,");
                    writer.WriteLine($"Avg. trade,{p.AvgTrade:C},,");
                    writer.WriteLine($"Avg. winning trade,{p.AvgWinningTrade:C},,");
                    writer.WriteLine($"Avg. losing trade,{p.AvgLosingTrade:C},,");
                }
            }
        }

        private void PrintOut(string message)
        {
            if (Core.Globals.CurrentContext != null)
                Core.Globals.CurrentContext.Print("McpResultExporter", message);
            else
                System.Diagnostics.Debug.WriteLine(message);
        }

        public string OutputDir
        {
            get { return outputDir; }
            set { outputDir = value; }
        }
    }
}
