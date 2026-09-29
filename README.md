# Binance Reverse Trade Bot

Telegram kanallarındaki işlem sinyallerini analiz eden, yönü tersine çevirerek Binance demo futures hesabında işlem başlatabilen Python botu.

> ⚠️ **Uyarı:** Bu proje eğitim/deneysel amaçlıdır, yatırım tavsiyesi değildir. Kodu gerçek (mainnet) bir hesapta kullanmadan önce risklerini anlayın ve kendi sorumluluğunuzda test edin.

## Özellikler

- Telegram kanalından son sinyalleri okur.
- Gemini ile mesajın işlem sinyali olup olmadığını analiz eder.
- LONG sinyalini SHORT'a, SHORT sinyalini LONG'a çevirir.
- Birden fazla kanaldan gelen sinyaller için konsensüs (consensus) mekanizması uygular.
- Binance demo futures üzerinde market emri gönderebilir.
- İşlem kayıtlarını `islem_gecmisi.xlsx` dosyasına yazar ve PnL bilgisini günceller.
- Streamlit kontrol paneli sunar.

## Proje Yapısı

| Dosya | Açıklama |
|---|---|
| `app.py` | Streamlit kontrol paneli |
| `main.py` | Botun ana çalışma döngüsü |
| `scraper.py` | Telegram kanal mesajlarını çeken modül |
| `parser.py` / `gemini.py` | Gemini AI ile sinyal analizi |
| `consensus.py` | Çoklu kanal konsensüs mantığı |
| `trader.py` / `binance.py` | Binance emir/işlem yönetimi |
| `excel_logger.py` | İşlem geçmişi Excel kaydı |
| `config_loader.py` | `.env` ve `channels.json` yapılandırma yükleyici |
| `channels.json` | İzlenecek Telegram kanalları listesi |

## Kurulum

Python 3.10 veya üzeri önerilir.

```powershell
git clone https://github.com/Niyazicancinkir/Binance-reverse-Trade-Bot.git
cd Binance-reverse-Trade-Bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install ccxt google-generativeai pandas openpyxl requests beautifulsoup4 streamlit python-dotenv
```

## API anahtarları

`.env.example` dosyasını `.env` olarak kopyalayıp kendi anahtarlarınızla doldurun:

```powershell
Copy-Item .env.example .env
```

```env
BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET_KEY=your_binance_secret_key
GEMINI_API_KEY=your_gemini_api_key
```

`.env` dosyası `.gitignore` içinde tanımlıdır ve repoya asla eklenmemelidir. Gerçek anahtarlarınızı kimseyle paylaşmayın veya commit etmeyin.

## Çalıştırma

Streamlit kontrol paneli:

```powershell
streamlit run app.py
```

Terminal üzerinden çalıştırma:

```powershell
python main.py
```

Binance istemcisi demo trading modunda yapılandırılmıştır. Gerçek hesapta işlem açmadan önce kodu ve risk ayarlarını mutlaka test edin.

## Güvenlik

- API anahtarlarını, secret key'leri veya başka kimlik bilgilerini kaynak koduna yazmayın; yalnızca `.env` üzerinden ortam değişkeni olarak sağlayın.
- Binance API anahtarlarında mümkünse yalnızca gerekli işlem izinlerini açın ve para çekme (withdrawal) iznini kapalı tutun.
- Bu repoyu public yapmadan önce `.env`, `.idea/`, kişisel `islem_gecmisi.xlsx` gibi dosyaların commit geçmişinde yer almadığından emin olun.
- Daha önce herhangi bir ortamda paylaşılmış anahtarlar varsa iptal edip yenileriyle değiştirin.

## Lisans

Bu proje eğitim amaçlıdır; kullanım ve dağıtım koşulları için depo sahibiyle iletişime geçin.