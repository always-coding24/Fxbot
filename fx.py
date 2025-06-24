import requests
import json
from datetime import datetime, timezone
import time
import os
from typing import List, Dict, Optional
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# ==============================================================================
#  CONFIGURATION
# ==============================================================================
ACCESS_TOKEN = '672f8f548ea2c0259ce2e043a27ccdf7-accd7e47d49e5eb316003deadbf45c56'
ACCOUNT_ID   = '101-001-35653324-001'
ENVIRONMENT  = 'practice'

# --- UPDATED: Instruments set to Bitcoin and Gold ---
INSTRUMENTS  = 'BTC_USD,XAU_USD'

# --- Risk management remains at $5 per trade ---
RISK_PER_TRADE_USD = 5.0

# ==============================================================================
#  SMC ANALYSIS ENGINE (The core trading logic)
# ==============================================================================
class SMCBot:
    def __init__(self, instrument_name: str):
        self.instrument = instrument_name
        self.mitigated_h4_pois = set()
        self.mitigated_h1_pois = set()

    def analyze(self, h4_data: List[Dict], h1_data: List[Dict]) -> Dict:
        if len(h4_data) < 5 or len(h1_data) < 5:
            return self._format_no_trade("INVALID_STRUCTURE", "Waiting for more candle data.")

        bias_analysis = self._get_4h_bias(h4_data)
        if "error" in bias_analysis:
            return self._format_no_trade(bias_analysis["reason"], bias_analysis["error"])

        bias = bias_analysis["bias"]
        h4_poi = bias_analysis["poi"]
        
        if not self._is_mitigated(h4_poi, h4_data):
            return self._format_no_trade("WAITING_FOR_4H_POI_MITIGATION", f"Waiting for price to tap 4H POI for {bias}")
        
        if 'time' in h4_poi and h4_poi['time'] not in self.mitigated_h4_pois:
             self.mitigated_h4_pois.add(h4_poi['time'])
        
        entry_analysis = self._get_1h_entry(bias, h1_data)
        if "error" in entry_analysis:
            return self._format_no_trade(entry_analysis["reason"], entry_analysis["error"])
        
        h1_poi = entry_analysis["poi"]

        if not self._is_mitigated(h1_poi, h1_data):
             return self._format_no_trade("WAITING_FOR_1H_POI_MITIGATION", "Waiting for price to tap 1H POI")
        
        if 'time' in h1_poi and h1_poi['time'] not in self.mitigated_h1_pois:
            self.mitigated_h1_pois.add(h1_poi['time'])

        return self._prepare_trade(bias, h1_poi, h1_data)

    def _prepare_trade(self, bias: str, poi: Dict, h1_data: List[Dict]) -> Dict:
        entry = float(poi['high'] if bias == "SELL" else poi['low'])
        swings = self._get_swing_points(h1_data)
        
        # NOTE: Stop Loss and TP are now set based on the POI.
        # The SPREAD ADJUSTMENT will happen in the LiveTrader class where we have live bid/ask data.
        if bias == "BUY":
            sl = float(poi['low'])
            tps = [s for s in swings['highs'] if s.get('time', 0) > poi.get('time', 0)]
            tp = float(tps[0]['high']) if tps else entry * 1.005 # Default TP
        else: # SELL
            sl = float(poi['high'])
            tps = [s for s in swings['lows'] if s.get('time',0) > poi.get('time',0)]
            tp = float(tps[0]['low']) if tps else entry * 0.995 # Default TP

        trade = {
            "action": "taketrade", "order_type": bias, "entry": entry, 
            "sl": sl, "tp": tp, "units": 0 # Units will be calculated later
        }
        
        self.mitigated_h1_pois.clear(); self.mitigated_h4_pois.clear()
        return trade

    # Other SMCBot helper methods remain unchanged...
    def _get_4h_bias(self, h4_data: List[Dict]) -> Dict:
        swings = self._get_swing_points(h4_data)
        if not swings['highs'] or not swings['lows']: return {"error": "...", "reason": "INVALID_STRUCTURE"}
        last_candle = h4_data[-1]
        swept_low = self._find_liquidity_sweep(swings['lows'], last_candle, "low")
        if swept_low:
            mss_high = self._find_mss(swept_low, swings['highs'], "bullish")
            if mss_high and last_candle['close'] > mss_high['high']:
                poi = self._find_poi_after_mss(mss_high, h4_data, "bullish")
                if poi: return {"bias": "BUY", "poi": poi}
        swept_high = self._find_liquidity_sweep(swings['highs'], last_candle, "high")
        if swept_high:
            mss_low = self._find_mss(swept_high, swings['lows'], "bearish")
            if mss_low and last_candle['close'] < mss_low['low']:
                poi = self._find_poi_after_mss(mss_low, h4_data, "bearish")
                if poi: return {"bias": "SELL", "poi": poi}
        return {"error": "Waiting for 4H liquidity sweep & MSS.", "reason": "NO_SETUP"}

    def _get_1h_entry(self, bias: str, h1_data: List[Dict]) -> Dict:
        swings = self._get_swing_points(h1_data)
        if not swings['highs'] or not swings['lows']: return {"error": "...", "reason": "INVALID_STRUCTURE"}
        last_candle = h1_data[-1]
        if bias == "BUY":
            swept_low = self._find_liquidity_sweep(swings['lows'], last_candle, "low", is_mini=True)
            if swept_low:
                mss_high = self._find_mss(swept_low, swings['highs'], "bullish")
                if mss_high and last_candle['close'] > mss_high['high']:
                    poi = self._find_poi_after_mss(mss_high, h1_data, "bullish", is_1h=True)
                    if poi: return {"poi": poi}
        elif bias == "SELL":
            swept_high = self._find_liquidity_sweep(swings['highs'], last_candle, "high", is_mini=True)
            if swept_high:
                mss_low = self._find_mss(swept_high, swings['lows'], "bearish")
                if mss_low and last_candle['close'] < mss_low['low']:
                    poi = self._find_poi_after_mss(mss_low, h1_data, "bearish", is_1h=True)
                    if poi: return {"poi": poi}
        return {"error": "Waiting for 1H liquidity sweep & MSS.", "reason": "NO_SETUP"}
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
        order_blocks = [];
        if len(data) < 2: return order_blocks
        for i in range(1, len(data)):
            p, c = data[i-1], data[i]
            strong = abs(c['close'] - c['open']) > abs(p['close'] - p['open'])
            if direction=="bullish" and p['close']<p['open'] and c['close']>c['open'] and strong: order_blocks.append(p)
            if direction=="bearish" and p['close']>p['open'] and c['close']<c['open'] and strong: order_blocks.append(p)
        return order_blocks
    def _is_mitigated(self, poi: Dict, data: List[Dict]) -> bool:
        if not poi or 'time' not in poi: return False
        candles = [c for c in data if c.get('time', 0) > poi['time']]
        for c in candles:
            if c['low'] <= poi['high'] and c['high'] >= poi['low']: return True
        return False
    def _format_no_trade(self, reason: str, details: str) -> Dict:
        return {"action": "don'ttaketrade", "reason": reason, "details": details}

# ==============================================================================
#  DASHBOARD AND VISUALS
# ==============================================================================
class Dashboard:
    def __init__(self):
        self.spinner_chars = ['|', '/', '—', '\\']
        self.spinner_index = 0

    def get_spinner(self):
        char = self.spinner_chars[self.spinner_index]
        self.spinner_index = (self.spinner_index + 1) % len(self.spinner_chars)
        return char

    def render(self, state: Dict):
        os.system('cls' if os.name == 'nt' else 'clear')
        
        print(Style.BRIGHT + Fore.CYAN + "=== Israel devReal-Time Trading Bot cred:fumcy❤️

 ===")
        print(f"Status: {Fore.GREEN}{state['connection_status']}{Style.RESET_ALL} | Uptime: {state['uptime']} | Max Risk: ${RISK_PER_TRADE_USD}")
        print("-" * 60)

        print(Style.BRIGHT + Fore.YELLOW + "\n--- Market Watch ---")
        header = f"{'Instrument':<12} | {'Price':<12} | {'Spread (pips)':<15} | {'Candles (1H/4H)':<16} | {'SMC Analysis Status'}"
        print(header); print("-" * len(header))
        for inst, data in state['instruments'].items():
            price_str = f"{data['price']:.2f}"
            candle_str = f"{data['h1_candles_count']} / {data['h4_candles_count']}"
            spread_str = f"{data['spread']:.1f}"
            status_color = Fore.YELLOW if 'Waiting' in data['analysis_status'] else Fore.CYAN
            print(f"{Fore.WHITE}{inst:<12}{Style.RESET_ALL} | {data['spinner']} {price_str:<10} | {spread_str:<15} | {candle_str:<16} | {status_color}{data['analysis_status']}")

        active_trades_exist = any(data['active_trade'] for data in state['instruments'].values())
        if active_trades_exist:
            print(Style.BRIGHT + Fore.GREEN + "\n--- Active Trades ---")
            trade_header = f"{'Instrument':<12} | {'Type':<5} | {'Lots':<10} | {'P/L (USD)':<15}"
            print(trade_header); print("-" * len(trade_header))
            for inst, data in state['instruments'].items():
                if data['active_trade']:
                    trade = data['active_trade']
                    pnl_usd = trade['live_pnl_usd']
                    pnl_color = Fore.GREEN if pnl_usd >= 0 else Fore.RED
                    lots_str = f"{trade.get('units', 0) / 100000.0:.2f}"
                    print(f"{Fore.WHITE}{inst:<12}{Style.RESET_ALL} | {trade['order_type']:<5} | {lots_str:<10} | {pnl_color}${pnl_usd:+.2f}")
        
        print(Style.BRIGHT + Fore.WHITE + "\n--- Event Log ---")
        if not state['logs']: print(f"{Style.DIM}No new events.")
        for log in state['logs'][-5:]: print(f"{Style.DIM}{log}")

# ==============================================================================
#  LIVE OANDA TRADING BOT ENGINE
# ==============================================================================
class LiveOandaTrader:
    def __init__(self, instruments: str):
        self.domain = 'stream-fxpractice.oanda.com' if ENVIRONMENT == 'practice' else 'stream-fxtrade.oanda.com'
        self.url = f'https://{self.domain}/v3/accounts/{ACCOUNT_ID}/pricing/stream'
        self.headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
        self.params = {'instruments': instruments}
        self.instrument_list = instruments.split(',')
        self.dashboard = Dashboard()
        self.start_time = time.time()
        self.logs = []
        self.state = {
            'connection_status': 'Initializing...', 'uptime': '0s',
            'instruments': {inst: {
                'price': 0.0, 'bid': 0.0, 'ask': 0.0, 'spread': 0.0,
                'analysis_status': 'Connecting...', 'h1_candles_count': 0, 'h4_candles_count': 0,
                'spinner': ' ', 'active_trade': None
            } for inst in self.instrument_list},
            'logs': self.logs
        }
        self.smc_bots = {inst: SMCBot(inst) for inst in self.instrument_list}
        self.h1_candles = {inst: [] for inst in self.instrument_list}
        self.h4_candles = {inst: [] for inst in self.instrument_list}
        self.current_h1_candle = {inst: None for inst in self.instrument_list}
        self.current_h4_candle = {inst: None for inst in self.instrument_list}

    def _add_log(self, message: str):
        self.logs.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {message}")

    def _update_candle(self, candle: Optional[Dict], price: float) -> Dict:
        if not candle: return {'open': price, 'high': price, 'low': price, 'close': price, 'start_time': None}
        candle['high'] = max(candle['high'], price)
        candle['low'] = min(candle['low'], price)
        candle['close'] = price
        return candle

    def _calculate_units(self, inst: str, stop_loss_pips: float) -> int:
        """Calculates position size to risk a fixed USD amount."""
        if stop_loss_pips <= 0: return 0
        
        # For XAU_USD and BTC_USD, the value of 1 pip for 1 unit is simply 0.01 (since they are quoted in USD)
        # For other pairs, more complex conversion logic would be needed.
        pip_value_for_one_unit = 0.01 

        risk_in_usd_per_unit = stop_loss_pips * pip_value_for_one_unit
        
        if risk_in_usd_per_unit <= 0: return 0
        
        return int(RISK_PER_TRADE_USD / risk_in_usd_per_unit)

    def _handle_tick(self, tick: Dict):
        try:
            if tick.get('type') != 'PRICE': return
            inst = tick['instrument']
            if inst not in self.instrument_list: return
            
            bid = float(tick['bids'][0]['price'])
            ask = float(tick['asks'][0]['price'])
            mid_price = (bid + ask) / 2
            
            # Pip calculation for BTC/XAU is based on price, not fixed value
            pips_multiplier = 1 if 'BTC' in inst or 'XAU' in inst else 10000 
            spread = (ask - bid) * pips_multiplier

            # Update dashboard state
            self.state['instruments'][inst]['price'] = mid_price
            self.state['instruments'][inst]['bid'] = bid
            self.state['instruments'][inst]['ask'] = ask
            self.state['instruments'][inst]['spread'] = spread
            self.state['instruments'][inst]['spinner'] = self.dashboard.get_spinner()
            
            if self.state['instruments'][inst]['active_trade']: self._track_active_trade(inst)
            self._aggregate_candles(inst, mid_price, datetime.fromisoformat(tick['time'].replace('Z', '+00:00')))
        except (KeyError, IndexError): pass

    def _track_active_trade(self, inst: str):
        trade = self.state['instruments'][inst]['active_trade']
        bid = self.state['instruments'][inst]['bid']
        ask = self.state['instruments'][inst]['ask']
        
        pnl_usd = 0.0
        if trade['order_type'] == 'BUY':
            # If we bought, we close the trade by selling at the current 'bid' price.
            price_change = bid - trade['entry_price_with_spread']
            pnl_usd = price_change * trade['units']
            if bid <= trade['sl']: self._close_trade(inst, bid, "STOP LOSS")
            elif bid >= trade['tp']: self._close_trade(inst, bid, "TAKE PROFIT")
        else: # SELL
            # If we sold, we close the trade by buying at the current 'ask' price.
            price_change = trade['entry_price_with_spread'] - ask
            pnl_usd = price_change * trade['units']
            if ask >= trade['sl']: self._close_trade(inst, ask, "STOP LOSS")
            elif ask <= trade['tp']: self._close_trade(inst, ask, "TAKE PROFIT")
        
        if self.state['instruments'][inst]['active_trade']:
            self.state['instruments'][inst]['active_trade']['live_pnl_usd'] = pnl_usd

    def _close_trade(self, inst: str, price: float, reason: str):
         self._add_log(f"🎯 [{inst}] {reason} HIT AT {price:.2f}")
         self.state['instruments'][inst]['active_trade'] = None

    def _aggregate_candles(self, inst: str, price: float, timestamp: datetime):
        h1_start = timestamp.replace(minute=0, second=0, microsecond=0)
        
        if self.current_h1_candle[inst] is None: self.current_h1_candle[inst] = self._update_candle(None, price); self.current_h1_candle[inst]['start_time'] = h1_start
        if h1_start > self.current_h1_candle[inst]['start_time']:
            c = self.current_h1_candle[inst]
            self.h1_candles[inst].append({"time": int(c['start_time'].timestamp()), "open":c['open'],"high":c['high'],"low":c['low'],"close":c['close'],"volume":0})
            self.state['instruments'][inst]['h1_candles_count'] = len(self.h1_candles[inst])
            self._add_log(f"🕯️ [{inst}] New 1H Candle. Total: {len(self.h1_candles[inst])}")
            self.current_h1_candle[inst] = self._update_candle(None, price); self.current_h1_candle[inst]['start_time'] = h1_start
            
            if not self.state['instruments'][inst]['active_trade']:
                bot = self.smc_bots[inst]
                res = bot.analyze(self.h4_candles[inst], self.h1_candles[inst])
                self.state['instruments'][inst]['analysis_status'] = res['details']
                if res['action'] == 'taketrade':
                    # --- SPREAD-AWARE TRADE EXECUTION ---
                    bid = self.state['instruments'][inst]['bid']
                    ask = self.state['instruments'][inst]['ask']
                    
                    if res['order_type'] == 'BUY':
                        res['entry_price_with_spread'] = ask # We buy at the ask price
                        stop_pips = ask - res['sl']
                        res['units'] = self._calculate_units(inst, stop_pips)
                    else: # SELL
                        res['entry_price_with_spread'] = bid # We sell at the bid price
                        stop_pips = res['sl'] - bid
                        res['units'] = self._calculate_units(inst, stop_pips)

                    if res['units'] > 0:
                        res['live_pnl_usd'] = 0.0
                        self.state['instruments'][inst]['active_trade'] = res
                        lots = res.get('units', 0) / 100000.0
                        self._add_log(f"🚨 [{inst}] TAKE TRADE: {res['order_type']} {lots:.2f} lots @ {res['entry_price_with_spread']:.2f}")
        
        h4_start = timestamp.replace(hour=(timestamp.hour//4)*4, minute=0, second=0, microsecond=0)
        if self.current_h4_candle[inst] is None: self.current_h4_candle[inst] = self._update_candle(None, price); self.current_h4_candle[inst]['start_time'] = h4_start
        if h4_start > self.current_h4_candle[inst]['start_time']:
            c = self.current_h4_candle[inst]
            self.h4_candles[inst].append({"time": int(c['start_time'].timestamp()), "open":c['open'],"high":c['high'],"low":c['low'],"close":c['close'],"volume":0})
            self.state['instruments'][inst]['h4_candles_count'] = len(self.h4_candles[inst])
            self._add_log(f"🕯️ [{inst}] New 4H Candle. Total: {len(self.h4_candles[inst])}")
            self.current_h4_candle[inst] = self._update_candle(None, price); self.current_h4_candle[inst]['start_time'] = h4_start
        
        self.current_h1_candle[inst] = self._update_candle(self.current_h1_candle[inst], price)
        self.current_h4_candle[inst] = self._update_candle(self.current_h4_candle[inst], price)

    def stream(self):
        last_render = 0
        while True:
            try:
                self.state['connection_status'] = 'Connecting...'
                self.dashboard.render(self.state)
                response = requests.get(self.url, headers=self.headers, params=self.params, stream=True, timeout=30)
                if response.status_code != 200:
                    self.state['connection_status'] = f'Error {response.status_code}'; self._add_log(f"Connection Error: {response.text}")
                    self.dashboard.render(self.state); time.sleep(15); continue
                self.state['connection_status'] = 'Connected'; self._add_log("Connection successful.")
                for line in response.iter_lines():
                    if line:
                        try: self._handle_tick(json.loads(line.decode('utf-8')))
                        except json.JSONDecodeError: continue
                    uptime_seconds = int(time.time() - self.start_time)
                    self.state['uptime'] = f"{uptime_seconds//3600}h {(uptime_seconds%3600)//60}m {uptime_seconds%60}s"
                    if time.time() - last_render > 0.5: self.dashboard.render(self.state); last_render = time.time()
            except requests.exceptions.RequestException as e:
                self.state['connection_status'] = 'Connection Lost'; self._add_log(f"Connection Error: {e}")
                self.dashboard.render(self.state); time.sleep(10)

if __name__ == "__main__":
    trader = LiveOandaTrader(instruments=INSTRUMENTS)
    try:
        trader.stream()
    except KeyboardInterrupt:
        print(Style.BRIGHT + Fore.YELLOW + "\n\n🔌 Disconnected by user. Goodbye, Israel!")


