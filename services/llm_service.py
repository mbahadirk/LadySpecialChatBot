"""
LadySpecial ChatBot - LLM Servisi

OpenAI API ile iletişim kuran servis.
Intent sınıflandırma ve cevap üretme sorumluluklarını taşır.
"""

import os
from openai import OpenAI
from dotenv import load_dotenv

from services.prompt_manager import PromptManager

load_dotenv()


class LLMService:
    """OpenAI GPT ile intent sınıflandırma ve cevap üretme servisi."""

    def __init__(self, model: str = "gpt-4o-mini"):
        """
        Args:
            model: Kullanılacak OpenAI modeli.
                   - "gpt-4o-mini" → Hızlı ve ucuz (intent, selamlama, genel sohbet)
                   - "gpt-4o"      → Ürün sorularında daha iyi sonuç
        """
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.model = model

    def classify_intent(self, user_message: str) -> str:
        """
        Müşterinin mesajının intent'ini (niyetini) belirler.

        Returns:
            "product_inquiry" | "order_request" | "greeting" | "complaint" | "general_chat"
        """
        classification_prompt = PromptManager.get_intent_classification_prompt()

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # Intent sınıflandırma için mini yeterli
                messages=[
                    {"role": "system", "content": classification_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=20,
                temperature=0.0  # Deterministik çıktı
            )
            intent = response.choices[0].message.content.strip().lower()

            # Bilinen intent'lerden biri mi kontrol et
            valid_intents = [
                "product_inquiry", "order_request",
                "greeting", "complaint", "general_chat"
            ]
            if intent not in valid_intents:
                print(f"⚠️ Bilinmeyen intent: '{intent}' → general_chat olarak ayarlandı")
                return "general_chat"

            print(f"🎯 Intent: {intent}")
            return intent

        except Exception as e:
            print(f"❌ Intent sınıflandırma hatası: {e}")
            return "general_chat"

    def generate_product_response(
        self,
        user_message: str,
        product_data: list[dict],
        conversation_history: list[dict]
    ) -> str:
        """
        Ürün bilgileri ile müşteriye cevap üretir.

        Args:
            user_message: Müşterinin mesajı
            product_data: Bulunan ürün bilgileri listesi
            conversation_history: Önceki mesajlar (LLM context)

        Returns:
            LLM'in ürettiği cevap metni
        """
        system_prompt = PromptManager.get_system_prompt()
        product_prompt = PromptManager.get_product_response_prompt()

        # Ürün bilgilerini metin formatına çevir
        product_info_text = self._format_products_for_llm(product_data)

        messages = [
            {"role": "system", "content": f"{system_prompt}\n\n{product_prompt}"}
        ]

        # Geçmiş mesajları ekle
        messages.extend(conversation_history)

        # Kullanıcının mesajını ve ürün bilgilerini ekle
        messages.append({
            "role": "user",
            "content": f"Müşteri mesajı: {user_message}\n\nBulunan ürün bilgileri:\n{product_info_text}"
        })

        return self._call_llm(messages)

    def generate_order_response(
        self,
        user_message: str,
        product_data: list[dict],
        conversation_history: list[dict]
    ) -> str:
        """
        Sipariş isteği için web sitesi yönlendirmeli cevap üretir.
        """
        system_prompt = PromptManager.get_system_prompt()
        order_prompt = PromptManager.get_order_response_prompt()

        product_info_text = self._format_products_for_llm(product_data)

        messages = [
            {"role": "system", "content": f"{system_prompt}\n\n{order_prompt}"}
        ]
        messages.extend(conversation_history)
        messages.append({
            "role": "user",
            "content": f"Müşteri mesajı: {user_message}\n\nİlgili ürün bilgileri:\n{product_info_text}"
        })

        return self._call_llm(messages)

    def generate_greeting_response(
        self,
        user_message: str,
        is_returning: bool,
        conversation_history: list[dict]
    ) -> str:
        """
        Selamlama cevabı üretir.

        Args:
            is_returning: Kullanıcı daha önce mesaj atmış mı?
        """
        system_prompt = PromptManager.get_system_prompt()
        greeting_prompt = PromptManager.get_greeting_response_prompt()

        context_note = "Bu müşteri daha önce bizimle konuşmuş, tanıdık birisi." if is_returning else "Bu müşteri ilk kez yazıyor."

        messages = [
            {"role": "system", "content": f"{system_prompt}\n\n{greeting_prompt}\n\nNot: {context_note}"}
        ]
        messages.extend(conversation_history[-4:])  # Son birkaç mesaj yeterli
        messages.append({"role": "user", "content": user_message})

        return self._call_llm(messages, model="gpt-4o-mini")

    def generate_general_response(
        self,
        user_message: str,
        conversation_history: list[dict]
    ) -> str:
        """
        Genel sohbet ve şikayet cevabı üretir.
        """
        system_prompt = PromptManager.get_system_prompt()

        messages = [
            {"role": "system", "content": system_prompt}
        ]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_message})

        return self._call_llm(messages)

    def extract_product_query(self, user_message: str, conversation_history: list[dict] = None) -> str:
        """
        Mesajdan ürün arama sorgusunu çıkarır.
        Eğer kullanıcı "bu ürün stokta var mı?" gibi geçmişe atıfta bulunursa,
        geçmiş konuşmayı kullanarak doğru ürün adını çıkarır.
        """
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Kullanıcının mesajından ürün arama sorgusunu çıkar. "
                        "Sadece ürün adını veya tanımını döndür, başka bir şey yazma (örn: 'Kırmızı elbise var mı?' -> 'kırmızı elbise'). "
                        "Eğer kullanıcı 'bu ürün', 'bunun fiyatı' gibi kelimeler kullanıyorsa önceki konuşmalara bakarak "
                        "hangi üründen bahsettiğini anla ve o ürünün tam adını döndür. "
                        "ÖNEMLİ: Eğer kullanıcı 'bundan bahsetmiyorum', 'bu değil', 'bunu sormadım' diyorsa VEYA "
                        "hiçbir ürün adı tespit edilemiyorsa, SADECE 'BOŞ' kelimesini döndür. Asla uydurma bir ürün adı döndürme."
                    )
                }
            ]
            if conversation_history:
                messages.extend(conversation_history[-4:])
            messages.append({"role": "user", "content": user_message})

            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=30,
                temperature=0.0
            )
            query = response.choices[0].message.content.strip()
            if query == "BOŞ" or query == "":
                print("[LLM] Sorgu cikarilamadi (BOS).")
                return ""
            print(f"[LLM] Cikarilan arama sorgusu: '{query}'")
            return query

        except Exception as e:
            print(f"[LLM] Sorgu cikarma hatasi: {e}")
            return user_message  # Fallback: mesajın kendisini kullan

    # ──────────────────────────────────────
    #  Görsel Yanıt Metotları
    # ──────────────────────────────────────

    def generate_image_response(
        self,
        user_message: str,
        search_results: list[dict],
        conversation_history: list[dict]
    ) -> str:
        """
        Görsel + metin birlikte geldiğinde cevap üretir.
        Görsel arama sonuçlarını ve müşterinin mesajını birlikte değerlendirir.
        """
        system_prompt = PromptManager.get_system_prompt()
        image_prompt = PromptManager.load_prompt("image_response_prompt.txt")

        results_text = self._format_image_results_for_llm(search_results)

        messages = [
            {"role": "system", "content": f"{system_prompt}\n\n{image_prompt}"}
        ]
        messages.extend(conversation_history)
        messages.append({
            "role": "user",
            "content": (
                f"Musteri bir urun gorseli gonderdi ve su mesaji yazdi: \"{user_message}\"\n\n"
                f"{results_text}"
            )
        })

        return self._call_llm(messages)

    def generate_image_search_response(
        self,
        search_results: list[dict],
        conversation_history: list[dict]
    ) -> str:
        """
        Sadece görsel gönderildiğinde (60 saniye mesaj gelmeyince) otomatik cevap üretir.
        """
        system_prompt = PromptManager.get_system_prompt()
        image_prompt = PromptManager.load_prompt("image_response_prompt.txt")

        results_text = self._format_image_results_for_llm(search_results)

        messages = [
            {"role": "system", "content": f"{system_prompt}\n\n{image_prompt}"}
        ]
        messages.extend(conversation_history)
        messages.append({
            "role": "user",
            "content": (
                f"Musteri sadece bir urun gorseli gonderdi, mesaj yazmadi.\n\n"
                f"{results_text}\n\n"
                f"Gorsele benzer urunleri goster. Stokta olanlarin linkini paylas. "
                f"Stokta olmayanlarin adini soyle ve benzer alternatifleri oner."
            )
        })

        return self._call_llm(messages)

    def _format_image_results_for_llm(self, results: list[dict]) -> str:
        """Görsel arama sonuçlarını LLM formatına çevirir."""
        if not results:
            return "Gorsel Arama Sonucu: Hicbir benzer urun bulunamadi."

        # %80 (0.80) veya üzeri benzerlik varsa SADECE İLK ÜRÜNÜ GÖSTER
        if results and results[0].get("score", 0) >= 0.80:
            results = [results[0]]
            high_confidence = True
        else:
            # Emin değilse sadece ilk 3 ürünü göster
            results = results[:3]
            high_confidence = False

        lines = ["Gorsel Arama Sonuclari:"]
        
        # LLM'e davranışını belirten gizli not ekle
        if high_confidence:
             lines.append("[SISTEM NOTU: 1 ürün kesin olarak eşleşti. Müşteriye tam olarak o ürünle ilgili bilgileri ver.]")
        else:
             lines.append("[SISTEM NOTU: Tam eşleşen bir ürün bulunamadı. Müşteriye 'Görseldekini tam bulamadım ama şunlardan biri olabilir mi?' diyerek aşağıdaki 3 alternatifi sun.]")

        for p in results:
            stock_count = p.get('stock', 0)
            if p.get("in_stock"):
                 stock_status = "STOKTA VAR" if stock_count > 10 else f"STOKTA ({stock_count} adet)"
            else:
                 stock_status = "TUKENMIS"
            line = f"{p['name']} -- {p.get('price', 0)} TL -- {stock_status}"
            if stock_count > 0 and p.get("url"):
                line += f" -- URL: {p['url']}"
            lines.append(line)

        return "\n".join(lines)

    def _format_products_for_llm(self, products: list[dict]) -> str:
        """Ürün listesini LLM'in okuyabileceği metin formatına çevirir."""
        if not products:
            return "Hiçbir ürün bulunamadı."

        lines = []
        for p in products:
            stock_count = p.get('stock', 0)
            if stock_count > 10:
                stock_status = "Stokta var"
            elif stock_count > 0:
                stock_status = f"Stok: {stock_count} adet"
            else:
                stock_status = "Stokta yok"
            variants_info = ""
            if p.get("in_stock_variants"):
                variant_texts = [v.get("option1", "") for v in p["in_stock_variants"][:5]]
                variants_info = f" | Seçenekler: {', '.join(variant_texts)}"

            url_line = f"\n   URL: {p.get('url', 'Yok')}" if stock_count > 0 else ""

            lines.append(
                f"{p['name']} — {p.get('price', 0)} {p.get('currency', 'TL')} — "
                f"{stock_status}{variants_info}{url_line}"
            )

        return "\n".join(lines)

    def _call_llm(self, messages: list[dict], model: str = None) -> str:
        """OpenAI API'ye istek gönderir."""
        try:
            response = self.client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                max_tokens=500,
                temperature=0.7
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"[LLM] Hata: {e}")
            return "Bir aksaklik yasandi, lutfen biraz sonra tekrar deneyin."

