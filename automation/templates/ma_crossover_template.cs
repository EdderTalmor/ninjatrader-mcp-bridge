using System;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;

namespace NinjaTrader.NinjaScript.Strategies
{
    public class {{CLASS_NAME}} : Strategy
    {
        private SMA fastSMA;
        private SMA slowSMA;

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
            }
            else if (State == State.Configure)
            {
                fastSMA = SMA({{FAST_PERIOD}});
                slowSMA = SMA({{SLOW_PERIOD}});
                AddChartIndicator(fastSMA);
                AddChartIndicator(slowSMA);
            }
            else if (State == State.DataRequired)
            {
                // Wait for indicators to be ready
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBar < {{SLOW_PERIOD}} + 1)
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
    }
}
