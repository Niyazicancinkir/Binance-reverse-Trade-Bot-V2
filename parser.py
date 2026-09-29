import json
import logging
import os
import re
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from config_loader import UNSUPPORTED_ASSET_PATTERNS

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Parser")

BATCH_CHUNK_SIZE = 5
DEFAULT_SL_PCT = 0.015
DEFAULT_TP_PCT = 0.030
DEFAULT_LEVERAGE = 10

MODEL_LIMITS = {
    "gemini-3.1-flash-lite": 14,
    "gemini-3.5-flash-lite": 14
}

_model_usage = {m: deque() for m in MODEL_LIMITS}
_rate_lock = threading.Lock()

def _get_available_model() -> str:
    with _rate_lock:
        while True:
            now = time.time()

            for m in MODEL_LIMITS:
                while _model_usage[m] and now - _model_usage[m][0] > 60:
                    _model_usage[m].popleft()

            for m, limit in MODEL_LIMITS.items():
                if len(_model_usage[m]) < limit:
                    _model_usage[m].append(now)
                    return m

            oldest_times = {m: _model_usage[m][0] for m in MODEL_LIMITS if _model_usage[m]}
            soonest_model = min(oldest_times, key=oldest_times.get)
            wait_time = 60 - (now - oldest_times[soonest_model]) + 0.1
            
            logger.info(f"[AI_KOTA] Her iki modelin kapasitesi (30 RPM) doldu! {wait_time:.1f} sn bekleniyor...")
            time.sleep(wait_time)


def get_genai_client() -> Optional[genai.Client]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    if not api_key or api_key == "your_gemini_api_key":
        logger.error("[PARSER_ERROR] GEMINI_API_KEY bulunamadı!")
        return None
    try:
        return genai.Client(api_key=api_key, http_options=types.HttpOptions(headers={"x-goog-api-key": api_key}))
    except Exception as e:
        logger.error(f"[PARSER_ERROR] Gemini Client oluşturma hatası: {e}")
        return None

class SignalParseResult(BaseModel):
    is_signal: bool = Field(description="Mesaj geçerli bir alım-satım sinyali içeriyor mu?")
    coin_pair: Optional[str] = Field(default=None, description="Standart sembol örn: BTCUSDT")
    original_direction: Optional[str] = Field(default=None, description="Fenomenin yönü: LONG veya SHORT")
    reverse_direction: Optional[str] = Field(default=None, description="Bizim ters yönümüz: SHORT veya LONG")
    entry: Optional[float] = Field(default=None, description="Giriş seviyesi")
    tp: Optional[float] = Field(default=None, description="Ters pozisyon için hesaplanan Hedef TP")
    sl: Optional[float] = Field(default=None, description="Ters pozisyon için hesaplanan Zarar Kes SL")
    leverage: Optional[int] = Field(default=DEFAULT_LEVERAGE, description="Kaldıraç oranı")
    reasoning: Optional[str] = Field(default=None, description="Kısa açıklama")
    tp_source: Optional[str] = Field(default=None, description="TP kaynağı")
    sl_source: Optional[str] = Field(default=None, description="SL kaynağı")

class SignalSchema(BaseModel):
    is_signal: bool = Field(description="Mesaj bir alım/satım sinyali mi?")
    original_direction: Optional[str] = Field(default=None, description="LONG veya SHORT")
    entry: Optional[float] = Field(default=None, description="Giriş fiyatı")
    original_tp: Optional[float] = Field(default=None, description="İlk hedef fiyatı")
    original_sl: Optional[float] = Field(default=None, description="Stop-loss fiyatı")
    leverage: Optional[int] = Field(default=DEFAULT_LEVERAGE, description="Kaldıraç")
    reasoning: Optional[str] = Field(default=None, description="Gerekçe")

class BatchSignalItem(SignalSchema):
    index: int = Field(description="Girdi listesindeki mesaj sırası")

def normalize_text(text: str) -> str:
    translation_table = str.maketrans("ıİğĞüÜşŞöÖçÇ", "iigguussoocc")
    return text.translate(translation_table).lower()

CRITICAL_KEYWORDS = {
    "long", "short", "buy", "sell", "al", "sat", "alis", "satis",
    "entry", "giris", "tp", "sl", "take profit", "stop loss", "stop",
    "target", "targets", "hedef", "kar al", "zarar kes", "leverage",
    "kaldirac", "signal", "sinyal", "parite", "trade", "cmp", "🎯", "🛑", "🟢", "🔴", "🚀"
}

def regex_keyword_prefilter(text: str) -> bool:
    norm_text = normalize_text(text)
    return any(kw in norm_text or kw in text for kw in CRITICAL_KEYWORDS) or bool(re.search(r"\b(tp\d?|sl|t[1-5])\b", norm_text))

def asset_validator(text: str) -> Tuple[bool, Optional[str], Optional[str]]:
    text_upper = text.upper()

    for pattern in UNSUPPORTED_ASSET_PATTERNS:
        regex_pattern = rf"\b{pattern[:3]}[/_\-]?{pattern[3:]}\b|\b{pattern}\b"
        if re.search(regex_pattern, text_upper):
            return False, "UNSUPPORTED", pattern

    explicit_match = re.search(r"\b([A-Z0-9]{2,10})[/_\-]?(?:USDT|USD)(?:\.P)?\b", text_upper)
    if explicit_match:
        base = explicit_match.group(1)
        return True, f"{base}USDT", None

    marked_candidates = re.findall(r"[#$]([A-Z0-9]{2,10})\b", text_upper)
    if marked_candidates:
        for cand in marked_candidates:
            if not cand.isdigit():
                return True, f"{cand}USDT", None

    return False, "NO_PAIR", None

def _to_float(value: Any) -> Optional[float]:
    if value is None: return None
    try:
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
        result = float(value)
        return result if result > 0 else None
    except (TypeError, ValueError): return None

def _to_int(value: Any, default: int = DEFAULT_LEVERAGE) -> int:
    try:
        return int(float(value)) if value is not None else default
    except (TypeError, ValueError): return default

def _normalize_direction(raw: Optional[str]) -> Optional[str]:
    if not raw: return None
    norm = str(raw).strip().upper()
    if any(w in norm for w in ("BUY", "LONG", "AL", "ALIS")): return "LONG"
    if any(w in norm for w in ("SELL", "SHORT", "SAT", "SATIS")): return "SHORT"
    return norm if norm in ("LONG", "SHORT") else None

def _reverse_and_fill_levels(original_direction: str, entry: Optional[float], original_tp: Optional[float], original_sl: Optional[float], current_price: Optional[float], sl_pct: float, tp_pct: float) -> Tuple:
    reverse_direction = "SHORT" if original_direction == "LONG" else "LONG"
    effective_entry = entry if entry and entry > 0 else current_price
    bizim_tp = original_sl if original_sl and original_sl > 0 else None
    bizim_sl = original_tp if original_tp and original_tp > 0 else None
    tp_source = "FENOMEN_DONUSUMU" if bizim_tp else None
    sl_source = "FENOMEN_DONUSUMU" if bizim_sl else None

    if not effective_entry: return reverse_direction, effective_entry, bizim_tp, bizim_sl, tp_source, sl_source

    if reverse_direction == "SHORT":
        if not bizim_tp or bizim_tp >= effective_entry:
            bizim_tp, tp_source = round(effective_entry * (1 - tp_pct), 6), "OTOMATIK_HESAPLANDI"
        if not bizim_sl or bizim_sl <= effective_entry:
            bizim_sl, sl_source = round(effective_entry * (1 + sl_pct), 6), "OTOMATIK_HESAPLANDI"
    else:
        if not bizim_tp or bizim_tp <= effective_entry:
            bizim_tp, tp_source = round(effective_entry * (1 + tp_pct), 6), "OTOMATIK_HESAPLANDI"
        if not bizim_sl or bizim_sl >= effective_entry:
            bizim_sl, sl_source = round(effective_entry * (1 - sl_pct), 6), "OTOMATIK_HESAPLANDI"

    return reverse_direction, effective_entry, bizim_tp, bizim_sl, tp_source, sl_source

class SignalParser:
    def __init__(self, sl_pct: float = DEFAULT_SL_PCT, tp_pct: float = DEFAULT_TP_PCT, batch_chunk_size: int = BATCH_CHUNK_SIZE):
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct
        self.batch_chunk_size = max(1, batch_chunk_size)

    def _prefilter(self, text: str) -> Tuple[bool, Optional[str], Optional[str]]:
        if not regex_keyword_prefilter(text): return False, None, "Finansal kelime içermiyor."
        is_valid, detected_pair, unsupp = asset_validator(text)
        if not is_valid: return False, None, f"Geçersiz/Desteklenmeyen Varlık: {unsupp or 'Yok'}"
        return True, detected_pair, None

    @staticmethod
    def _build_batch_prompt(chunk: List[Tuple[int, str, str]]) -> str:
        items_json = ",\n".join(json.dumps({"index": i, "coin_pair": pair, "mesaj": text}, ensure_ascii=False) for i, text, pair in chunk)
        return f"""Sen uzman bir kripto ayrıştırıcısısın. Her mesajı analiz et ve GİRDİDEKİ TÜM "index" DEĞERLERİ İÇİN BİR SONUÇ NESNESİ ÜRET.
KURALLAR: Yön (BUY/LONG veya SELL/SHORT) ve coin varsa sinyaldir (is_signal: true). Kâr kutlaması veya sohbet sinyal değildir.
MESAJLAR:
[{items_json}]
"""

    def _call_llm(self, prompt: str, response_schema: Any) -> Optional[str]:
        client = get_genai_client()
        if not client: return None

        for _ in range(len(MODEL_LIMITS)):
            model_name = _get_available_model() 
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        temperature=0.1,
                    ),
                )
                if response and response.text:
                    logger.debug(f"[LLM_ROUTING] Başarılı istek: {model_name} üzerinden işlendi.")
                    return response.text
            except Exception as e:
                logger.warning(f"[PARSER_WARN] {model_name} modeli hata verdi: {e}. Yük dengeleyici diğer modele geçiyor...")
                continue
                
        return None

    def _finalize_result(self, detected_pair: str, data: Dict[str, Any], current_price: Optional[float]) -> SignalParseResult:
        if not data.get("is_signal"): return SignalParseResult(is_signal=False, coin_pair=detected_pair, reasoning="Geçersiz LLM sinyali.")
        original_direction = _normalize_direction(data.get("original_direction"))
        if not original_direction: return SignalParseResult(is_signal=False, coin_pair=detected_pair, reasoning="Geçerli yön yok.")
        
        entry = _to_float(data.get("entry"))
        original_tp = _to_float(data.get("original_tp"))
        original_sl = _to_float(data.get("original_sl"))
        leverage = _to_int(data.get("leverage"), DEFAULT_LEVERAGE)

        rev_dir, eff_entry, b_tp, b_sl, tp_src, sl_src = _reverse_and_fill_levels(original_direction, entry, original_tp, original_sl, current_price, self.sl_pct, self.tp_pct)

        return SignalParseResult(is_signal=True, coin_pair=detected_pair, original_direction=original_direction, reverse_direction=rev_dir, entry=eff_entry, tp=b_tp, sl=b_sl, leverage=leverage, reasoning="Başarılı", tp_source=tp_src, sl_source=sl_src)

    def parse_signals_batch(self, texts: List[str], current_prices: Optional[Dict[str, float]] = None) -> List[SignalParseResult]:
        current_prices = current_prices or {}
        results: List[Optional[SignalParseResult]] = [None] * len(texts)
        candidates = [(i, text, p) for i, text in enumerate(texts) if (passed := self._prefilter(text)) and passed[0] and (p := passed[1])]
        
        for i, text in enumerate(texts):
            if not self._prefilter(text)[0]: results[i] = SignalParseResult(is_signal=False, reasoning=self._prefilter(text)[2])

        for chunk_start in range(0, len(candidates), self.batch_chunk_size):
            chunk = candidates[chunk_start : chunk_start + self.batch_chunk_size]
            prompt = self._build_batch_prompt(chunk)
            raw = self._call_llm(prompt, list[BatchSignalItem])

            parsed_by_index = {}
            if raw:
                try:
                    for item in json.loads(raw):
                        if isinstance(item, dict) and "index" in item: parsed_by_index[int(item["index"])] = item
                except: pass

            for i, text, detected_pair in chunk:
                data = parsed_by_index.get(i)
                if data is None:
                    results[i] = SignalParseResult(is_signal=False, reasoning="LLM bu veriyi (dropout) atladı.")
                    continue
                results[i] = self._finalize_result(detected_pair, data, current_prices.get(detected_pair))

        return results