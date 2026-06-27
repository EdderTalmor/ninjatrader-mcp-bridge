#region Using declarations
using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using NinjaTrader.Cbi;
using NinjaTrader.Core;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.AddOns
{
    /// <summary>
    McpBridgeAddon - HTTP bridge server for NinjaTrader 8
    Exposes REST API on configurable port (default 7890) for MCP servers and external tools.
    
    Endpoints:
      GET  /health           - Server status, counts
      GET  /accounts         - List accounts with balances
      GET  /positions        - All open positions
      GET  /orders           - All orders (working, pending, filled)
      GET  /quote?symbol=X   - Current bid/ask/last for symbol
      GET  /chart?symbol=X&count=N&timeframe=T - OHLCV bar data
      POST /order            - Place an order
      POST /cancel           - Cancel an order by ID
      POST /cancelall       - Cancel all working orders
    </summary>
    public class McpBridgeAddon : AddOnBase
    {
        private HttpListener httpListener;
        private Thread listenerThread;
        private volatile bool isRunning;
        private int port = 7890;

        // Cache of snapshot data - refreshed periodically
        private volatile string cachedHealth = "{}";
        private volatile string cachedPositions = "[]";
        private volatile string cachedOrders = "[]";
        private volatile string cachedAccounts = "[]";
        private volatile long lastRefreshTicks;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "HTTP bridge server for MCP/external tool access to NinjaTrader";
                Name = "McpBridgeAddon";
                Port = 7890;
            }
            else if (State == State.Configure)
            {
                // Nothing additional to configure
            }
            else if (State == State.DataLoaded)
            {
                StartHttpServer();
                RefreshAllCaches();
            }
            else if (State == State.Terminated)
            {
                StopHttpServer();
            }
        }

        /// <summary>
        /// Called periodically by a timer to refresh cached data.
        /// Alternatively, caches are refreshed on demand when requests come in.
        /// </summary>
        public void RefreshCache()
        {
            RefreshAllCaches();
        }

        // ─── HTTP Server ──────────────────────────────────────────

        private void StartHttpServer()
        {
            try
            {
                httpListener = new HttpListener();
                httpListener.Prefixes.Add(string.Format("http://+:{0}/", Port));
                httpListener.Start();
                isRunning = true;

                listenerThread = new Thread(ListenLoop)
                {
                    IsBackground = true,
                    Name = "McpBridgeHttpListener"
                };
                listenerThread.Start();
            }
            catch (Exception ex)
            {
                // Can't use Print here since we might not be on the UI thread
                System.Diagnostics.Debug.WriteLine("McpBridgeAddon: Failed to start HTTP server: " + ex.Message);
            }
        }

        private void StopHttpServer()
        {
            isRunning = false;
            try
            {
                if (httpListener != null)
                    httpListener.Stop();
            }
            catch { }
            httpListener = null;

            // Wait for listener thread to finish
            if (listenerThread != null && listenerThread.IsAlive)
            {
                listenerThread.Join(1000);
            }
        }

        private void ListenLoop()
        {
            while (isRunning && httpListener != null)
            {
                try
                {
                    var context = httpListener.GetContext();
                    ThreadPool.QueueUserWorkItem(ProcessRequest, context);
                }
                catch (HttpListenerException)
                {
                    break; // Stopped
                }
                catch (Exception ex)
                {
                    if (isRunning)
                        System.Diagnostics.Debug.WriteLine("McpBridgeAddon: Listener error: " + ex.Message);
                }
            }
        }

        private void ProcessRequest(object state)
        {
            var context = (HttpListenerContext)state;
            try
            {
                var request = context.Request;
                var response = context.Response;

                // CORS support
                response.Headers.Add("Access-Control-Allow-Origin", "*");
                response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
                response.Headers.Add("Access-Control-Allow-Headers", "Content-Type");

                if (request.HttpMethod == "OPTIONS")
                {
                    response.StatusCode = 204;
                    response.Close();
                    return;
                }

                string path = request.Url.AbsolutePath.Trim('/').ToLowerInvariant();
                int statusCode = 200;
                string responseJson;

                switch (path)
                {
                    case "api/health":
                        responseJson = cachedHealth;
                        break;
                    case "api/positions":
                        cachedPositions = BuildPositionsJson();
                        responseJson = cachedPositions;
                        break;
                    case "api/orders":
                        cachedOrders = BuildOrdersJson();
                        responseJson = cachedOrders;
                        break;
                    case "api/accounts":
                        cachedAccounts = BuildAccountsJson();
                        responseJson = cachedAccounts;
                        break;
                    case "api/quote":
                        string symbol = request.QueryString["symbol"];
                        if (string.IsNullOrEmpty(symbol))
                        {
                            responseJson = ErrorJson("symbol query parameter is required");
                            statusCode = 400;
                        }
                        else
                        {
                            responseJson = HandleQuote(symbol);
                        }
                        break;
                    case "api/chart":
                        string chartSymbol = request.QueryString["symbol"];
                        if (string.IsNullOrEmpty(chartSymbol))
                        {
                            responseJson = ErrorJson("symbol query parameter is required");
                            statusCode = 400;
                        }
                        else
                        {
                            int count = 100;
                            string countStr = request.QueryString["count"];
                            if (!string.IsNullOrEmpty(countStr))
                                int.TryParse(countStr, out count);

                            string tf = request.QueryString["timeframe"] ?? "5m";
                            responseJson = HandleChart(chartSymbol, count, tf);
                        }
                        break;
                    case "api/order":
                        if (request.HttpMethod != "POST")
                        {
                            responseJson = ErrorJson("POST method required");
                            statusCode = 405;
                        }
                        else
                        {
                            string body = ReadRequestBody(request);
                            responseJson = HandleOrder(body);
                        }
                        break;
                    case "api/cancel":
                        if (request.HttpMethod != "POST")
                        {
                            responseJson = ErrorJson("POST method required");
                            statusCode = 405;
                        }
                        else
                        {
                            string body = ReadRequestBody(request);
                            responseJson = HandleCancel(body);
                        }
                        break;
                    case "api/cancelall":
                        if (request.HttpMethod != "POST")
                        {
                            responseJson = ErrorJson("POST method required");
                            statusCode = 405;
                        }
                        else
                        {
                            responseJson = HandleCancelAll();
                        }
                        break;
                    default:
                        responseJson = ErrorJson("Unknown endpoint: " + path);
                        statusCode = 404;
                        break;
                }

                byte[] buffer = Encoding.UTF8.GetBytes(responseJson);
                response.ContentType = "application/json; charset=utf-8";
                response.StatusCode = statusCode;
                response.ContentLength64 = buffer.Length;
                response.OutputStream.Write(buffer, 0, buffer.Length);
                response.Close();
            }
            catch (Exception ex)
            {
                try
                {
                    var errorBytes = Encoding.UTF8.GetBytes(ErrorJson("Internal error: " + ex.Message));
                    context.Response.StatusCode = 500;
                    context.Response.OutputStream.Write(errorBytes, 0, errorBytes.Length);
                    context.Response.Close();
                }
                catch { }
            }
        }

        // ─── Cache Management ─────────────────────────────────────

        private void RefreshAllCaches()
        {
            try
            {
                System.Threading.Interlocked.Exchange(ref lastRefreshTicks, DateTime.UtcNow.Ticks);
                cachedHealth = BuildHealthJson();
                cachedAccounts = BuildAccountsJson();
                cachedPositions = BuildPositionsJson();
                cachedOrders = BuildOrdersJson();
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine("McpBridgeAddon: Cache refresh error: " + ex.Message);
            }
        }

        private string BuildHealthJson()
        {
            int accountCount = 0;
            int positionCount = 0;
            int orderCount = 0;

            try
            {
                if (Core.Globals.Accounts != null)
                {
                    foreach (var account in Core.Globals.Accounts)
                    {
                        accountCount++;
                        if (account.GetPositions() != null)
                            positionCount += account.GetPositions().Count;
                        if (account.GetOrders() != null)
                            orderCount += account.GetOrders().Count;
                    }
                }
            }
            catch { }

            return SimpleJson.SerializeObject(new
            {
                status = "ok",
                mode = "local",
                server = "McpBridgeAddon",
                time = DateTime.UtcNow.ToString("o"),
                accounts = accountCount,
                positions = positionCount,
                orders = orderCount
            });
        }

        private string BuildAccountsJson()
        {
            var accountsList = new List<object>();
            try
            {
                if (Core.Globals.Accounts == null)
                    return "[]";

                foreach (var account in Core.Globals.Accounts)
                {
                    accountsList.Add(new
                    {
                        name = account.Name,
                        balance = account.Get(AccountItem.TotalCashBalance, Currency.UsDollar),
                        unrealizedPnl = account.Get(AccountItem.UnrealizedProfitLoss, Currency.UsDollar),
                        buyingPower = account.Get(AccountItem.BuyingPower, Currency.UsDollar),
                        cashValue = account.Get(AccountItem.CashValue, Currency.UsDollar),
                        margin = account.Get(AccountItem.MaintenanceMargin, Currency.UsDollar)
                    });
                }
            }
            catch (Exception ex)
            {
                return ErrorJson("Error building accounts: " + ex.Message);
            }
            return SimpleJson.SerializeObject(new { accounts = accountsList });
        }

        private string BuildPositionsJson()
        {
            var positionsList = new List<object>();
            try
            {
                if (Core.Globals.Accounts == null)
                    return "{\"positions\":[]}";

                foreach (var account in Core.Globals.Accounts)
                {
                    var positions = account.Positions;
                    if (positions == null) continue;

                    foreach (var pos in positions)
                    {
                        string symbol = pos.Instrument != null ? (pos.Instrument.FullName ?? pos.Instrument.Name) : "unknown";
                        positionsList.Add(new
                        {
                            symbol = symbol,
                            quantity = pos.Quantity,
                            averagePrice = pos.AveragePrice,
                            marketPrice = pos.MarketPrice,
                            isLong = pos.IsLong,
                            account = account.Name,
                            exchange = pos.Exchange,
                            instrumentType = pos.InstrumentType.ToString()
                        });
                    }
                }
            }
            catch (Exception ex)
            {
                return ErrorJson("Error building positions: " + ex.Message);
            }
            return SimpleJson.SerializeObject(new { positions = positionsList });
        }

        private string BuildOrdersJson()
        {
            var ordersList = new List<object>();
            try
            {
                if (Core.Globals.Accounts == null)
                    return "{\"orders\":[]}";

                foreach (var account in Core.Globals.Accounts)
                {
                    var orders = account.Orders;
                    if (orders == null) continue;

                    foreach (var order in orders)
                    {
                        string symbol = order.Instrument != null ? (order.Instrument.FullName ?? order.Instrument.Name) : "unknown";
                        ordersList.Add(new
                        {
                            orderId = order.Id.ToString(),
                            symbol = symbol,
                            orderType = order.Name,
                            quantity = order.Quantity,
                            limitPrice = order.LimitPrice,
                            stopPrice = order.StopPrice,
                            filledQuantity = order.Filled,
                            state = order.State.ToString(),
                            timeInForce = order.TimeInForce.ToString(),
                            account = account.Name,
                            isLong = order.IsLong,
                            time = order.Time.ToString("o"),
                            averageFillPrice = order.AverageFillPrice
                        });
                    }
                }
            }
            catch (Exception ex)
            {
                return ErrorJson("Error building orders: " + ex.Message);
            }
            return SimpleJson.SerializeObject(new { orders = ordersList });
        }

        // ─── Live Quote Handler ───────────────────────────────────

        private string HandleQuote(string symbol)
        {
            try
            {
                Quote quote = null;

                // Try to find the instrument and get a quote
                if (Core.Globals.Instruments != null)
                {
                    var inst = Core.Globals.Instruments.FirstOrDefault(i =>
                        (i.Name != null && i.Name.Equals(symbol, StringComparison.OrdinalIgnoreCase)) ||
                        (i.FullName != null && i.FullName.Equals(symbol, StringComparison.OrdinalIgnoreCase)));

                    if (inst != null)
                        quote = Core.GetQuote(inst);
                }

                if (quote == null)
                {
                    // Try alternative lookup
                    foreach (var account in Core.Globals.Accounts ?? Enumerable.Empty<Account>())
                    {
                        var positions = account.GetPositions();
                        if (positions == null) continue;
                        var pos = positions.FirstOrDefault(p =>
                            p.Instrument != null &&
                            ((p.Instrument.Name != null && p.Instrument.Name.Equals(symbol, StringComparison.OrdinalIgnoreCase)) ||
                             (p.Instrument.FullName != null && p.Instrument.FullName.Equals(symbol, StringComparison.OrdinalIgnoreCase))));
                        if (pos != null)
                        {
                            quote = Core.GetQuote(pos.Instrument);
                            break;
                        }

                        var orders = account.GetOrders();
                        if (orders == null) continue;
                        var order = orders.FirstOrDefault(o =>
                            o.Instrument != null &&
                            ((o.Instrument.Name != null && o.Instrument.Name.Equals(symbol, StringComparison.OrdinalIgnoreCase)) ||
                             (o.Instrument.FullName != null && o.Instrument.FullName.Equals(symbol, StringComparison.OrdinalIgnoreCase))));
                        if (order != null)
                        {
                            quote = Core.GetQuote(order.Instrument);
                            break;
                        }
                    }
                }

                if (quote == null)
                {
                    return ErrorJson("No quote available for " + symbol);
                }

                return SimpleJson.SerializeObject(new
                {
                    symbol = quote.Instrument?.FullName ?? quote.Instrument?.Name ?? symbol,
                    bid = quote.Bid,
                    ask = quote.Ask,
                    last = quote.Last,
                    bidSize = quote.BidSize,
                    askSize = quote.AskSize,
                    volume = quote.Volume,
                    time = quote.Time.ToString("o")
                });
            }
            catch (Exception ex)
            {
                return ErrorJson("Quote error: " + ex.Message);
            }
        }

        // ─── Chart/Data Handler ───────────────────────────────────
        // Reads from QMcpChartIndicator.SharedCache (ConcurrentDictionary)
        // which is populated by the indicator running on charts.

        private string HandleChart(string symbol, int count, string timeframe)
        {
            try
            {
                var bars = QMcpChartIndicator.GetChartData(symbol, count);
                if (bars == null || bars.Length == 0)
                {
                    return ErrorJson("No chart data for " + symbol + ". Add QMcpChartIndicator to a chart for this symbol.");
                }

                var chartData = bars.Select(b => new
                {
                    time = b.Time.ToString("o"),
                    open = b.Open,
                    high = b.High,
                    low = b.Low,
                    close = b.Close,
                    volume = b.Volume
                }).ToList();

                return SimpleJson.SerializeObject(new
                {
                    symbol = symbol,
                    timeframe = timeframe,
                    barCount = chartData.Count,
                    bars = chartData
                });
            }
            catch (Exception ex)
            {
                return ErrorJson("Chart data error: " + ex.Message);
            }
        }

        // ─── Order Handlers ───────────────────────────────────────

        private string HandleOrder(string body)
        {
            try
            {
                // Parse the order request
                var req = SimpleJson.DeserializeObject<Dictionary<string, object>>(body);
                if (req == null)
                    return ErrorJson("Invalid JSON body");

                // Extract required fields
                string symbol = GetDictValue(req, "symbol");
                if (string.IsNullOrEmpty(symbol))
                    return ErrorJson("'symbol' is required");

                string accountName = GetDictValue(req, "account");
                string orderType = GetDictValue(req, "orderType") ?? "market";
                string quantityStr = GetDictValue(req, "quantity");
                string limitPriceStr = GetDictValue(req, "limitPrice");
                string stopPriceStr = GetDictValue(req, "stopPrice");
                string tif = GetDictValue(req, "tif") ?? "day";

                if (!int.TryParse(quantityStr, out int quantity) || quantity == 0)
                    return ErrorJson("'quantity' must be a non-zero integer");

                // Find the account
                Account account = null;
                foreach (var acc in Core.Globals.Accounts ?? Enumerable.Empty<Account>())
                {
                    if (string.IsNullOrEmpty(accountName) || acc.Name.Equals(accountName, StringComparison.OrdinalIgnoreCase))
                    {
                        account = acc;
                        break;
                    }
                }
                if (account == null)
                    return ErrorJson("No trading account found. " + (string.IsNullOrEmpty(accountName) ? "Multiple accounts available, specify 'account'." : "Account not found: " + accountName));

                // Find the instrument
                Instrument instrument = null;
                if (Core.Globals.Instruments != null)
                {
                    instrument = Core.Globals.Instruments.FirstOrDefault(i =>
                        (i.Name != null && i.Name.Equals(symbol, StringComparison.OrdinalIgnoreCase)) ||
                        (i.FullName != null && i.FullName.Equals(symbol, StringComparison.OrdinalIgnoreCase)));
                }
                if (instrument == null)
                {
                    try
                    {
                        instrument = Instrument.GetInstrument(symbol);
                    }
                    catch { }
                }
                if (instrument == null)
                    return ErrorJson("Instrument not found: " + symbol);

                // Determine direction: positive quantity = buy (long), negative = sell (short)
                bool isBuy = quantity > 0;
                quantity = Math.Abs(quantity);

                // Build the order
                Order order = null;
                string upperOrderType = orderType.ToUpperInvariant();
                TimeInForce tifEnum = TimeInForce.Gtc;

                // Parse time-in-force (NT8 supports: Day, Gtc, Gtd)
                if (!string.IsNullOrEmpty(tif))
                {
                    switch (tif.ToUpperInvariant())
                    {
                        case "GTC": tifEnum = TimeInForce.Gtc; break;
                        case "DAY": tifEnum = TimeInForce.Day; break;
                        case "GTD": tifEnum = TimeInForce.Gtd; break;
                    }
                }

                if (upperOrderType == "MARKET")
                {
                OrderAction action = isBuy ? OrderAction.Buy : OrderAction.Sell;
                order = account.CreateOrder(instrument, action, OrderType.Market, OrderEntry.Automated, tifEnum, quantity, 0, 0, "", "MCP", Core.Globals.MaxDate, null);
                }
                else if (upperOrderType == "LIMIT")
                {
                if (!double.TryParse(limitPriceStr, out double limitPrice) || limitPrice <= 0)
                    return ErrorJson("'limitPrice' required for limit orders");
                OrderAction action = isBuy ? OrderAction.Buy : OrderAction.Sell;
                order = account.CreateOrder(instrument, action, OrderType.Limit, OrderEntry.Automated, tifEnum, quantity, limitPrice, 0, "", "MCP", Core.Globals.MaxDate, null);
                }
                else if (upperOrderType == "STOPMARKET" || upperOrderType == "STOP_MARKET" || upperOrderType == "STOP")
                {
                if (!double.TryParse(stopPriceStr, out double stopPrice) || stopPrice <= 0)
                    return ErrorJson("'stopPrice' required for stop market orders");
                OrderAction action = isBuy ? OrderAction.Buy : OrderAction.Sell;
                order = account.CreateOrder(instrument, action, OrderType.StopMarket, OrderEntry.Automated, tifEnum, quantity, 0, stopPrice, "", "MCP", Core.Globals.MaxDate, null);
                }
                else if (upperOrderType == "STOPLIMIT" || upperOrderType == "STOP_LIMIT")
                {
                if (!double.TryParse(limitPriceStr, out double limPrice) || limPrice <= 0)
                    return ErrorJson("'limitPrice' required for stop limit orders");
                if (!double.TryParse(stopPriceStr, out double stPrice) || stPrice <= 0)
                    return ErrorJson("'stopPrice' required for stop limit orders");
                OrderAction action = isBuy ? OrderAction.Buy : OrderAction.Sell;
                order = account.CreateOrder(instrument, action, OrderType.StopLimit, OrderEntry.Automated, tifEnum, quantity, limPrice, stPrice, "", "MCP", Core.Globals.MaxDate, null);
                }
                else
                {
                    return ErrorJson("Unsupported order type: " + orderType + ". Use 'market', 'limit', 'stopMarket', or 'stopLimit'.");
                }

                if (order == null)
                    return ErrorJson("Failed to create order object");

                // Submit the order
                account.Submit(order);

                return SimpleJson.SerializeObject(new
                {
                    success = true,
                    orderId = order.Id.ToString(),
                    state = order.State.ToString(),
                    message = string.Format("Order submitted: {0} {1} {2} @ {3}", isBuy ? "BUY" : "SELL", quantity, symbol, orderType)
                });
            }
            catch (Exception ex)
            {
                return ErrorJson("Order submission error: " + ex.Message);
            }
        }

        private string HandleCancel(string body)
        {
            try
            {
                var req = SimpleJson.DeserializeObject<Dictionary<string, object>>(body);
                if (req == null)
                    return ErrorJson("Invalid JSON body");

                string orderId = GetDictValue(req, "orderId");
                if (string.IsNullOrEmpty(orderId))
                    return ErrorJson("'orderId' is required");

                if (!int.TryParse(orderId, out int orderIdInt))
                    return ErrorJson("'orderId' must be an integer");

                foreach (var account in Core.Globals.Accounts ?? Enumerable.Empty<Account>())
                {
                    var orders = account.Orders;
                    if (orders == null) continue;

                    var order = orders.FirstOrDefault(o => o.Id == orderIdInt);
                    if (order != null)
                    {
                        account.Cancel(order);
                        return SimpleJson.SerializeObject(new
                        {
                            success = true,
                            orderId = orderId,
                            state = order.State.ToString(),
                            message = "Order cancellation submitted"
                        });
                    }
                }

                return ErrorJson("Order not found: " + orderId);
            }
            catch (Exception ex)
            {
                return ErrorJson("Cancel error: " + ex.Message);
            }
        }

        private string HandleCancelAll()
        {
            int totalCancelled = 0;
            foreach (var account in Core.Globals.Accounts ?? Enumerable.Empty<Account>())
            {
                var orders = account.Orders;
                if (orders == null) continue;

                var workingOrders = orders.Where(o =>
                    o.State == OrderState.Working ||
                    o.State == OrderState.PendingSubmitted ||
                    o.State == OrderState.ChangePending ||
                    o.State == OrderState.CancelPending).ToList();

                foreach (var order in workingOrders)
                {
                    account.Cancel(order);
                    totalCancelled++;
                }
            }

            return SimpleJson.SerializeObject(new
            {
                success = true,
                cancelledCount = totalCancelled,
                message = string.Format("Cancelled {0} orders", totalCancelled)
            });
        }

        // ─── Utility Helpers ──────────────────────────────────────

        private string ErrorJson(string message)
        {
            return SimpleJson.SerializeObject(new { error = message });
        }

        private string ReadRequestBody(HttpListenerRequest request)
        {
            using (var body = request.InputStream)
            using (var reader = new StreamReader(body, request.ContentEncoding))
            {
                return reader.ReadToEnd();
            }
        }

        private string GetDictValue(Dictionary<string, object> dict, string key)
        {
            if (dict == null) return null;
            object value;
            if (dict.TryGetValue(key, out value))
                return value?.ToString();
            return null;
        }

        /// <summary>
        /// Port the HTTP server listens on. Default 7890. Set in SetDefaults.
        /// </summary>
        public int Port
        {
            get { return port; }
            set { port = value; }
        }
    }
}
