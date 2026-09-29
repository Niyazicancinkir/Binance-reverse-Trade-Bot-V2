import asyncio
import random
import time
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Set
import aiohttp
from bs4 import BeautifulSoup

from config_loader import ChannelConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Scraper")

@dataclass
class ScrapedMessage:
    channel_id: int
    channel_handle: str
    channel_weight: float
    message_id: str
    text: str
    scraped_at: float

class TelegramAsyncScraper:
    def __init__(
        self,
        channels: List[ChannelConfig],
        jitter_min: float = 1.0,
        jitter_max: float = 3.0,
        max_retries: int = 3
    ):
        self.channels = channels
        self.jitter_min = jitter_min
        self.jitter_max = jitter_max
        self.max_retries = max_retries
        self.seen_messages: Dict[str, Set[str]] = {c.handle: set() for c in channels}
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,tr;q=0.8"
        }

    async def _fetch_channel_html(
        self, session: aiohttp.ClientSession, channel: ChannelConfig
    ) -> Optional[str]:
        """
        Kanal HTML sayfasını jitter ve backoff ile çeker.
        """
        url = channel.url
        retry_delay = 2.0
        
        # LOG İÇİN TAKMA AD (ALIAS) OLUŞTURULDU
        takma_ad = channel.name.replace(" ", "")

        for attempt in range(1, self.max_retries + 1):
            try:
                # Jitter: İstek öncesi rastgele bekleme
                await asyncio.sleep(random.uniform(self.jitter_min, self.jitter_max))

                async with session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        return await response.text()
                    elif response.status in (429, 503):
                        logger.warning(
                            f"[SCRAPER_WARN] @{takma_ad} rate-limit alındı (HTTP {response.status}). "
                            f"Deneme {attempt}/{self.max_retries}. {retry_delay}s bekleniyor..."
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        logger.error(
                            f"[SCRAPER_ERROR] @{takma_ad} HTTP hatası: {response.status}"
                        )
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    f"[SCRAPER_WARN] @{takma_ad} bağlantı hatası (Deneme {attempt}/{self.max_retries}): {e}"
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2

        logger.error(f"[SCRAPER_FAILED] @{takma_ad} {self.max_retries} deneme sonrası başarısız.")
        return None

    def _parse_messages(self, channel: ChannelConfig, html_content: str) -> List[ScrapedMessage]:
       
        soup = BeautifulSoup(html_content, "html.parser")
        msg_boxes = soup.find_all("div", class_="tgme_widget_message")

        new_messages: List[ScrapedMessage] = []

        for box in msg_boxes:
            data_post = box.get("data-post", "")
            msg_id = data_post.split("/")[-1] if "/" in data_post else str(hash(box.text))

            text_div = box.find("div", class_="tgme_widget_message_text")
            if not text_div:
                continue

            clean_text = text_div.get_text(separator=" ", strip=True)
            if not clean_text:
                continue

            if channel.handle in self.seen_messages and msg_id in self.seen_messages[channel.handle]:
                continue

            if channel.handle not in self.seen_messages:
                self.seen_messages[channel.handle] = set()

            self.seen_messages[channel.handle].add(msg_id)

            scraped_msg = ScrapedMessage(
                channel_id=channel.id,
                channel_handle=channel.handle,
                channel_weight=channel.weight,
                message_id=msg_id,
                text=clean_text,
                scraped_at=time.time()
            )
            new_messages.append(scraped_msg)

        return new_messages

    async def scrape_channel(
        self, session: aiohttp.ClientSession, channel: ChannelConfig
    ) -> List[ScrapedMessage]:
        """
        Tek bir kanalı asenkron tarar.
        """
        if not channel.active:
            return []

        html = await self._fetch_channel_html(session, channel)
        if not html:
            return []
            
        # LOG İÇİN TAKMA AD (ALIAS) OLUŞTURULDU
        takma_ad = channel.name.replace(" ", "")

        messages = self._parse_messages(channel, html)
        if messages:
            # ORİJİNAL HANDLE YERİNE TAKMA AD KULLANILIYOR
            logger.info(f"[SCRAPER] @{takma_ad}: {len(messages)} yeni mesaj yakalandı.")
        return messages

    async def scrape_all_channels(self) -> List[ScrapedMessage]:
        """
        Tüm aktif kanalları asenkron ve paralel olarak tarar.
        """
        active_channels = [c for c in self.channels if c.active]
        if not active_channels:
            logger.info("[SCRAPER] Aktif kanal bulunamadı.")
            return []

        async with aiohttp.ClientSession() as session:
            tasks = [self.scrape_channel(session, channel) for channel in active_channels]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_new_messages: List[ScrapedMessage] = []
        for res in results:
            if isinstance(res, list):
                all_new_messages.extend(res)
            elif isinstance(res, Exception):
                logger.error(f"[SCRAPER_EXC] Kanal tarama sırasında istisna oluştu: {res}")

        return all_new_messages


if __name__ == "__main__":
    from config_loader import get_active_channels

    async def main_test():
        active = get_active_channels()
        scraper = TelegramAsyncScraper(active)
        messages = await scraper.scrape_all_channels()
        print(f"Toplam Çekilen Yeni Mesaj Sayısı: {len(messages)}")
        for m in messages[:5]:
            # Test bloğunda da anonim isim gösterilmesi için
            kanal_adi = next((c.name.replace(" ", "") for c in active if c.handle == m.channel_handle), m.channel_handle)
            print(f"- [@{kanal_adi}] {m.text[:60]}...")

    asyncio.run(main_test())