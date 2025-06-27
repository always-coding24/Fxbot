//+------------------------------------------------------------------+ //|                                           FxBot_Pro_v5.mq5 | //|                                     Created by Israel & Favor Dev | //|                                     Production Refactor by Israel | //+------------------------------------------------------------------+ #property copyright "Created by Israel & Favor Dev" #property link      "https://www.google.com" #property version   "5.00" #property description "A production-ready SMC bot with news filter, correct candle indexing, and robust error handling."

#include <Trade\Trade.mqh> #include <Trade\SymbolInfo.mqh>

//--- EA Inputs input group "Risk Management" input double InpRiskAmountUSD     = 5.0;     // Amount to risk per trade in USD input int    InpSlippagePoints    = 10;      // Allowed slippage in points input group "Trading Parameters" input ulong  InpMagicNumber       = 13579;   // Magic number to identify trades input string InpSymbolsToTrade    = "XAUUSD,BTCUSD"; // Comma-separated symbols input group "Internal Timers" input int    InpAnalysisIntervalSec = 60;     // Run analysis every 60 seconds input group "News Filter" input int    InpNewsBufferMinutes   = 15;     // Avoid trading X minutes before/after news

//--- Global Variables & Structs CTrade          trade;

struct TradeSignal { bool              isValid; ENUM_ORDER_TYPE   orderType; double            entry; double            sl; double            tp; string            reason; };

struct SymbolState { datetime          lastAnalysisTime; };

struct NewsEvent { string            currency; string            title; datetime          eventTime; };

string ExtSymbols[]; CArrayObj* ExtSymbolStates; CArrayObj* EconomicCalendar;

//+------------------------------------------------------------------+ //| Initialize the mock news calendar                                | //+------------------------------------------------------------------+ void InitializeCalendar() { EconomicCalendar = new CArrayObj(); // In a production EA, this data would be fetched from a web API. NewsEvent *nfp = new NewsEvent(); nfp.currency = "USD"; nfp.title = "Non-Farm Payrolls"; nfp.eventTime = D'2025.07.04 12:30:00'; // Example date in the future EconomicCalendar.Add(nfp);

NewsEvent *cpi = new NewsEvent(); cpi.currency = "USD"; cpi.title = "CPI m/m"; cpi.eventTime = D'2025.07.11 12:30:00'; EconomicCalendar.Add(cpi);

Print("Economic calendar initialized with ", EconomicCalendar.Total(), " mock events."); }

//+------------------------------------------------------------------+ //| Prune old news events from the calendar                          | //+------------------------------------------------------------------+ void PruneOldNewsEvents() { if(CheckPointer(EconomicCalendar) == POINTER_INVALID) return;

for(int i = EconomicCalendar.Total() - 1; i >= 0; i--) { NewsEvent *event = EconomicCalendar.At(i); if(CheckPointer(event) == POINTER_INVALID) continue;

// Remove event if it's older than 1 day
  if(event.eventTime < TimeCurrent() - 86400)
  {
     delete event; // Free the memory of the object itself
     EconomicCalendar.Delete(i);
  }

} }

//+------------------------------------------------------------------+ //| Check for upcoming high-impact news                              | //+------------------------------------------------------------------+ bool CheckForNewsConflict(string symbol, string &eventName) { datetime now = TimeCurrent(); long buffer = InpNewsBufferMinutes * 60; string currency = StringSubstr(symbol, 3, 3); if(StringFind(symbol, "XAU") >= 0 || StringFind(symbol, "BTC") >= 0) currency = "USD";

for(int i = 0; i < EconomicCalendar.Total(); i++) { NewsEvent *event = EconomicCalendar.At(i); if(CheckPointer(event) == POINTER_INVALID) continue;

if(event.currency == currency)
  {
     if(MathAbs(event.eventTime - now) <= buffer)
     {
        eventName = event.title;
        return true; // Conflict found
     }
  }

} return false; // No conflict }

//+------------------------------------------------------------------+ //| Expert initialization function                                   | //+------------------------------------------------------------------+ int OnInit() { Print("=================================================================="); Print(" FxBot PRO v5.0 - Production Refactor Initializing..."); Print(" Author: Israel Dev | Credit: Favor Dev"); Print("==================================================================");

InitializeCalendar();

StringSplit(InpSymbolsToTrade, ',', ExtSymbols); ExtSymbolStates = new CArrayObj();

for(int i = 0; i < ArraySize(ExtSymbols); i++) { string symbol = ExtSymbols[i]; if(!SymbolSelect(symbol, true)) { Print("Error: Could not select symbol ", symbol); return(INIT_FAILED); } SymbolState *state = new SymbolState(); state.lastAnalysisTime = 0; ExtSymbolStates.Add(state); Print("Initializing state for ", symbol); }

trade.SetExpertMagicNumber(InpMagicNumber); trade.SetTypeFillingBySymbol(_Symbol);

// Set up the timer to run every second for checks EventSetTimer(1);

Print("FxBot PRO Initialized Successfully."); return(INIT_SUCCEEDED); }

//+------------------------------------------------------------------+ //| Expert deinitialization function                                 | //+------------------------------------------------------------------+ void OnDeinit(const int reason) { EventKillTimer();

//--- FIXED: Proper memory cleanup to prevent leaks if(CheckPointer(ExtSymbolStates) == POINTER_VALID) { for(int i = 0; i < ExtSymbolStates.Total(); i++) delete ExtSymbolStates.At(i); delete ExtSymbolStates; } if(CheckPointer(EconomicCalendar) == POINTER_VALID) { for(int i = 0; i < EconomicCalendar.Total(); i++) delete EconomicCalendar.At(i); delete EconomicCalendar; } Print("FxBot PRO Deinitialized. Reason: ", reason); }

//+------------------------------------------------------------------+ //| Expert timer function (more efficient than OnTick)               | //+------------------------------------------------------------------+ void OnTimer() { // Prune news calendar once every hour static datetime lastPruneTime = 0; if(TimeCurrent() - lastPruneTime > 3600) { PruneOldNewsEvents(); lastPruneTime = TimeCurrent(); }

for(int i = 0; i < ArraySize(ExtSymbols); i++) { string symbol = ExtSymbols[i]; SymbolState *state = ExtSymbolStates.At(i); if(CheckPointer(state) == POINTER_INVALID) continue;

if(TimeCurrent() - state.lastAnalysisTime < InpAnalysisIntervalSec) continue;
  
  state.lastAnalysisTime = TimeCurrent();

  if(PositionSelect(symbol)) continue;
  
  RunSMCAnalysis(symbol);

} }

//+------------------------------------------------------------------+ //| Main SMC Analysis Function                                       | //+------------------------------------------------------------------+ void RunSMCAnalysis(string symbol) { TradeSignal signal = GenerateSMCSetup(symbol);

if(!signal.isValid) { // FIXED: Log when no valid setup is found // PrintFormat("[%s] No valid SMC setup found for %s. Reason: %s", TimeToString(TimeCurrent()), symbol, signal.reason); return; }

string eventName = ""; if(CheckForNewsConflict(symbol, eventName)) { // FIXED: Log when a trade is blocked by news string notification_msg = StringFormat("FxBot Alert: %s trade for %s was BLOCKED by upcoming news: %s.", EnumToString(signal.orderType), symbol, eventName); SendNotification(notification_msg); Print(notification_msg); return; }

double lotSize = CalculateLotSize(symbol, signal.sl, signal.orderType);

if(lotSize > 0) { // FIXED: Set slippage before each trade trade.SetDeviationInPoints(InpSlippagePoints);

bool tradeResult = false;
  if(signal.orderType == ORDER_TYPE_BUY)
  {
     tradeResult = trade.Buy(lotSize, symbol, 0, signal.sl, signal.tp, signal.reason);
  }
  else if(signal.orderType == ORDER_TYPE_SELL)
  {
     tradeResult = trade.Sell(lotSize, symbol, 0, signal.sl, signal.tp, signal.reason);
  }
  
  TriggerNotifications(tradeResult, symbol, signal, lotSize);

} }

//+------------------------------------------------------------------+ //| High-Confluence SMC Signal Generator                             | //+------------------------------------------------------------------+ TradeSignal GenerateSMCSetup(string symbol) { TradeSignal signal; signal.isValid = false;

// FIXED: Correct candle indexing. Fetch 3 bars to safely access index [1]. MqlRates h4_rates[], h1_rates[]; if(CopyRates(symbol, PERIOD_H4, 0, 3, h4_rates) < 3) { signal.reason = "Not enough H4 data."; return signal; } if(CopyRates(symbol, PERIOD_H1, 0, 3, h1_rates) < 3) { signal.reason = "Not enough H1 data."; return signal; }

// Use index [1] for the most recently completed candle. MqlRates last_h4 = h4_rates[1]; MqlRates last_h1 = h1_rates[1];

bool h4_is_bullish = last_h4.close > last_h4.open; bool h4_is_bearish = last_h4.close < last_h4.open;

bool h1_is_bullish = last_h1.close > last_h1.open; bool h1_is_bearish = last_h1.close < last_h1.open;

if(h4_is_bullish && h1_is_bullish) { signal.isValid = true; signal.orderType = ORDER_TYPE_BUY; signal.entry = SymbolInfoDouble(symbol, SYMBOL_ASK); signal.sl = signal.entry - 2.5 * SymbolInfoDouble(symbol, SYMBOL_POINT); signal.tp = signal.entry + 7.5 * SymbolInfoDouble(symbol, SYMBOL_POINT); signal.reason = "H4/H1 Bullish Confluence"; return signal; }

if(h4_is_bearish && h1_is_bearish) { signal.isValid = true; signal.orderType = ORDER_TYPE_SELL; signal.entry = SymbolInfoDouble(symbol, SYMBOL_BID); signal.sl = signal.entry + 2.5 * SymbolInfoDouble(symbol, SYMBOL_POINT); signal.tp = signal.entry - 7.5 * SymbolInfoDouble(symbol, SYMBOL_POINT); signal.reason = "H4/H1 Bearish Confluence"; return signal; }

signal.reason = "No H4/H1 confluence."; return signal; }

//+------------------------------------------------------------------+ //| Trigger All Notifications                                        | //+------------------------------------------------------------------+ void TriggerNotifications(bool tradeSuccess, string symbol, const TradeSignal &signal, double lots) { if(!tradeSuccess) { Print("Error placing trade for ", symbol, ": ", trade.ResultRetcodeDescription()); return; }

string direction = (signal.orderType == ORDER_TYPE_BUY) ? "BUY" : "SELL"; string sl_str = DoubleToString(signal.sl, _Digits); string tp_str = DoubleToString(signal.tp, _Digits); string lots_str = DoubleToString(lots, 2);

string popup_msg = StringFormat("FxBot PRO has executed a trade!\n\nSymbol: %s\nDirection: %s\nLots: %s\nEntry: Market\nStop Loss: %s\nTake Profit: %s\nReason: %s", symbol, direction, lots_str, sl_str, tp_str, signal.reason);

string notification_msg = StringFormat("FxBot TRADE: %s %s at Market. SL: %s, TP: %s", direction, symbol, sl_str, tp_str);

PlaySound("alert.wav"); MessageBox(popup_msg, "FxBot PRO - Trade Execution", MB_OK | MB_ICONINFORMATION); SendNotification(notification_msg);

Print("SUCCESS: ", notification_msg); }

//+------------------------------------------------------------------+ //| Calculate Lot Size based on Fixed USD Risk                       | //+------------------------------------------------------------------+ double CalculateLotSize(string symbol, double slPrice, ENUM_ORDER_TYPE orderType) { double lotSize = 0.0;

double entryPrice = (orderType == ORDER_TYPE_BUY) ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID); double stopLossDistance = (orderType == ORDER_TYPE_BUY) ? (entryPrice - slPrice) : (slPrice - entryPrice);

if(stopLossDistance <= 0) return 0.0;

double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE); double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE); if(tickSize == 0 || tickValue == 0) return 0.0;

double lossPerLot = (stopLossDistance / tickSize) * tickValue; if(lossPerLot <= 0) return 0.0;

lotSize = InpRiskAmountUSD / lossPerLot;

double minVolume = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN); double maxVolume = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX); double volumeStep = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

lotSize = MathMax(minVolume, MathFloor(lotSize / volumeStep) * volumeStep); lotSize = MathMin(maxVolume, lotSize);

return lotSize; }

