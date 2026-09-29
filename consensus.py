import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ConsensusEngine")

@dataclass
class BufferedSignal:
    channel_id: int
    channel_handle: str
    channel_weight: float
    coin_pair: str
    original_direction: str  # "LONG" or "SHORT"
    reverse_direction: str   # "SHORT" or "LONG"
    tp: Optional[float] = None
    sl: Optional[float] = None
    received_at: float = field(default_factory=time.time)

@dataclass
class ConsensusDecision:
    coin_pair: str
    decision_direction: str  # "SHORT", "LONG", or "NEUTRAL"
    majority_original_direction: str  # "LONG", "SHORT", or "EQUAL"
    long_weight: float
    short_weight: float
    total_signals_count: int
    participating_channels: List[str]
    suggested_tp: Optional[float] = None
    suggested_sl: Optional[float] = None
    should_execute: bool = False
    reasoning: str = ""

class ConsensusEngine:
    def __init__(self, window_seconds: int = 300):
        """
        window_seconds: Konsensüs penceresi (Varsayılan 300 saniye = 5 dakika)
        """
        self.window_seconds = window_seconds
        self.buffer: List[BufferedSignal] = []
        
        # Dinamik Ters İtibar Skoru (Başlangıçta hepsi 1.0 nötr çarpan)
        self.reputation_scores: Dict[str, float] = {}

    def get_reputation(self, handle: str) -> float:
        return self.reputation_scores.get(handle, 1.0)
        
    def update_reputation(self, handle: str, multiplier: float):
        """Kanal çok yanılıyorsa çarpanı artır (Tersine mükemmel), çok biliyorsa düşür."""
        current = self.get_reputation(handle)
        self.reputation_scores[handle] = round(current * multiplier, 2)

    def add_signal(self, signal: BufferedSignal):
        """
        Tampona yeni bir sinyal ekler.
        """
        self.buffer.append(signal)
        logger.info(
            f"[CONSENSUS_BUFFER] @{signal.channel_handle} -> {signal.coin_pair} "
            f"Orijinal Yön: {signal.original_direction} (Ağırlık: {signal.channel_weight})"
        )

    def _prune_expired_signals(self):
        """
        Pencere süresi dolmuş eski sinyalleri temizler.
        """
        now = time.time()
        before_count = len(self.buffer)
        self.buffer = [s for s in self.buffer if (now - s.received_at) <= self.window_seconds]
        removed = before_count - len(self.buffer)
        if removed > 0:
            logger.debug(f"[CONSENSUS_PRUNE] {removed} adet süresi dolmuş sinyal tampondan silindi.")

    def evaluate_pair(self, coin_pair: str) -> Optional[ConsensusDecision]:
        """
        Belirtilen parite için konsensüs oylamasını yürütür.
        """
        self._prune_expired_signals()

        # Pariteye ait aktif sinyalleri filtrele (Ham liste)
        raw_pair_signals = [s for s in self.buffer if s.coin_pair == coin_pair]

        if not raw_pair_signals:
            return None

        # --- YENİ MANTIK: KANAL TEKİLLEŞTİRME KONTROLÜ (DEDUPLICATION) ---
        # Bir kanal aynı 5 dakikalık pencerede aynı coin için birden fazla sinyal attıysa,
        # sözlük mantığıyla sadece EN SON (en güncel) sinyali geçerli sayılır. Spam engellenir.
        unique_signals_map = {}
        for s in raw_pair_signals:
            unique_signals_map[s.channel_handle] = s 
            
        pair_signals = list(unique_signals_map.values())
        # -----------------------------------------------------------------

        # Ağırlık hesaplanırken Ters İtibar Skoru çarpana dahil ediliyor!
        long_weight = sum((s.channel_weight * self.get_reputation(s.channel_handle)) for s in pair_signals if s.original_direction == "LONG")
        short_weight = sum((s.channel_weight * self.get_reputation(s.channel_handle)) for s in pair_signals if s.original_direction == "SHORT")
        
        channels = [f"{s.channel_handle}(İtibar:{self.get_reputation(s.channel_handle)})" for s in pair_signals]

        # Ortalama TP / SL hesapla
        tps = [s.tp for s in pair_signals if s.tp is not None]
        sls = [s.sl for s in pair_signals if s.sl is not None]
        avg_tp = sum(tps) / len(tps) if tps else None
        avg_sl = sum(sls) / len(sls) if sls else None

        # Karar Mantığı (Inverse Trading Majority)
        if long_weight > short_weight:
            decision_dir = "SHORT"
            majority_orig = "LONG"
            should_execute = True
            reason = f"Çoğunluk LONG önerdi (Ağırlık: Long {long_weight:.1f} vs Short {short_weight:.1f}). Tersine SHORT açılıyor."
        elif short_weight > long_weight:
            decision_dir = "LONG"
            majority_orig = "SHORT"
            should_execute = True
            reason = f"Çoğunluk SHORT önerdi (Ağırlık: Short {short_weight:.1f} vs Long {long_weight:.1f}). Tersine LONG açılıyor."
        else:
            decision_dir = "NEUTRAL"
            majority_orig = "EQUAL"
            should_execute = False
            reason = f"Sinyaller eşit ağırlıkta (%50 Long / %50 Short). Nötr kalınıyor, işlem açılmadı."

        decision = ConsensusDecision(
            coin_pair=coin_pair,
            decision_direction=decision_dir,
            majority_original_direction=majority_orig,
            long_weight=long_weight,
            short_weight=short_weight,
            total_signals_count=len(pair_signals),
            participating_channels=channels,
            suggested_tp=avg_tp,
            suggested_sl=avg_sl,
            should_execute=should_execute,
            reasoning=reason
        )

        # Log çıktısını temiz tutmak için formatı düzenledim
        logger.info(
            f"\n[CONSENSUS_RESULT] Parite: {coin_pair}\n"
            f"  -> Toplam Sinyal : {len(pair_signals)} KANAL {channels}\n"
            f"  -> Long Ağırlık  : {long_weight:.2f}\n"
            f"  -> Short Ağırlık : {short_weight:.2f}\n"
            f"  -> Karar Yönü    : {decision_dir} (İşlem Açılacak mı: {should_execute})\n"
            f"  -> Açıklama      : {reason}\n"
        )

        return decision
    def clear_pair_signals(self, coin_pair: str):
        """
        İşlem açıldıktan sonra o pariteye ait işlenen sinyalleri tampondan temizler.
        """
        self.buffer = [s for s in self.buffer if s.coin_pair != coin_pair]


if __name__ == "__main__":
    engine = ConsensusEngine(window_seconds=300)

    # Test senaryosu 1: Çoğunluk LONG -> SHORT Kararı
    engine.add_signal(BufferedSignal(1, "BinanceKillers", 1.5, "BTCUSDT", "LONG", "SHORT", 58000, 68000))
    engine.add_signal(BufferedSignal(2, "CryptoSignals", 1.2, "BTCUSDT", "LONG", "SHORT", 57500, 67500))
    engine.add_signal(BufferedSignal(3, "TopTrading", 1.0, "BTCUSDT", "SHORT", "LONG", 68000, 58000))

    dec1 = engine.evaluate_pair("BTCUSDT")

    # Test senaryosu 2: Eşitlik (%50 - %50) -> NEUTRAL
    engine.clear_pair_signals("BTCUSDT")
    engine.add_signal(BufferedSignal(1, "GroupA", 1.0, "ETHUSDT", "LONG", "SHORT"))
    engine.add_signal(BufferedSignal(2, "GroupB", 1.0, "ETHUSDT", "SHORT", "LONG"))

    dec2 = engine.evaluate_pair("ETHUSDT")