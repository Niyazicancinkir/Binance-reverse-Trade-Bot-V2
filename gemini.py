import json
import logging
import os
import time
import threading
from collections import deque
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger("SignalResolver")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)

# Kota koruması: dakikada en fazla N istek (token tasarrufu için)
GEMINI_MAX_REQUESTS_PER_MINUTE = 5
_gemini_call_timestamps = deque()
_gemini_rate_lock = threading.Lock()


def _gemini_rate_limit_wait():
    """Gemini çağrılarını dakikada GEMINI_MAX_REQUESTS_PER_MINUTE ile sınırlar."""
    with _gemini_rate_lock:
        while True:
            now = time.time()
            while _gemini_call_timestamps and now - _gemini_call_timestamps[0] > 60:
                _gemini_call_timestamps.popleft()

            if len(_gemini_call_timestamps) < GEMINI_MAX_REQUESTS_PER_MINUTE:
                _gemini_call_timestamps.append(now)
                return

            wait_time = 60 - (now - _gemini_call_timestamps[0]) + 0.1
            logger.info(f"[AI_RATE_LIMIT] Dakikalık istek limiti doldu, {wait_time:.1f} sn bekleniyor...")
            time.sleep(wait_time)


# --- Çıktı Şeması ---
class SignalSchema(BaseModel):
    is_signal: bool = Field(
        description=(
            "Mesaj bir alım/satım, işlem veya analiz sinyali içeriyor mu?"
        )
    )
    coin_pair: Optional[str] = Field(
        default=None,
        description="İşlem yapılan parite veya sembol (örn: BTCUSDT, APTUSD, ETH)",
    )
    fenomen_yonu: Optional[str] = Field(
        default=None,
        description="Önerilen yön: BUY, LONG, SELL, SHORT (Bilinmiyorsa null)",
    )
    entry: Optional[float] = Field(
        default=None,
        description="Giriş fiyatı (Aralık ise ilk rakam veya ortalama)",
    )
    sl: Optional[float] = Field(
        default=None, description="Zarar kes (Stop Loss) seviyesi"
    )
    tp: Optional[float] = Field(
        default=None,
        description="Kâr al (Take Profit / TP1) ilk hedef seviyesi",
    )


def _to_float(val: Any) -> Optional[float]:
    """None, str ve sayısal değerleri güvenli biçimde float'a çevirir (TypeError önleyici)."""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = val.replace("$", "").replace(",", "").strip()
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def _yon_hesapla(
    fenomen_yonu_raw: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Fenomen yönünü standartlaştırır ve ters pozisyon yönünü belirler."""
    if not fenomen_yonu_raw:
        return None, None

    raw = str(fenomen_yonu_raw).strip().upper()
    if any(w in raw for w in ["BUY", "LONG", "AL", "ALIS"]):
        return "LONG", "SHORT"
    elif any(w in raw for w in ["SELL", "SHORT", "SAT", "SATIS"]):
        return "SHORT", "LONG"

    return raw, ("SHORT" if raw == "LONG" else "LONG")


def sinyali_cozumle(mesaj_metni: str) -> Dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")

    if not api_key or api_key == "your_gemini_api_key":
        logger.error("[AI_HATA] GEMINI_API_KEY bulunamadı!")
        return {
            "is_signal": False,
            "error": "API Key eksik veya geçersiz",
            "sebep": "NO_API_KEY",
        }

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(headers={"x-goog-api-key": api_key}),
        )
    except Exception as e:
        logger.error(f"[AI_HATA] Client oluşturulamadı: {e}")
        return {"is_signal": False, "error": str(e)}

    prompt = f"""
Sen uzman bir kripto ve finans işlem sinyali ayrıştırıcısısın.
Aşağıdaki Telegram mesajını analiz et:

KURALLAR:
1. SİNYAL TANIMI: Bir varlık adı/paritesi (örn: BTC, APTUSD, AKEUSDT, ETH) ile birlikte bir yön (BUY/LONG, SELL/SHORT) veya giriş/hedef seviyesi veriliyorsa bu BİR SİNYALDİR (is_signal: true).
2. SL (Stop Loss) veya TP (Take Profit) verilmese dahi işlem yönü ve coin belirtilmişse bu bir sinyaldir.
3. SADECE şunlar sinyal DEĞİLDİR (is_signal: false):
   - Genel piyasa sohbeti, haberler ve duyurular.
   - Sadece geçmiş kâr/başarı kutlamaları (örn: "970% total profit for September", "Targets keep falling").
   - İçinde işlem bilgisi olmayan sadece VIP üyelik/reklam mesajları.
4. Fiyat aralıklarında (örn: "1.6100 - 1.6150") ilk rakamı sayı olarak al. '$' veya metin koyma.

Mesaj:
\"\"\"{mesaj_metni}\"\"\"
"""

    # Model listesi: Biri yanıt vermez veya 404 dönerse diğerine geçer
    models_to_try = [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ]

    response = None
    last_err = None

    for model_name in models_to_try:
        try:
            _gemini_rate_limit_wait()
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SignalSchema,
                    temperature=0.1,
                ),
            )
            if response and response.text:
                break
        except Exception as err:
            last_err = err
            logger.warning(
                f"[AI_UYARI] {model_name} ile çağrı başarısız oldu: {err}. Sıradaki deneniyor..."
            )
            continue

    if not response or not response.text:
        logger.error(f"[AI_HATA] Hiçbir model yanıt üretemedi. Son Hata: {last_err}")
        return {
            "is_signal": False,
            "error": f"Model hatası: {last_err}",
            "sebep": "MODEL_FAILED",
        }

    try:
        veri = json.loads(response.text)
    except json.JSONDecodeError as jde:
        logger.error(f"[AI_HATA] JSON parse hatası: {jde} | Raw: {response.text}")
        return {"is_signal": False, "error": "JSONDecodeError"}

    if not veri.get("is_signal"):
        return {
            "is_signal": False,
            "coin_pair": veri.get("coin_pair"),
            "sebep": "LLM sinyal olarak onaylamadı",
        }

    # Güvenli tip dönüşümleri ve yön atamaları
    std_yon, ters_yon = _yon_hesapla(veri.get("fenomen_yonu"))
    entry_val = _to_float(veri.get("entry"))
    eski_tp = _to_float(veri.get("tp"))
    eski_sl = _to_float(veri.get("sl"))

    return {
        "is_signal": True,
        "coin_pair": veri.get("coin_pair"),
        "fenomen_yonu": std_yon,
        "bizim_yonumuz": ters_yon,
        "entry": entry_val,
        "eski_tp": eski_tp,
        "eski_sl": eski_sl,
        # Ters Pozisyon: Fenomenin SL noktası bizim Kâr Hedefimiz (TP) olur,
        # Fenomenin TP noktası bizim Zarar Durdurma (SL) noktamız olur.
        "bizim_tp": eski_sl,
        "bizim_sl": eski_tp,
    }


def seviyeleri_tamamla(
    sinyal_verisi: Dict[str, Any],
    varsayilan_sl_yuzde: float = 0.015,  # %1.5 Fiyat Hareketi SL
    varsayilan_tp_yuzde: float = 0.030,  # %3.0 Fiyat Hareketi TP
    anlik_borsa_fiyati: Optional[float] = None,
) -> Dict[str, Any]:
    """Eksik TP, SL veya Entry değerlerini varsayılan risk yönetimi oranlarıyla otomatik hesaplar."""
    if not sinyal_verisi.get("is_signal"):
        return sinyal_verisi

    bizim_yon = sinyal_verisi.get("bizim_yonumuz")
    entry = sinyal_verisi.get("entry")

    # 1. Giriş fiyatı mesajda yoksa borsa anlık fiyatını baz al
    if not entry and anlik_borsa_fiyati:
        entry = anlik_borsa_fiyati
        sinyal_verisi["entry"] = entry
        sinyal_verisi["entry_kaynagi"] = "ANLIK_BORSA_FIYATI"
    else:
        sinyal_verisi["entry_kaynagi"] = "MESAJ"

    # Giriş fiyatı olmadan yüzdesel TP/SL türetilemez
    if not entry or not bizim_yon:
        return sinyal_verisi

    bizim_tp = sinyal_verisi.get("bizim_tp")
    bizim_sl = sinyal_verisi.get("bizim_sl")

    # 2. SHORT Pozisyonu İçin Eksik Seviyeleri Doldur
    if bizim_yon == "SHORT":
        # Hedef (TP) girişin altında olmalıdır
        if not bizim_tp or bizim_tp >= entry:
            sinyal_verisi["bizim_tp"] = round(entry * (1 - varsayilan_tp_yuzde), 6)
            sinyal_verisi["tp_turu"] = "OTOMATIK_HESAPLANDI"
        else:
            sinyal_verisi["tp_turu"] = "FENOMEN_SL_DONUSUMU"

        # Zarar Durdur (SL) girişin üstünde olmalıdır
        if not bizim_sl or bizim_sl <= entry:
            sinyal_verisi["bizim_sl"] = round(entry * (1 + varsayilan_sl_yuzde), 6)
            sinyal_verisi["sl_turu"] = "OTOMATIK_HESAPLANDI"
        else:
            sinyal_verisi["sl_turu"] = "FENOMEN_TP_DONUSUMU"

    # 3. LONG Pozisyonu İçin Eksik Seviyeleri Doldur
    elif bizim_yon == "LONG":
        # Hedef (TP) girişin üstünde olmalıdır
        if not bizim_tp or bizim_tp <= entry:
            sinyal_verisi["bizim_tp"] = round(entry * (1 + varsayilan_tp_yuzde), 6)
            sinyal_verisi["tp_turu"] = "OTOMATIK_HESAPLANDI"
        else:
            sinyal_verisi["tp_turu"] = "FENOMEN_SL_DONUSUMU"

        # Zarar Durdur (SL) girişin altında olmalıdır
        if not bizim_sl or bizim_sl >= entry:
            sinyal_verisi["bizim_sl"] = round(entry * (1 - varsayilan_sl_yuzde), 6)
            sinyal_verisi["sl_turu"] = "OTOMATIK_HESAPLANDI"
        else:
            sinyal_verisi["sl_turu"] = "FENOMEN_TP_DONUSUMU"

    return sinyal_verisi


if __name__ == "__main__":
    # Fenomen sadece Buy demiş, hiçbir fiyat ve hedef vermemiş:
    ham_sinyal = {
        "is_signal": True,
        "coin_pair": "SAGAUSDT",
        "fenomen_yonu": "LONG",
        "bizim_yonumuz": "SHORT",
        "entry": None,
        "bizim_tp": None,
        "bizim_sl": None,
    }

    # Binance'den anlık SAGAUSDT fiyatının 2.50 olduğu senaryo:
    tamamlanmis = seviyeleri_tamamla(
        ham_sinyal,
        varsayilan_sl_yuzde=0.02,  # %2 SL
        varsayilan_tp_yuzde=0.04,  # %4 TP
        anlik_borsa_fiyati=2.50,
    )

    print(tamamlanmis)
    # Çıktı:
    # Entry: 2.50
    # Bizim Yön: SHORT
    # Bizim TP: 2.40  (%4 kâr hedefi)
    # Bizim SL: 2.55  (%2 stop seviyesi)