import json
import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("ConfigLoader")

@dataclass
class ChannelConfig:
    id: int
    handle: str
    name: str
    url: str
    market_type: str  # "crypto", "forex", "mixed"
    weight: float
    active: bool
    notes: Optional[str] = ""

@dataclass
class BotConfig:
    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", "").strip().strip('"').strip("'"))
    binance_secret_key: str = field(default_factory=lambda: os.getenv("BINANCE_SECRET_KEY", "").strip().strip('"').strip("'"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'"))
    
    # --- DÜZELTME 1: Tüm çevresel değişkenler lambda ile dinamik hale getirildi ---
   # 5 Dakika (300 saniye) Konsensüs bekleme odası
    consensus_window_seconds: int = field(default_factory=lambda: int(os.getenv("CONSENSUS_WINDOW_SECONDS", "300")))
    
    # Kâr/Zarar Limitleri (Kendi riskine göre ayarlayabilirsin)
    fixed_margin_usdt: float = field(default_factory=lambda: float(os.getenv("FIXED_MARGIN_USDT", "10.0")))
    default_leverage: int = field(default_factory=lambda: int(os.getenv("DEFAULT_LEVERAGE", "10")))
    
    # 1 Dakika (60 saniye) Telegram tarama sıklığı
    scrape_interval_seconds: int = field(default_factory=lambda: int(os.getenv("SCRAPE_INTERVAL_SECONDS", "60")))
    jitter_min_seconds: float = field(default_factory=lambda: float(os.getenv("JITTER_MIN_SECONDS", "1.0")))
    jitter_max_seconds: float = field(default_factory=lambda: float(os.getenv("JITTER_MAX_SECONDS", "3.0")))

SUPPORTED_PAIRS: Set[str] = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "NEARUSDT", "MATICUSDT", "PEPEUSDT", "SUIUSDT", "FETUSDT",
    "ARBUSDT", "OPUSDT", "APTUSDT", "LTCUSDT", "BCHUSDT",
    "SHIBUSDT", "TRXUSDT", "UNIUSDT", "ATOMUSDT", "INJUSDT",
    "ETCUSDT", "FILUSDT", "TIAUSDT", "SEIUSDT", "RENDERUSDT"
}

UNSUPPORTED_ASSET_PATTERNS: Set[str] = {
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "USDCAD", "AUDUSD", "NZDUSD", "USDCHF", "WTI",
    "BRENT", "GOLD", "SILVER", "OIL", "US30", "US500", "NAS100"
}

def load_channels(json_path: str = "channels.json") -> List[ChannelConfig]:
    """
    channels.json dosyasından kanal yapılandırmalarını asenkron veya senkron yükler.
    """
    path = Path(json_path)
    if not path.exists():
        # Fallback to parent or relative path if executed from different working directory
        path = Path(__file__).parent / json_path

    if not path.exists():
        raise FileNotFoundError(f"Kanal yapılandırma dosyası bulunamadı: {json_path}")

    # --- DÜZELTME 2: JSON Format Hatası Kalkanı Eklendi ---
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"[KRİTİK HATA] {json_path} dosyasının formatı bozuk! Virgül hatası olabilir: {e}")
        return []

    channels: List[ChannelConfig] = []
    for item in data:
        channel = ChannelConfig(
            id=item.get("id", 0),
            handle=item.get("handle", ""),
            name=item.get("name", ""),
            url=item.get("url", f"https://t.me/s/{item.get('handle', '')}"),
            market_type=item.get("market_type", "crypto"),
            weight=float(item.get("weight", 1.0)),
            active=bool(item.get("active", True)),
            notes=item.get("notes", "")
        )
        channels.append(channel)

    return channels

def get_active_channels(json_path: str = "channels.json") -> List[ChannelConfig]:
    """Sadece aktif olan kanalları filtreler."""
    all_channels = load_channels(json_path)
    return [c for c in all_channels if c.active]

if __name__ == "__main__":
    ch_list = load_channels()
    print(f"Toplam Yüklenen Kanal Sayısı: {len(ch_list)}")
    print(f"Aktif Kanal Sayısı: {len([c for c in ch_list if c.active])}")