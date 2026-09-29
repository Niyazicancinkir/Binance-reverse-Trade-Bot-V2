import pandas as pd
import os
import logging
from datetime import datetime

logger = logging.getLogger("ExcelLogger")

def islemi_excel_kaydet(parite, yon, kaldirac, giris_fiyati, tp, sl, kanallar, miktar=0.0):
    """Yeni açılan işlemi Excel'e kaydeder."""
    file_name = "islem_gecmisi.xlsx"
    
    yeni_islem = {
        "Tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Parite": parite,
        "Kanal": kanallar,
        "Yön": yon,
        "Kaldıraç": float(kaldirac),
        "Miktar": float(miktar),
        "Giriş Fiyatı": float(giris_fiyati),
        "Hedef (TP)": float(tp) if tp else None,
        "Stop (SL)": float(sl) if sl else None,
        "Güncel Fiyat": float(giris_fiyati),
        "PnL (%)": 0.0,
        "Durum": "Açık"
    }

    try:
        if os.path.exists(file_name):
            df = pd.read_excel(file_name)
            df = pd.concat([df, pd.DataFrame([yeni_islem])], ignore_index=True)
        else:
            df = pd.DataFrame([yeni_islem])
        
        df.to_excel(file_name, index=False)
        logger.info(f"[EXCEL] Yeni işlem '{file_name}' dosyasına başarıyla işlendi (Kanal: {kanallar}).")
    except Exception as e:
        logger.error(f"[EXCEL_HATA] İşlem kaydedilirken hata oluştu: {e}")


def excel_kar_zarar_guncelle(exchange):
    """Açık olan işlemlerin PnL durumunu günceller."""
    file_name = "islem_gecmisi.xlsx"
    if not os.path.exists(file_name):
        return

    try:
        df = pd.read_excel(file_name)
        
        # --- KRİTİK DÜZELTME: Pandas int64 hatasını önlemek için sütunları float yapıyoruz ---
        if "PnL (%)" in df.columns:
            df["PnL (%)"] = pd.to_numeric(df["PnL (%)"], errors='coerce').astype(float)
        if "Güncel Fiyat" in df.columns:
            df["Güncel Fiyat"] = pd.to_numeric(df["Güncel Fiyat"], errors='coerce').astype(float)
        # -----------------------------------------------------------------------------------
        
        tickers = {}
        guncelleme_oldu = False

        for idx, row in df.iterrows():
            durum = str(row.get("Durum", ""))
            
            if durum == "Açık":
                parite = str(row.get("Parite", ""))
                yon = str(row.get("Yön", "")).upper()
                
                try:
                    giris_fiyati = float(row.get("Giriş Fiyatı", 0.0))
                    kaldirac = float(row.get("Kaldıraç", 10.0))
                except ValueError:
                    continue
                
                if parite not in tickers:
                    try:
                        ticker = exchange.fetch_ticker(parite)
                        tickers[parite] = float(ticker['last'])
                    except Exception as e:
                        logger.error(f"Fiyat çekilemedi {parite}: {e}")
                        continue
                        
                guncel_fiyat = tickers[parite]
                
                if yon == "LONG":
                    fiyat_farki_yuzde = (guncel_fiyat - giris_fiyati) / giris_fiyati
                elif yon == "SHORT":
                    fiyat_farki_yuzde = (giris_fiyati - guncel_fiyat) / giris_fiyati
                else:
                    fiyat_farki_yuzde = 0.0
                    
                pnl_yuzde = fiyat_farki_yuzde * kaldirac * 100
                
                df.at[idx, "Güncel Fiyat"] = guncel_fiyat
                df.at[idx, "PnL (%)"] = round(pnl_yuzde, 2)
                guncelleme_oldu = True

        if guncelleme_oldu:
            df.to_excel(file_name, index=False)
            logger.info("[EXCEL] PnL değerleri güncellendi (SHORT/LONG mantığı uygulandı).")
            
    except Exception as e:
        logger.error(f"[EXCEL_HATA] PnL güncellenirken hata oluştu: {e}")