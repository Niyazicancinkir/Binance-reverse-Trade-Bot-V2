import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
import ccxt
from excel_logger import islemi_excel_kaydet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Trader")

@dataclass
class TradeOrderResult:
    success: bool
    symbol: str
    direction: str
    quantity: float
    entry_price: float
    order_id: str
    tp: Optional[float] = None
    sl: Optional[float] = None
    message: str = ""

class BinanceFuturesTrader:
    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None, testnet: bool = True, fixed_margin_usdt: float = 10.0, default_leverage: int = 10):
        self.api_key = (api_key or os.getenv("BINANCE_API_KEY", "")).strip().strip('"').strip("'")
        self.secret_key = (secret_key or os.getenv("BINANCE_SECRET_KEY", "")).strip().strip('"').strip("'")
        self.testnet = testnet
        self.fixed_margin_usdt = fixed_margin_usdt
        self.default_leverage = default_leverage

        self.exchange = ccxt.binance({
            "apiKey": self.api_key,
            "secret": self.secret_key,
            "enableRateLimit": True,
            "options": {"defaultType": "future", "adjustForTimeDifference": True}
        })
        if self.testnet:
            self.exchange.enable_demo_trading(True)
        self.exchange.load_markets()

    def get_open_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            positions = self.exchange.fetch_positions([symbol])
            for pos in positions:
                contracts = float(pos.get("contracts", 0) or pos.get("positionAmt", 0))
                if abs(contracts) > 0:
                    return {"symbol": symbol, "size": contracts, "side": pos.get("side", "LONG" if contracts > 0 else "SHORT")}
            return None
        except Exception as e:
            return None

    def execute_reverse_trade(
        self, 
        symbol: str, 
        reverse_direction: str, 
        tp: Optional[float] = None, 
        sl: Optional[float] = None, 
        leverage: Optional[int] = None, 
        channel_handles: str = "Bilinmiyor"
    ) -> TradeOrderResult:
        target_leverage = leverage or self.default_leverage

        if self.get_open_position(symbol):
            msg = f"Açık pozisyon var, atlandı."
            logger.warning(f"[{symbol}] {msg}")
            return TradeOrderResult(False, symbol, reverse_direction, 0.0, 0.0, "", message=msg)

        try:
            self.exchange.set_leverage(target_leverage, symbol)
            ticker = self.exchange.fetch_ticker(symbol)
            entry_price = float(ticker["last"])

            raw_quantity = (self.fixed_margin_usdt * target_leverage) / entry_price
            formatted_qty = float(self.exchange.amount_to_precision(symbol, raw_quantity))
            ccxt_side = "sell" if reverse_direction == "SHORT" else "buy"
            exit_side = "buy" if reverse_direction == "SHORT" else "sell"

            logger.info(f"Ana Emir İletiliyor: {symbol} | Yön: {reverse_direction} | Miktar: {formatted_qty}")

            order = self.exchange.create_market_order(symbol, ccxt_side, formatted_qty)
            order_id = str(order.get("id", "DEMO_ORDER"))

            TARGET_TP_PCT = 0.02  # Kanalın SL'si geçersizse, bizim varsayılan Kâr (TP) hedefimiz (%2  fiyat hareketi)
            TARGET_SL_PCT = 0.015 # Sabit Zarar Kes (SL) sınırımız (%1.5 fiyat hareketi = 10x'te %15 zarar)

            guvenli_tp = sl 
            
            guvenli_sl = None 

            if reverse_direction == "SHORT":
                
                if not guvenli_tp or guvenli_tp >= entry_price:
                    guvenli_tp = entry_price * (1 - TARGET_TP_PCT)
                    logger.info(f"[{symbol}] Orijinal SL geçersizdi. Varsayılan %2 TP uygulandı: {guvenli_tp}")
                else:
                    logger.info(f"[{symbol}] AVCI MODU: Kanalın SL'si bizim TP'miz olarak ayarlandı -> {guvenli_tp}")

                # Bizim Stop noktamız (SL), giriş fiyatının ÜSTÜNDE olmalıdır (%1.5 risk)
                if not guvenli_sl or guvenli_sl <= entry_price:
                    guvenli_sl = entry_price * (1 + TARGET_SL_PCT)
                    logger.info(f"[{symbol}] Sabit Korumalı SL (%1.5) oluşturuldu: {guvenli_sl}")

            else: # BİZ LONG GİRİYORUZ (Yani kanal SHORT girmişti)
                
                # Bizim Kâr hedefimiz (TP), giriş fiyatının ÜSTÜNDE olmalıdır.
                if not guvenli_tp or guvenli_tp <= entry_price:
                    guvenli_tp = entry_price * (1 + TARGET_TP_PCT)
                    logger.info(f"[{symbol}] Orijinal SL geçersizdi. Varsayılan %2 TP uygulandı: {guvenli_tp}")
                else:
                    logger.info(f"[{symbol}] AVCI MODU: Kanalın SL'si bizim TP'miz olarak ayarlandı -> {guvenli_tp}")

                # Bizim Stop noktamız (SL), giriş fiyatının ALTINDA olmalıdır (%1.5 risk)
                if not guvenli_sl or guvenli_sl >= entry_price:
                    guvenli_sl = entry_price * (1 - TARGET_SL_PCT)
                    logger.info(f"[{symbol}] Sabit Korumalı SL (%1.5) oluşturuldu: {guvenli_sl}")
            # ----------------------------------------------------

            # 2. Sınırlandırılmış ReduceOnly TP/SL Emirleri
            if guvenli_tp:
                tp_price = float(self.exchange.price_to_precision(symbol, guvenli_tp))
                self.exchange.create_order(
                    symbol, 'TAKE_PROFIT_MARKET', exit_side, formatted_qty, None,
                    params={'stopPrice': tp_price, 'reduceOnly': True}
                )
                logger.info(f"[{symbol}] ✅ Limitli TP Emri Borsaya İletildi ({tp_price})")
            
            if guvenli_sl:
                sl_price = float(self.exchange.price_to_precision(symbol, guvenli_sl))
                self.exchange.create_order(
                    symbol, 'STOP_MARKET', exit_side, formatted_qty, None,
                    params={'stopPrice': sl_price, 'reduceOnly': True}
                )
                logger.info(f"[{symbol}] ✅ Limitli SL Emri Borsaya İletildi ({sl_price})")

            # EXCEL KAYDI (Parametre isimleri excel_logger'daki yeni fonksiyonla tam eşleşecek şekilde güncellendi)
            try:
                islemi_excel_kaydet(
                    parite=symbol, 
                    yon=reverse_direction, 
                    kaldirac=target_leverage, 
                    giris_fiyati=entry_price, 
                    tp=guvenli_tp, 
                    sl=guvenli_sl, 
                    kanallar=channel_handles, 
                    miktar=formatted_qty
                )
            except Exception as e:
                logger.error(f"[{symbol}] Excel kayıt hatası: {e}")

            return TradeOrderResult(True, symbol, reverse_direction, formatted_qty, entry_price, order_id, guvenli_tp, guvenli_sl, "Başarılı")

        except Exception as e:
            return TradeOrderResult(False, symbol, reverse_direction, 0.0, 0.0, "", message=f"Hata: {e}")