import asyncio
import os
import json
import hashlib
import pandas as pd
import streamlit as st

from config_loader import BotConfig, load_channels, get_active_channels
from scraper import TelegramAsyncScraper
from parser import SignalParser
from consensus import ConsensusEngine, BufferedSignal
from trader import BinanceFuturesTrader
from excel_logger import excel_kar_zarar_guncelle

st.set_page_config(page_title="Tersine İşlem Botu V2 | Kontrol Paneli", page_icon="⚡", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    [data-testid="stMetric"] { background-color: #1e293b !important; padding: 18px !important; border-radius: 12px !important; border: 1px solid #334155 !important; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2) !important; }
    [data-testid="stMetricLabel"] { color: #94a3b8 !important; font-size: 0.95rem !important; font-weight: 600 !important; }
    [data-testid="stMetricValue"] { color: #f8fafc !important; font-size: 1.5rem !important; font-weight: 700 !important; }
    [data-testid="stMetricDelta"] { color: #38bdf8 !important; font-weight: 600 !important; }
    [data-testid="stSidebar"] { border-right: 1px solid #334155; }
    </style>
""", unsafe_allow_html=True)

def display_dataframe(df):
    try: st.dataframe(df, width="stretch")
    except: st.dataframe(df, use_container_width=True)

def display_button(label, **kwargs):
    try: return st.button(label, width="stretch", **kwargs)
    except: return st.button(label, use_container_width=True, **kwargs)

config = BotConfig()
trader = BinanceFuturesTrader(api_key=config.binance_api_key, secret_key=config.binance_secret_key, testnet=True, fixed_margin_usdt=config.fixed_margin_usdt, default_leverage=config.default_leverage)

if "init_guncelleme" not in st.session_state:
    if os.path.exists("islem_gecmisi.xlsx"):
        try: excel_kar_zarar_guncelle(trader.exchange)
        except: pass
    st.session_state["init_guncelleme"] = True

st.title("⚡ Tersine İşlem Botu V2 (Otonom Multi-Channel & Consensus)")
st.caption("Çoklu Telegram Kanal Taraması, Akıllı Asset Validator, Konsensüs Motoru ve Binance Testnet Paneli")

active_channels = get_active_channels()
all_channels = load_channels()

alias_map = {c.handle: c.name.replace(" ", "") for c in all_channels}

st.sidebar.header("🎛️ V2 Kontrol Merkezi")
st.sidebar.metric("Toplam Kanal", len(all_channels))
st.sidebar.metric("Aktif Taranan Kanal", len(active_channels))
with st.sidebar:
    st.divider() 
    calistir_butonu = display_button("🚀 V2 Asenkron Taramayı Tetikle", key="btn_main_scan")

col1, col2, col3, col4 = st.columns(4)
col1.metric(label="Sistem Durumu", value="V2 Aktif / Hazır", delta="Async Scraper")
col2.metric(label="Mod Stratejisi", value="Inverse (Tersine)", delta="LONG ↔ SHORT")
col3.metric(label="Konsensüs Motoru", value=f"{config.consensus_window_seconds // 60} Dk Pencere", delta="Çoğunluk Tersi")
col4.metric(label="Borsa Ağı", value="Binance Demo", delta="Futures Testnet")

st.divider()

tab1, tab2, tab3 = st.tabs(["📊 Canlı Sinyal & Konsensüs Taraması", "📡 Kanal Yönetimi (channels.json)", "📁 Geçmiş İşlemler & Canlı PnL"])

with tab1:
    st.subheader("🚀 Çoklu Kanal Asenkron Taraması ve İşlem Yürütme")
    ui_consensus = ConsensusEngine(window_seconds=config.consensus_window_seconds)

    if calistir_butonu:
        with st.spinner("Kanallar asenkron olarak taranıyor, geçmiş mesajlar filtreleniyor..."):
            
            async def run_v2_scan():
                scraper = TelegramAsyncScraper(active_channels)
                parser = SignalParser()
                scan_consensus = ConsensusEngine(window_seconds=config.consensus_window_seconds)
                messages = await scraper.scrape_all_channels()
                return messages, parser, scan_consensus

            raw_scraped_messages, parser, active_consensus = asyncio.run(run_v2_scan())
            ui_consensus = active_consensus

            # --- YENİ: ARAYÜZ İÇİN MESAJ GEÇMİŞİ (HASH) FİLTRESİ ---
            seen_file = "seen_messages.json"
            seen_msgs = set()
            if os.path.exists(seen_file):
                try:
                    with open(seen_file, "r", encoding="utf-8") as f:
                        seen_msgs = set(json.load(f))
                except: pass

            scraped_messages = []
            for msg in raw_scraped_messages:
                # MD5 Parmak İzi Oluşturma
                msg_hash = hashlib.md5(f"{msg.channel_handle}_{msg.text.strip()}".encode("utf-8")).hexdigest()
                if msg_hash not in seen_msgs:
                    scraped_messages.append(msg)
                    seen_msgs.add(msg_hash)
            
            # Güncel parmak izlerini kaydet
            try:
                with open(seen_file, "w", encoding="utf-8") as f:
                    json.dump(list(seen_msgs), f)
            except: pass
            # --------------------------------------------------------

            if not scraped_messages:
                st.warning("📭 Yeni mesaj bulunamadı. Taranan tüm mesajlar daha önce işlenmiş.")
            else:
                st.success(f"Taramadan toplam {len(scraped_messages)} YENİ mesaj çekildi ve yapay zekaya gönderiliyor...")

                detected_pairs = set()
                parsed_results = []
                
                current_prices = {}
                for msg in scraped_messages:
                    passed, symbol, _ = parser._prefilter(msg.text)
                    if passed and symbol not in current_prices:
                        try:
                            ticker = trader.exchange.fetch_ticker(symbol)
                            current_prices[symbol] = float(ticker['last'])
                        except: pass

                with st.spinner("LLM Filtresi ve Konsensüs Motoru çalıştırılıyor..."):
                    parse_results = parser.parse_signals_batch([msg.text for msg in scraped_messages], current_prices=current_prices)

                for msg, parse_res in zip(scraped_messages, parse_results):
                    fake_handle = alias_map.get(msg.channel_handle, msg.channel_handle)

                    parsed_results.append({
                        "Kanal": f"@{fake_handle}",
                        "Ağırlık": msg.channel_weight,
                        "Mesaj": msg.text,
                        "Sinyal mi": parse_res.is_signal,
                        "Parite": parse_res.coin_pair,
                        "Orijinal Yön": parse_res.original_direction,
                        "Ters Yön": parse_res.reverse_direction,
                        "Açıklama": parse_res.reasoning
                    })

                    if parse_res.is_signal and parse_res.coin_pair and parse_res.original_direction:
                        ui_consensus.add_signal(BufferedSignal(
                            channel_id=msg.channel_id,
                            channel_handle=fake_handle,
                            channel_weight=msg.channel_weight,
                            coin_pair=parse_res.coin_pair,
                            original_direction=parse_res.original_direction,
                            reverse_direction=parse_res.reverse_direction,
                            tp=parse_res.tp,
                            sl=parse_res.sl
                        ))
                        detected_pairs.add(parse_res.coin_pair)

                st.markdown("### 📥 YENİ Çekilen Mesajlar ve AI Ayrıştırma Sonuçları")
                
                st.dataframe(
                    pd.DataFrame(parsed_results),
                    width="stretch",
                    column_config={
                        "Mesaj": st.column_config.TextColumn(
                            "Mesaj (Tamamını okumak için çift tıkla)",
                            width="large",
                            help="Metnin tamamını görmek için hücreye çift tıklayın."
                        )
                    }
                )

                st.markdown("### ⚖️ Konsensüs Motoru Kararları")
                if not detected_pairs:
                    st.info("Geçerli bir kripto sinyali tespit edilmedi veya mesajlar filtrelendi.")
                else:
                    for pair in detected_pairs:
                        decision = ui_consensus.evaluate_pair(pair)
                        if decision:
                            if decision.should_execute:
                                st.success(f"🎯 **{pair}** için KONSENSÜS SAĞLANDI! Çoğunluk: {decision.majority_original_direction} -> Kararımız: **{decision.decision_direction}**")
                                st.write(f"**Katılan Kanallar:** {', '.join(decision.participating_channels)}")
                                
                                katilan_kanallar_str = ",".join(decision.participating_channels)

                                with st.status(f"Binance Testnet üzerinde {pair} {decision.decision_direction} emri açılıyor...", expanded=True) as status:
                                    res = trader.execute_reverse_trade(
                                        symbol=pair,
                                        reverse_direction=decision.decision_direction,
                                        tp=decision.suggested_tp,
                                        sl=decision.suggested_sl,
                                        channel_handles=katilan_kanallar_str
                                    )
                                    if res.success:
                                        status.update(label=f"✅ Pozisyon Açıldı! Emir ID: {res.order_id}", state="complete")
                                    else:
                                        status.update(label=f"⚠️ {res.message}", state="error")
                            else:
                                st.warning(f"⚖️ **{pair}** için Karar: **NÖTR (İşlem Açılmadı)**. {decision.reasoning}")
    else:
        st.info("Sol menüdeki **'🚀 V2 Asenkron Taramayı Tetikle'** butonuna basarak tarama yapabilirsiniz.")

    st.divider()
    st.markdown("### 🧬 Kanal Ters İtibar Skorları (Dynamic Weights)")
    
    rep_file = "reputation.json"
    saved_reps = {}
    if os.path.exists(rep_file):
        try:
            with open(rep_file, "r") as f:
                saved_reps = json.load(f)
        except: pass
        
    rep_data = {}
    for c in active_channels:
        fake_handle = c.name.replace(" ", "")
        rep_data[fake_handle] = saved_reps.get(fake_handle, 1.0)
    
    df_rep = pd.DataFrame(list(rep_data.items()), columns=['Kanal', 'İtibar Skoru']).set_index('Kanal')
    
    col_chart, col_info = st.columns([3, 1])
    with col_chart:
        st.bar_chart(df_rep, color="#38bdf8")
    with col_info:
        st.info("💡 **Ters İtibar Skoru Nedir?**\n\nArka plandaki Asenkron Checker görevimiz 10 dakikada bir Binance'e bağlanarak kapanan kârlı işlemlerimizi kontrol eder. Bir kanal sinyallerinde ne kadar çok patlarsa, tersine motorumuzda o kanalın ağırlığı o kadar artar.")

with tab2:
    st.subheader("📡 Yapılandırılmış Telegram Kanalları (Gizli Liste)")
    channels_df = pd.DataFrame([
        {
            "ID": c.id,
            "Kanal Adı": c.name,
            "Handle": f"@{c.name.replace(' ', '')}",
            "Piyasa Türü": c.market_type,
            "Ağırlık Puanı": c.weight,
            "Aktiflik": "✅ Aktif" if c.active else "❌ Pasif",
            "URL": f"https://t.me/s/{c.name.replace(' ', '')}",
            "Notlar": c.notes
        }
        for c in all_channels
    ])
    display_dataframe(channels_df)

with tab3:
    st.subheader("📁 Geçmiş İşlem Arşivi ve Canlı PnL Raporu")

    col_t3_1, col_t3_2 = st.columns([4, 1])
    with col_t3_2:
        guncelle_tiklandi = display_button("🔄 PnL & Fiyat Güncelle", key="btn_pnl_refresh")

    if guncelle_tiklandi:
        with st.spinner("Binance verileri çekiliyor, PnL ve Ters İtibar Skorları güncelleniyor..."):
            try:
                excel_kar_zarar_guncelle(trader.exchange)
                
                from main import AutonomousBotV2
                temp_bot = AutonomousBotV2()
                temp_bot.check_and_update_reputation()
                
                st.success("PnL değerleri ve kanal itibar skorları güncellendi!")
            except Exception as e:
                st.error(f"Güncelleme hatası: {e}")

    if os.path.exists("islem_gecmisi.xlsx"):
        df_gecmis = pd.read_excel("islem_gecmisi.xlsx")
        display_dataframe(df_gecmis)
    else:
        st.info("Henüz kaydedilmiş bir işlem bulunmuyor.")