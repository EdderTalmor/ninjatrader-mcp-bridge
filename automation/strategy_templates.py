"""
Strategy templates for NT8 code generation.
These are starting points that Claude can modify and parameterize.
"""
import os
import textwrap

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def get_template(name):
    """Get a strategy template by name."""
    templates = {
        "ma_crossover": MA_CROSSOVER,
        "rsi_mean_reversion": RSI_MEAN_REVERSION,
        "breakout": BREAKOUT,
        "dual_sma_filter": DUAL_SMA_FILTER,
    }
    return templates.get(name, MA_CROSSOVER)


def save_template(name, output_dir, params=None):
    """Save a template to a .cs file with optional parameter overrides."""
    code = get_template(name)
    if params:
        for key, value in params.items():
            code = code.replace(f"{{{{{key}}}}}", str(value))
    
    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"{name}.cs")
    with open(filepath, "w") as f:
        f.write(code)
    return filepath


# ─── Templates ───────────────────────────────────────────────────────────────

MA_CROSSOVER = textwrap.dedent("""\
// <reference path="NinjaTrader.Core.dll"/>
// <reference path="NinjaTrader.Data.dll"/>
// <reference path="NinjaTrader.NinjaScript.dll"/>

using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class MACrossover : Strategy
    {
        private SMA fastSMA;
        private SMA slowSMA;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Moving Average Crossover Strategy";
                Name = "MACrossover";
                Calculate = Calculate.OnBarClose;
                BarsRequiredToTrade = 20;
            }
            else if (State == State.Configure)
            {
                fastSMA = SMA({{Fast}});
                slowSMA = SMA({{Slow}});

                AddChartIndicator(fastSMA);
                AddChartIndicator(slowSMA);
            }
            else if (State == State.DataLoaded)
            {
                fastSMA = SMA(Close, {{Fast}});
                slowSMA = SMA(Close, {{Slow}});
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < Math.Max({{Fast}}, {{Slow}}) + 1)
                return;

            // Fast crosses above slow → Buy
            if (fastSMA[1] <= slowSMA[1] && fastSMA[0] > slowSMA[0])
            {
                EnterLong(1, "LongEntry");
            }
            // Fast crosses below slow → Sell
            else if (fastSMA[1] >= slowSMA[1] && fastSMA[0] < slowSMA[0])
            {
                EnterShort(1, "ShortEntry");
            }
        }
    }
}
""")


RSI_MEAN_REVERSION = textwrap.dedent("""\
using System;
using System.ComponentModel;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class RSIMeanReversion : Strategy
    {
        private RSI rsi;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "RSI Mean Reversion Strategy";
                Name = "RSIMeanReversion";
                Calculate = Calculate.OnBarClose;
                BarsRequiredToTrade = 20;
            }
            else if (State == State.Configure)
            {
                rsi = RSI(Close, {{RSIPeriod}});
                AddChartIndicator(rsi);
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < {{RSIPeriod}} + 5)
                return;

            // Oversold → Buy
            if (rsi[0] < {{Oversold}})
            {
                EnterLong(1, "LongEntry");
            }
            // Overbought → Sell
            else if (rsi[0] > {{Overbought}})
            {
                EnterShort(1, "ShortEntry");
            }
        }
    }
}
""")


BREAKOUT = textwrap.dedent("""\
using System;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class BreakoutStrategy : Strategy
    {
        private Highest highIndicator;
        private Lowest lowIndicator;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Breakout Strategy";
                Name = "BreakoutStrategy";
                Calculate = Calculate.OnBarClose;
                BarsRequiredToTrade = 20;
            }
            else if (State == State.Configure)
            {
                highIndicator = Highest(High, {{Lookback}});
                lowIndicator = Lowest(Low, {{Lookback}});
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < {{Lookback}} + 1)
                return;

            // Break above recent high → Buy
            if (High[0] > highIndicator[1])
            {
                EnterLong(1, "LongBreakout");
            }
            // Break below recent low → Sell
            else if (Low[0] < lowIndicator[1])
            {
                EnterShort(1, "ShortBreakout");
            }
        }
    }
}
""")


DUAL_SMA_FILTER = textwrap.dedent("""\
using System;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class DualSMAFilter : Strategy
    {
        private SMA shortSMA;
        private SMA longSMA;
        private SMA trendSMA;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Dual SMA with Trend Filter";
                Name = "DualSMAFilter";
                Calculate = Calculate.OnBarClose;
                BarsRequiredToTrade = 50;
            }
            else if (State == State.Configure)
            {
                shortSMA = SMA(Close, {{ShortPeriod}});
                longSMA = SMA(Close, {{LongPeriod}});
                trendSMA = SMA(Close, {{TrendPeriod}});
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < {{TrendPeriod}} + 5)
                return;

            bool trendUp = Close[0] > trendSMA[0];
            bool trendDown = Close[0] < trendSMA[0];

            // Short crosses above long + uptrend → Buy
            if (shortSMA[1] <= longSMA[1] && shortSMA[0] > longSMA[0] && trendUp)
            {
                EnterLong(1, "LongEntry");
            }
            // Short crosses below long + downtrend → Sell
            else if (shortSMA[1] >= longSMA[1] && shortSMA[0] < longSMA[0] && trendDown)
            {
                EnterShort(1, "ShortEntry");
            }
        }
    }
}
""")


if __name__ == "__main__":
    # Generate all templates with default params
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    
    params = {
        "Fast": "10",
        "Slow": "25",
        "RSIPeriod": "14",
        "Oversold": "30",
        "Overbought": "70",
        "Lookback": "20",
        "ShortPeriod": "10",
        "LongPeriod": "25",
        "TrendPeriod": "50",
    }
    
    for name in ["ma_crossover", "rsi_mean_reversion", "breakout", "dual_sma_filter"]:
        path = save_template(name, TEMPLATES_DIR, params)
        print(f"Generated: {path}")
