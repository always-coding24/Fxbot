import json
import sys
from typing import List, Dict, Optional, Tuple

class SMCBot:
    """
    A trading bot that implements a Smart Money Concepts (SMC) strategy.
    It uses 4-hour data for directional bias and 1-hour data for trade entries.
    """

    def __init__(self, h4_data: List[Dict], h1_data: List[Dict]):
        """
        Initializes the bot with candlestick data.
        
        Args:
            h4_data: A list of 4-hour candlestick objects.
            h1_data: A list of 1-hour candlestick objects.
        """
        self.h4_data = sorted(h4_data, key=lambda x: x['time'])
        self.h1_data = sorted(h1_data, key=lambda x: x['time'])
        self.mitigated_h4_pois = set()
        self.mitigated_h1_pois = set()

    def analyze(self) -> Dict:
        """
        Main analysis function to determine trade action.
        
        Returns:
            A dictionary with the trade action or reason for not trading.
        """
        if len(self.h4_data) < 5 or len(self.h1_data) < 5:
            return self._format_no_trade("INVALID_STRUCTURE", "Insufficient data.")

        # 1. Determine 4H Directional Bias
        bias_analysis = self._get_4h_bias()
        if "error" in bias_analysis:
            return self._format_no_trade(bias_analysis["reason"], bias_analysis["error"])

        bias = bias_analysis["bias"]
        h4_poi = bias_analysis["poi"]
        
        # 2. Wait for 4H POI Mitigation
        if not self._is_mitigated(h4_poi, self.h4_data):
            return self._format_no_trade("WAITING_FOR_4H_POI_MITIGATION", "Waiting for price to tap the 4H POI.")
        
        self.mitigated_h4_pois.add(h4_poi['time'])

        # 3. Analyze 1H for Entry
        entry_analysis = self._get_1h_entry(bias)
        if "error" in entry_analysis:
            return self._format_no_trade(entry_analysis["reason"], entry_analysis["error"])
        
        h1_poi = entry_analysis["poi"]

        # 4. Wait for 1H POI Mitigation
        if not self._is_mitigated(h1_poi, self.h1_data):
             return self._format_no_trade("WAITING_FOR_1H_POI_MITIGATION", "Waiting for price to tap the 1H POI for entry.")
        
        self.mitigated_h1_pois.add(h1_poi['time'])

        # 5. Execute Trade
        return self._prepare_trade(bias, h1_poi)

    def _get_4h_bias(self) -> Dict:
        """Analyzes the 4H chart to find directional bias and a POI."""
        swings = self._get_swing_points(self.h4_data)
        if not swings['highs'] or not swings['lows']:
            return {"error": "Could not determine market structure.", "reason": "INVALID_STRUCTURE"}

        last_candle = self.h4_data[-1]
        
        # Check for Bullish Bias
        # 1. Liquidity sweep of a significant low
        swept_low = self._find_liquidity_sweep(swings['lows'], last_candle, "low")
        if swept_low:
            # 2. Market Structure Shift (MSS)
            mss_high = self._find_mss(swept_low, swings['highs'], "bullish")
            if mss_high and last_candle['close'] > mss_high['high']:
                # 3. Find Inducement (FVG or Equal Lows) and POI
                poi = self._find_poi_after_mss(mss_high, self.h4_data, "bullish")
                if poi:
                    return {"bias": "BUY", "poi": poi}
                else:
                    return {"error": "No valid POI found after 4H bullish MSS.", "reason": "NO_VALID_POI"}
            else:
                 return {"error": "Waiting for bullish 4H MSS confirmation.", "reason": "WAITING_FOR_4H_MSS"}

        # Check for Bearish Bias
        # 1. Liquidity sweep of a significant high
        swept_high = self._find_liquidity_sweep(swings['highs'], last_candle, "high")
        if swept_high:
            # 2. Market Structure Shift (MSS)
            mss_low = self._find_mss(swept_high, swings['lows'], "bearish")
            if mss_low and last_candle['close'] < mss_low['low']:
                # 3. Find Inducement (FVG or Equal Highs) and POI
                poi = self._find_poi_after_mss(mss_low, self.h4_data, "bearish")
                if poi:
                    return {"bias": "SELL", "poi": poi}
                else:
                    return {"error": "No valid POI found after 4H bearish MSS.", "reason": "NO_VALID_POI"}
            else:
                 return {"error": "Waiting for bearish 4H MSS confirmation.", "reason": "WAITING_FOR_4H_MSS"}

        return {"error": "No clear liquidity sweep and MSS found.", "reason": "NO_SETUP"}

    def _get_1h_entry(self, bias: str) -> Dict:
        """Analyzes the 1H chart to find an entry point."""
        swings = self._get_swing_points(self.h1_data)
        if not swings['highs'] or not swings['lows']:
            return {"error": "Could not determine 1H market structure.", "reason": "INVALID_STRUCTURE"}
            
        last_candle = self.h1_data[-1]

        if bias == "BUY":
            # 1. Mini liquidity sweep
            swept_low = self._find_liquidity_sweep(swings['lows'], last_candle, "low", is_mini=True)
            if swept_low:
                # 2. 1H MSS
                mss_high = self._find_mss(swept_low, swings['highs'], "bullish")
                if mss_high and last_candle['close'] > mss_high['high']:
                    # 3. Find POI
                    poi = self._find_poi_after_mss(mss_high, self.h1_data, "bullish", is_1h=True)
                    if poi:
                         return {"poi": poi}
                    else:
                        return {"error": "No valid 1H POI found after bullish MSS.", "reason": "NO_VALID_POI"}
                else:
                    return {"error": "Waiting for bullish 1H MSS confirmation.", "reason": "WAITING_FOR_1H_MSS"}

        elif bias == "SELL":
            # 1. Mini liquidity sweep
            swept_high = self._find_liquidity_sweep(swings['highs'], last_candle, "high", is_mini=True)
            if swept_high:
                # 2. 1H MSS
                mss_low = self._find_mss(swept_high, swings['lows'], "bearish")
                if mss_low and last_candle['close'] < mss_low['low']:
                    # 3. Find POI
                    poi = self._find_poi_after_mss(mss_low, self.h1_data, "bearish", is_1h=True)
                    if poi:
                        return {"poi": poi}
                    else:
                         return {"error": "No valid 1H POI found after bearish MSS.", "reason": "NO_VALID_POI"}
                else:
                    return {"error": "Waiting for bearish 1H MSS confirmation.", "reason": "WAITING_FOR_1H_MSS"}

        return {"error": "No 1H entry setup found.", "reason": "NO_SETUP"}

    def _get_swing_points(self, data: List[Dict]) -> Dict:
        """Identifies swing highs and lows from candlestick data."""
        highs, lows = [], []
        # Need at least 3 candles to form a swing point
        for i in range(1, len(data) - 1):
            # Swing High
            if data[i]['high'] > data[i-1]['high'] and data[i]['high'] > data[i+1]['high']:
                highs.append(data[i])
            # Swing Low
            if data[i]['low'] < data[i-1]['low'] and data[i]['low'] < data[i+1]['low']:
                lows.append(data[i])
        return {"highs": highs, "lows": lows}

    def _find_liquidity_sweep(self, swings: List[Dict], current_candle: Dict, side: str, is_mini: bool = False) -> Optional[Dict]:
        """Finds the most recent liquidity sweep."""
        # For simplicity, we check if the current candle's low/high has taken out the most recent swing point.
        # A more complex implementation would check for significant sweeps.
        if not swings:
            return None
        
        last_swing = swings[-1]
        if side == "low" and current_candle['low'] < last_swing['low']:
            return last_swing
        if side == "high" and current_candle['high'] > last_swing['high']:
            return last_swing
        return None

    def _find_mss(self, swept_point: Dict, opposite_swings: List[Dict], direction: str) -> Optional[Dict]:
        """Finds the relevant market structure shift point after a sweep."""
        if direction == "bullish": # Swept a low, looking for break of a high
            # Find the swing high that formed before the swept low was created
            relevant_swings = [s for s in opposite_swings if s['time'] < swept_point['time']]
            return max(relevant_swings, key=lambda x: x['time']) if relevant_swings else None
        else: # Swept a high, looking for break of a low
            # Find the swing low that formed before the swept high was created
            relevant_swings = [s for s in opposite_swings if s['time'] < swept_point['time']]
            return max(relevant_swings, key=lambda x: x['time']) if relevant_swings else None

    def _find_poi_after_mss(self, mss_point: Dict, data: List[Dict], direction: str, is_1h: bool = False) -> Optional[Dict]:
        """Finds the closest Order Block or Breaker Block after an MSS."""
        # This is a simplified search. A real implementation would be more nuanced.
        # We look for inducement patterns like FVGs first.
        
        search_range = [c for c in data if c['time'] > mss_point['time']]

        # Find FVGs as inducement
        fvgs = self._find_fvgs(search_range, direction)
        
        # Find Order Blocks near inducement
        order_blocks = self._find_order_blocks(search_range, direction)

        # Select the best POI (closest to current price, below/above inducement)
        # For now, we take the most recent valid OB.
        valid_pois = [ob for ob in order_blocks if (not is_1h and ob['time'] not in self.mitigated_h4_pois) or (is_1h and ob['time'] not in self.mitigated_h1_pois)]
        
        if not valid_pois:
            return None

        # A simple check: POI must not be touched by inducement creating candles
        # If there are FVGs, the POI should ideally be beyond the FVG from the MSS point.
        if fvgs:
            last_fvg = fvgs[-1]
            if direction == "bullish": # POI should be below FVG
                potential_pois = [p for p in valid_pois if p['high'] < last_fvg['low']]
            else: # POI should be above FVG
                potential_pois = [p for p in valid_pois if p['low'] > last_fvg['high']]
            
            if potential_pois:
                return min(potential_pois, key=lambda x: x['low']) if direction == "bullish" else max(potential_pois, key=lambda x: x['high'])

        # If no FVG, take the most recent OB as a simplified POI
        return valid_pois[-1] if valid_pois else None
        
    def _find_fvgs(self, data: List[Dict], direction: str) -> List[Dict]:
        """Identifies Fair Value Gaps (FVGs)."""
        fvgs = []
        for i in range(len(data) - 2):
            c1, c2, c3 = data[i], data[i+1], data[i+2]
            if direction == "bullish" and c1['high'] < c3['low']:
                fvgs.append({"low": c1['high'], "high": c3['low'], "time": c2['time']})
            elif direction == "bearish" and c1['low'] > c3['high']:
                fvgs.append({"low": c3['high'], "high": c1['low'], "time": c2['time']})
        return fvgs

    def _find_order_blocks(self, data: List[Dict], direction: str) -> List[Dict]:
        """Identifies Order Blocks."""
        order_blocks = []
        # Simplified: Look for a strong move after an opposite candle
        for i in range(1, len(data)):
            prev_candle = data[i-1]
            curr_candle = data[i]

            is_strong_move = abs(curr_candle['close'] - curr_candle['open']) > (abs(prev_candle['close'] - prev_candle['open']) * 1.5)

            if direction == "bullish":
                # Last down candle before strong up move
                if prev_candle['close'] < prev_candle['open'] and curr_candle['close'] > curr_candle['open'] and is_strong_move:
                    order_blocks.append(prev_candle)
            
            if direction == "bearish":
                 # Last up candle before strong down move
                if prev_candle['close'] > prev_candle['open'] and curr_candle['close'] < curr_candle['open'] and is_strong_move:
                    order_blocks.append(prev_candle)

        return order_blocks

    def _is_mitigated(self, poi: Dict, data: List[Dict]) -> bool:
        """Checks if a POI has been touched by price."""
        # Check candles after the POI was formed
        relevant_candles = [c for c in data if c['time'] > poi['time']]
        for candle in relevant_candles:
            if candle['low'] <= poi['high'] and candle['high'] >= poi['low']:
                return True
        return False

    def _prepare_trade(self, bias: str, poi: Dict) -> Dict:
        """Constructs the final trade JSON object."""
        entry_price = poi['high'] if bias == "SELL" else poi['low']
        
        if bias == "BUY":
            sl = poi['low'] * 0.999 # A bit below the low
            # Find next swing high for TP
            swings = self._get_swing_points(self.h1_data)
            potential_tps = [s for s in swings['highs'] if s['time'] > poi['time']]
            tp = potential_tps[0]['high'] if potential_tps else entry_price * 1.01 # Fallback TP
        else: # SELL
            sl = poi['high'] * 1.001 # A bit above the high
            # Find next swing low for TP
            swings = self._get_swing_points(self.h1_data)
            potential_tps = [s for s in swings['lows'] if s['time'] > poi['time']]
            tp = potential_tps[0]['low'] if potential_tps else entry_price * 0.99 # Fallback TP
            
        return {
            "action": "taketrade",
            "order_type": bias,
            "entry": entry_price,
            "sl": sl,
            "tp": tp
        }

    def _format_no_trade(self, reason_code: str, log_message: str) -> Dict:
        """Formats the JSON for a no-trade decision."""
        # In a real system, you might log the log_message.
        return {"action": "don'ttaketrade", "reason": reason_code}


def main():
    """
    Main function to run the bot.
    Reads candlestick data from stdin, analyzes it, and prints the result to stdout.
    """
    try:
        # Read the entire input from stdin
        input_data = sys.stdin.read()
        if not input_data:
            print(json.dumps({"action": "don'ttaketrade", "reason": "INVALID_STRUCTURE", "details": "Input is empty"}), file=sys.stdout)
            return

        # Parse the JSON input
        data = json.loads(input_data)
        h4_data = data.get("h4_data")
        h1_data = data.get("h1_data")
        
        if not h4_data or not h1_data:
            print(json.dumps({"action": "don'ttaketrade", "reason": "INVALID_STRUCTURE", "details": "Missing h4_data or h1_data"}), file=sys.stdout)
            return

        # Initialize and run the bot
        bot = SMCBot(h4_data, h1_data)
        result = bot.analyze()
        
        # Print the result as a JSON string
        print(json.dumps(result), file=sys.stdout)

    except json.JSONDecodeError:
        print(json.dumps({"action": "don'ttaketrade", "reason": "INVALID_STRUCTURE", "details": "Invalid JSON format"}), file=sys.stdout)
    except Exception as e:
        # Catch any other unexpected errors during analysis
        print(json.dumps({"action": "don'ttaketrade", "reason": "INVALID_STRUCTURE", "details": str(e)}), file=sys.stdout)


if __name__ == '__main__':
    main()


