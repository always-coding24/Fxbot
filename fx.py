import asyncio
import websockets
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

# ==============================================================================
#  SMC ANALYSIS ENGINE (UNCHANGED FROM OUR PREVIOUS VERSION)
# ==============================================================================
# This class contains the pure trading logic. It doesn't know about websockets;
# it just knows how to analyze lists of candles.

class SMCBot:
    def __init__(self):
        self.mitigated_h4_pois = set()
        self.mitigated_h1_pois = set()
        self.h4_data = []
        self.h1_data = []

    def analyze(self, h4_data: List[Dict], h1_data: List[Dict]) -> Dict:
        self.h4_data = h4_data
        self.h1_data = h1_data
        
        if len(self.h4_data) < 5 or len(self.h1_data) < 5:
            return self._format_no_trade("INVALID_STRUCTURE", "Insufficient historical data.")

        bias_analysis = self._get_4h_bias()
        if "error" in bias_analysis:
            return self._format_no_trade(bias_analysis["reason"], bias_analysis["error"])

        bias = bias_analysis["bias"]
        h4_poi = bias_analysis["poi"]
        
        if not self._is_mitigated(h4_poi, self.h4_data):
            return self._format_no_trade("WAITING_FOR_4H_POI_MITIGATION", f"Waiting for price to tap the 4H POI for {bias}")
        
        if 'time' in h4_poi and h4_poi['time'] not in self.mitigated_h4_pois:
             self.mitigated_h4_pois.add(h4_poi['time'])
             print(f"✅ 4H POI Mitigated: {h4_poi}")


        entry_analysis = self._get_1h_entry(bias)
        if "error" in entry_analysis:
            return self._format_no_trade(entry_analysis["reason"], entry_analysis["error"])
        
        h1_poi = entry_analysis["poi"]

        if not self._is_mitigated(h1_poi, self.h1_data):
             return self._format_no_trade("WAITING_FOR_1H_POI_MITIGATION", "Waiting for price to tap the 1H POI for entry.")
        
        if 'time' in h1_poi and h1_poi['time'] not in self.mitigated_h1_pois:
            self.mitigated_h1_pois.add(h1_poi['time'])
            print(f"✅ 1H POI Mitigated: {h1_poi}")


        return self._prepare_trade(bias, h1_poi)

    def _get_4h_bias(self) -> Dict:
        swings = self._get_swing_points(self.h4_data)
        if not swings['highs'] or not swings['lows']:
            return {"error": "Could not determine 4H market structure.", "reason": "INVALID_STRUCTURE"}

        last_candle = self.h4_data[-1]
        
        swept_low = self._find_liquidity_sweep(swings['lows'], last_candle, "low")
        if swept_low:
            mss_high = self._find_mss(swept_low, swings['highs'], "bullish")
            if mss_high and last_candle['close'] > mss_high['high']:
                poi = self._find_poi_after_mss(mss_high, self.h4_data, "bullish")
                if poi: return {"bias": "BUY", "poi": poi}
                return {"error": "No valid POI after 4H bullish MSS.", "reason": "NO_VALID_POI"}
        
        swept_high = self._find_liquidity_sweep(swings['highs'], last_candle, "high")
        if swept_high:
            mss_low = self._find_mss(swept_high, swings['lows'], "bearish")
            if mss_low and last_candle['close'] < mss_low['low']:
                poi = self._find_poi_after_mss(mss_low, self.h4_data, "bearish")
                if poi: return {"bias": "SELL", "poi": poi}
                return {"error": "No valid POI after 4H bearish MSS.", "reason": "NO_VALID_POI"}

        return {"error": "No clear liquidity sweep and MSS found.", "reason": "NO_SETUP"}

    def _get_1h_entry(self, bias: str) -> Dict:
        swings = self._get_swing_points(self.h1_data)
        if not swings['highs'] or not swings['lows']:
            return {"error": "Could not determine 1H market structure.", "reason": "INVALID_STRUCTURE"}
            
        last_candle = self.h1_data[-1]

        if bias == "BUY":
            swept_low = self._find_liquidity_sweep(swings['lows'], last_candle, "low", is_mini=True)
            if swept_low:
                mss_high = self._find_mss(swept_low, swings['highs'], "bullish")
                if mss_high and last_candle['close'] > mss_high['high']:
                    poi = self._find_poi_after_mss(mss_high, self.h1_data, "bullish", is_1h=True)
                    if poi: return {"poi": poi}
                    return {"error": "No valid 1H POI after bullish MSS.", "reason": "NO_VALID_POI"}

        elif bias == "SELL":
            swept_high = self._find_liquidity_sweep(swings['highs'], last_candle, "high", is_mini=True)
            if swept_high:
                mss_low = self._find_mss(swept_high, swings['lows'], "bearish")
                if mss_low and last_candle['close'] < mss_low['low']:
                    poi = self._find_poi_after_mss(mss_low, self.h1_data, "bearish", is_1h=True)
                    if poi: return {"poi": poi}
                    return {"error": "No valid 1H POI after bearish MSS.", "reason": "NO_VALID_POI"}

        return {"error": "No 1H entry setup found.", "reason": "NO_SETUP"}

    def _get_swing_points(self, data: List[Dict]) -> Dict:
        highs, lows = [], []
        if len(data) < 3: return {"highs": highs, "lows": lows}
        for i in range(1, len(data) - 1):
            if data[i]['high'] >= data[i-1]['high'] and data[i]['high'] > data[i+1]['high']: highs.append(data[i])
            if data[i]['low'] <= data[i-1]['low'] and data[i]['low'] < data[i+1]['low']: lows.append(data[i])
        return {"highs": highs, "lows": lows}

    def _find_liquidity_sweep(self, swings: List[Dict], current_candle: Dict, side: str, is_mini: bool = False) -> Optional[Dict]:
        if not swings: return None
        last_swing = swings[-1]
        if side == "low" and current_candle['low'] < last_swing['low']: return last_swing
        if side == "high" and current_candle['high'] > last_swing['high']: return last_swing
        return None

    def _find_mss(self, swept_point: Dict, opposite_swings: List[Dict], direction: str) -> Optional[Dict]:
        relevant_swings = [s for s in opposite_swings if s['time'] < swept_point['time']]
        return max(relevant_swings, key=lambda x: x['time']) if relevant_swings else None

    def _find_poi_after_mss(self, mss_point: Dict, data: List[Dict], direction: str, is_1h: bool = False) -> Optional[Dict]:
        search_range = [c for c in data if c['time'] > mss_point['time']]
        if not search_range: return None
        order_blocks = self._find_order_blocks(search_range, direction)
        mitigated_pois = self.mitigated_h1_pois if is_1h else self.mitigated_h4_pois
        valid_pois = [ob for ob in order_blocks if ob.get('time') not in mitigated_pois]
        return valid_pois[-1] if valid_pois else None

    def _find_order_blocks(self, data: List[Dict], direction: str) -> List[Dict]:
        order_blocks = []
        if len(data) < 2: return order_blocks
        for i in range(1, len(data)):
            prev_candle, curr_candle = data[i-1], data[i]
            is_strong_move = abs(curr_candle['close'] - curr_candle['open']) > (abs(prev_candle['close'] - prev_candle['open']) * 1.5)
            if direction == "bullish" and prev_candle['close'] < prev_candle['open'] and curr_candle['close'] > curr_candle['open'] and is_strong_move:
                order_blocks.append(prev_candle)
            if direction == "bearish" and prev_candle['close'] > prev_candle['open'] and curr_candle['close'] < curr_candle['open'] and is_strong_move:
                order_blocks.append(prev_candle)
        return order_blocks

    def _is_mitigated(self, poi: Dict, data: List[Dict]) -> bool:
        if not poi or 'time' not in poi: return False
        relevant_candles = [c for c in data if c.get('time', 0) > poi['time']]
        for candle in relevant_candles:
            if candle['low'] <= poi['high'] and candle['high'] >= poi['low']: return True
        return False

    def _prepare_trade(self, bias: str, poi: Dict) -> Dict:
        entry_price = poi['high'] if bias == "SELL" else poi['low']
        swings = self._get_swing_points(self.h1_data)
        if bias == "BUY":
            sl = poi['low'] * 0.999
            potential_tps = [s for s in swings['highs'] if s.get('time', 0) > poi.get('time', 0)]
            tp = potential_tps[0]['high'] if potential_tps else entry_price * 1.01
        else:
            sl = poi['high'] * 1.001
            potential_tps = [s for s in swings['lows'] if s.get('time', 0) > poi.get('time', 0)]
            tp = potential_tps[0]['low'] if potential_tps else entry_price * 0.99
        trade = {"action": "taketrade", "order_type": bias, "entry": entry_price, "sl": sl, "tp": tp}
        # Reset mitigated POIs after a trade is taken to look for new setups
        self.mitigated_h1_pois.clear()
        self.mitigated_h4_pois.clear()
        return trade

    def _format_no_trade(self, reason_code: str, log_message: str) -> Dict:
        return {"action": "don'ttaketrade", "reason": reason_code, "details": log_message}

# ==============================================================================
#  REAL-TIME TRADING BOT ENGINE
# ==============================================================================
# This class handles the live data stream, builds candles, and uses the
# SMCBot to run analysis.

class LiveTrader:
    def __init__(self, symbol):
        self.uri = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@trade"
        self.smc_bot = SMCBot()
        self.h1_candles = []
        self.h4_candles = []
        self.current_h1_candle = None
        self.current_h4_candle = None
        self.active_trade = None

    def _update_candle(self, candle: Optional[Dict], price: float, qty: float) -> Dict:
        """Helper function to update a candle with a new trade."""
        if not candle:
            return {'open': price, 'high': price, 'low': price, 'close': price, 'volume': qty, 'start_time': None}
        
        candle['high'] = max(candle['high'], price)
        candle['low'] = min(candle['low'], price)
        candle['close'] = price
        candle['volume'] += qty
        return candle
        
    async def _handle_message(self, ws):
        """Processes a single message from the websocket."""
        async for message in ws:
            trade = json.loads(message)
            price = float(trade['p'])
            qty = float(trade['q'])
            timestamp = int(trade['T']) // 1000  # Use seconds for timestamp
            
            # --- Track Active Trade and Profit ---
            if self.active_trade:
                entry = self.active_trade['entry']
                sl = self.active_trade['sl']
                tp = self.active_trade['tp']
                profit = 0
                if self.active_trade['order_type'] == 'BUY':
                    profit = ((price - entry) / entry) * 100
                    if price <= sl: 
                        print(f"\n❌ STOP LOSS HIT: P/L: {profit:.2f}%")
                        self.active_trade = None
                    elif price >= tp:
                        print(f"\n🎯 TAKE PROFIT HIT: P/L: {profit:.2f}%")
                        self.active_trade = None
                else: # SELL
                    profit = ((entry - price) / entry) * 100
                    if price >= sl:
                         print(f"\n❌ STOP LOSS HIT: P/L: {profit:.2f}%")
                         self.active_trade = None
                    elif price <= tp:
                        print(f"\n🎯 TAKE PROFIT HIT: P/L: {profit:.2f}%")
                        self.active_trade = None
                
                if self.active_trade: # Check if trade is still active before printing
                    pnl_str = f"🟢 P/L: {profit:+.2f}%" if profit >= 0 else f"🔴 P/L: {profit:+.2f}%"
                    print(f"\r🔔 IN TRADE [{self.active_trade['order_type']}]: Entry: {entry:.2f}, Current: {price:.2f}, SL: {sl:.2f}, TP: {tp:.2f} | {pnl_str}", end="")
            
            dt_object = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            current_h1_start_time = dt_object.replace(minute=0, second=0, microsecond=0)
            current_h4_start_time = dt_object.replace(hour=(dt_object.hour // 4) * 4, minute=0, second=0, microsecond=0)
            
            if self.current_h1_candle is None:
                self.current_h1_candle = self._update_candle(None, price, qty)
                self.current_h1_candle['start_time'] = current_h1_start_time
            
            if current_h1_start_time > self.current_h1_candle['start_time']:
                final_candle = {"time": int(self.current_h1_candle['start_time'].timestamp()), "open": self.current_h1_candle['open'], "high": self.current_h1_candle['high'], "low": self.current_h1_candle['low'], "close": self.current_h1_candle['close'], "volume": self.current_h1_candle['volume']}
                self.h1_candles.append(final_candle)
                print(f"\n🕯️ New 1H Candle Closed. Total 1H candles: {len(self.h1_candles)}. Analyzing...")
                
                self.current_h1_candle = self._update_candle(None, price, qty)
                self.current_h1_candle['start_time'] = current_h1_start_time
                
                if not self.active_trade:
                    analysis_result = self.smc_bot.analyze(self.h4_candles, self.h1_candles)
                    if analysis_result['action'] == 'taketrade':
                        self.active_trade = analysis_result
                        print("\n" + "!"*80)
                        print(f"🚨🚨🚨 TAKE TRADE SIGNAL 🚨🚨🚨")
                        print(json.dumps(self.active_trade, indent=4))
                        print("!"*80)
                    else:
                        print(f"📊 Analysis Result: {analysis_result['reason']} - {analysis_result['details']}")

            if self.current_h4_candle is None:
                self.current_h4_candle = self._update_candle(None, price, qty)
                self.current_h4_candle['start_time'] = current_h4_start_time

            if current_h4_start_time > self.current_h4_candle['start_time']:
                final_candle = {"time": int(self.current_h4_candle['start_time'].timestamp()), "open": self.current_h4_candle['open'], "high": self.current_h4_candle['high'], "low": self.current_h4_candle['low'], "close": self.current_h4_candle['close'], "volume": self.current_h4_candle['volume']}
                self.h4_candles.append(final_candle)
                print(f"\n🕯️ New 4H Candle Closed. Total 4H candles: {len(self.h4_candles)}.")
                self.current_h4_candle = self._update_candle(None, price, qty)
                self.current_h4_candle['start_time'] = current_h4_start_time

            self.current_h1_candle = self._update_candle(self.current_h1_candle, price, qty)
            self.current_h4_candle = self._update_candle(self.current_h4_candle, price, qty)

    async def listen(self):
        """Main loop to connect to WebSocket, process trades, and handle reconnections."""
        while True:
            try:
                # Add ping_interval to keep the connection alive
                async with websockets.connect(self.uri, ping_interval=20, ping_timeout=20) as ws:
                    print("✅ Connection successful. Waiting for live trade data...")
                    await self._handle_message(ws)
            except websockets.exceptions.ConnectionClosed:
                print("Connection closed. Reconnecting in 10 seconds...")
                await asyncio.sleep(10)
            except Exception as e:
                print(f"An error occurred: {e}. Reconnecting in 10 seconds...")
                await asyncio.sleep(10)


if __name__ == "__main__":
    trader = LiveTrader(symbol="btcusdt")
    try:
        asyncio.run(trader.listen())
    except KeyboardInterrupt:
        print("\n🔌 Disconnected by user.")

