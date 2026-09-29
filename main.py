import asyncio
import logging
import signal
import sys
import time
import json
import os
import hashlib
import pandas as pd
from typing import Set

from config_loader import BotConfig, get_active_channels, load_channels
from scraper import TelegramAsyncScraper
from parser import SignalParser
from consensus import ConsensusEngine, BufferedSignal
from trader import BinanceFuturesTrader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MainOrchestrator")


class AutonomousBotV2:
    def __init__(self):
        self.config = BotConfig()
        
        self.all_channels = load_channels()
        self.active_channels = get_active_channels()
        
        self.alias_map = {c.handle: c.name.replace(" ", "") for c in self.all_channels}

        logger.info("=" * 70)
        logger.info("🚀 Otonom Binance Inverse Trade Bot V2 Başlatılıyor")
        logger.info(f"📊 Aktif Kanal Sayısı   : {len(self.active_channels)}")
        logger.info(f"⏱️ Konsensüs Penceresi : {self.config.consensus_window_seconds} saniye")
        logger.info("=" * 70)

        self.scraper = TelegramAsyncScraper(channels=self.active_channels)
        self.parser = SignalParser()
        self.consensus = ConsensusEngine(window_seconds=self.config.consensus_window_seconds)
        self.trader = BinanceFuturesTrader(
            api_key=self.config.binance_api_key,
            secret_key=self.config.binance_secret_key,
            testnet=True,
            fixed_margin_usdt=self.config.fixed_margin_usdt,
            default_leverage=self.config.default_leverage
        )
        
        self.seen_messages_file = "seen_messages.json"
        self.seen_messages = self._load_seen_messages()
        
        self.is_running = True

    def _load_seen_messages(self) -> Set[str]:
        if os.path.exists(self.seen_messages_file):
            try:
                with open(self.seen_messages_file, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception as e:
                logger.warning(f"Geçmiş mesajlar okunamadı, sıfırdan başlanıyor: {e}")
                return set()
        return set()

    def _save_seen_messages(self):
        try:
            with open(self.seen_messages_file, "w", encoding="utf-8") as f:
                json.dump(list(self.seen_messages), f)
        except Exception as e:
            logger.error(f"Geçmiş mesajlar kaydedilemedi: {e}")

    def _get_msg_hash(self, channel_handle: str, text: str) -> str:
        raw_data = f"{channel_handle}_{text.strip()}"
        return hashlib.md5(raw_data.encode("utf-8")).hexdigest()

    def check_and_update_reputation(self):
        if not os.path.exists("islem_gecmisi.xlsx"):
            return
            
        try:
            df = pd.read_excel("islem_gecmisi.xlsx")
            guncelleme_yapildi = False
            
            rep_file = "reputation.json"
            reps = {}
            if os.path.exists(rep_file):
                with open(rep_file, "r", encoding="utf-8") as f:
                    reps = json.load(f)

            for idx, row in df.iterrows():
                if row["Durum"] == "Açık":
                    parite = row["Parite"]
                    kanallar_str = str(row.get("Kanal", ""))
                    
                    if not kanallar_str or kanallar_str.lower() == "nan":
                        continue

                    pos = self.trader.get_open_position(parite)
                    
                    if not pos: 
                        realized_pnl = 0.0
                        try:
                            trades = self.trader.exchange.fetch_my_trades(parite, limit=10)
                            for t in reversed(trades):
                                info = t.get('info', {})
                                rpnl = float(info.get('realizedPnl', 0.0))
                                
                                # --- YENİ: KOMİSYONU DÜŞEREK NET KÂRI HESAPLAMA ---
                                komisyon = float(info.get('commission', 0.0))
                                
                                if rpnl != 0.0:
                                    realized_pnl = rpnl - komisyon  # Artık brüt değil, cebimize giren NET para!
                                    break
                                # --------------------------------------------------
                        except Exception as e:
                            logger.error(f"[{parite}] Binance'den gerçekleşen PnL çekilemedi: {e}")

                        kanallar = kanallar_str.split(',')
                        for k in kanallar:
                            k = k.strip().replace('@', '')
                            if "(" in k:
                                k = k.split("(")[0].strip()
                            
                            if k and k.lower() != "nan":
                                mevcut_skor = reps.get(k, 1.0)
                                
                                if realized_pnl > 0:
                                    yeni_skor = round(mevcut_skor + 0.1, 2)
                                    logger.info(f"🟢 [İTİBAR ARTTI] {k} kanalı patladı (Net Kâr: ${realized_pnl:.2f}). Skor: {mevcut_skor} -> {yeni_skor}")
                                elif realized_pnl < 0:
                                    yeni_skor = round(max(0.1, mevcut_skor - 0.1), 2)
                                    logger.info(f"🔴 [İTİBAR DÜŞTÜ] {k} kanalı doğru bildi (Net Zarar: ${realized_pnl:.2f}). Skor: {mevcut_skor} -> {yeni_skor}")
                                else:
                                    yeni_skor = mevcut_skor
                                    logger.info(f"⚪ [İTİBAR DEĞİŞMEDİ] {k} kanalı başabaş kapandı (Net PnL: $0.00).")
                                
                                reps[k] = yeni_skor
                        
                        df.at[idx, "Durum"] = "Kapandı"
                        guncelleme_yapildi = True
            
            if guncelleme_yapildi:
                df.to_excel("islem_gecmisi.xlsx", index=False)
                with open(rep_file, "w", encoding="utf-8") as f:
                    json.dump(reps, f, indent=4)
                    
        except Exception as e:
            logger.error(f"[CHECKER_HATA] İtibar motoru güncellenirken hata: {e}")

    async def pnl_checker_loop(self):
        while self.is_running:
            logger.info("🕵️‍♂️ [CHECKER] 10 Dakikalık İtibar Kontrolcüsü uyanıyor...")
            self.check_and_update_reputation()
            logger.info("💤 [CHECKER] Kontrol tamamlandı. 10 dakika uykuya geçiliyor...")
            await asyncio.sleep(600)

    async def run_scrape_and_process_cycle(self):
        logger.info("\n--- [DÖNGÜ BAŞLADI] Telegram kanalları asenkron taranıyor... ---")
        raw_scraped_messages = await self.scraper.scrape_all_channels()

        if not raw_scraped_messages:
            return

        new_messages = []
        for msg in raw_scraped_messages:
            msg_hash = self._get_msg_hash(msg.channel_handle, msg.text)
            if msg_hash not in self.seen_messages:
                new_messages.append(msg)
                self.seen_messages.add(msg_hash)
                
        if not new_messages:
            logger.info("📭 Yeni mesaj bulunamadı (Tümü daha önce işlenmiş). Bot uyumaya devam ediyor.")
            return
            
        logger.info(f"📬 {len(new_messages)} adet YENİ mesaj tespit edildi! LLM'e gönderiliyor...")
        self._save_seen_messages()

        rep_file = "reputation.json"
        reps = {}
        if os.path.exists(rep_file):
            try:
                with open(rep_file, "r", encoding="utf-8") as f:
                    reps = json.load(f)
            except Exception:
                pass

        current_prices = {}
        for msg in new_messages:
            passed, symbol, _ = self.parser._prefilter(msg.text)
            if passed and symbol not in current_prices:
                try:
                    ticker = self.trader.exchange.fetch_ticker(symbol)
                    current_prices[symbol] = float(ticker['last'])
                except Exception:
                    pass

        detected_pairs: Set[str] = set()
        parse_results = self.parser.parse_signals_batch([msg.text for msg in new_messages], current_prices=current_prices)

        for msg, parse_res in zip(new_messages, parse_results):
            fake_handle = self.alias_map.get(msg.channel_handle, msg.channel_handle)
            
            dinamik_agirlik = reps.get(fake_handle, msg.channel_weight)
            
            if parse_res.is_signal and parse_res.coin_pair and parse_res.original_direction:
                buffered_sig = BufferedSignal(
                    channel_id=msg.channel_id,
                    channel_handle=fake_handle,
                    channel_weight=dinamik_agirlik,
                    coin_pair=parse_res.coin_pair,
                    original_direction=parse_res.original_direction,
                    reverse_direction=parse_res.reverse_direction,
                    tp=parse_res.tp,
                    sl=parse_res.sl,
                    received_at=time.time()
                )
                self.consensus.add_signal(buffered_sig)
                detected_pairs.add(parse_res.coin_pair)

        for pair in detected_pairs:
            decision = self.consensus.evaluate_pair(pair)

            if decision and decision.should_execute:
                logger.info(f"⚡ [KONSENSÜS ONAYI] {pair} için Tersine İşlem Kararı Alındı -> {decision.decision_direction}")
                
                katilan_kanallar = ",".join(decision.participating_channels)

                max_rep = 1.0
                for kanal in decision.participating_channels:
                    skor = reps.get(kanal, 1.0)
                    if skor > max_rep:
                        max_rep = skor
                
                uygulanacak_kaldirac = 20 if max_rep > 1.5 else self.config.default_leverage
                
                if max_rep > 1.5:
                    logger.info(f"🔥 [DİNAMİK RİSK] {katilan_kanallar} kanalının itibarı çok yüksek ({max_rep})! İşleme 20X KALDIRAÇ ile giriliyor.")
                else:
                    logger.info(f"⚖️ [STANDART RİSK] Kanalların maks itibarı {max_rep}. Standart {uygulanacak_kaldirac}X kaldıraç uygulanıyor.")

                trade_res = self.trader.execute_reverse_trade(
                    symbol=pair,
                    reverse_direction=decision.decision_direction,
                    tp=decision.suggested_tp,
                    sl=decision.suggested_sl,
                    leverage=uygulanacak_kaldirac,
                    channel_handles=katilan_kanallar
                )

                if trade_res.success:
                    logger.info(f"✅ [İŞLEM BAŞARILI] {pair} {decision.decision_direction} pozisyonu başarıyla açıldı!")
                    self.consensus.clear_pair_signals(pair)
                else:
                    logger.warning(f"⚠️ [İŞLEM BAŞARISIZ / ATLANDI] {trade_res.message}")

    async def bot_loop(self):
        while self.is_running:
            try:
                await self.run_scrape_and_process_cycle()
            except Exception as e:
                logger.error(f"[DÖNGÜ_HATASI] Beklenmeyen hata: {e}", exc_info=True)
            await asyncio.sleep(self.config.scrape_interval_seconds)

    async def start(self):
        await asyncio.gather(
            self.bot_loop(),
            self.pnl_checker_loop()
        )

    def stop(self):
        logger.info("🛑 Bot durduruluyor...")
        self.is_running = False

async def main():
    bot = AutonomousBotV2()
    def handle_sigint():
        logger.info("\n[KAPATMA] Kesme sinyali alındı.")
        bot.stop()

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_sigint)
    except NotImplementedError:
        pass

    try:
        await bot.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("[KAPATMA] Bot sonlandırıldı.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[ÇIKIŞ] Bot kapatıldı.")