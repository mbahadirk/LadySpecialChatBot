"""
LadySpecial ChatBot - Prompt Yöneticisi

LLM'e gönderilecek promptları dosyalardan okur ve yönetir.
Tüm promptlar /prompts klasöründe .txt dosyaları olarak tutulur.
"""

import os

# Prompt dosyalarının bulunduğu dizin
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")


class PromptManager:
    """LLM prompt dosyalarını okuyup yönetir."""

    # Prompt dosya isimleri
    SYSTEM_PROMPT = "system_prompt.txt"
    INTENT_CLASSIFICATION = "intent_classification_prompt.txt"
    PRODUCT_RESPONSE = "product_response_prompt.txt"
    ORDER_RESPONSE = "order_response_prompt.txt"
    GREETING_RESPONSE = "greeting_response_prompt.txt"

    _cache: dict[str, str] = {}

    @classmethod
    def load_prompt(cls, filename: str) -> str:
        """
        Prompt dosyasını okur. İlk okumadan sonra cache'e alır.

        Args:
            filename: Prompt dosya adı (örn: "system_prompt.txt")

        Returns:
            Prompt metni
        """
        if filename in cls._cache:
            return cls._cache[filename]

        filepath = os.path.join(PROMPTS_DIR, filename)

        if not os.path.exists(filepath):
            print(f"⚠️ Prompt dosyası bulunamadı: {filepath}")
            return ""

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            cls._cache[filename] = content
            return content
        except Exception as e:
            print(f"❌ Prompt dosyası okuma hatası ({filename}): {e}")
            return ""

    @classmethod
    def get_system_prompt(cls) -> str:
        """Ana sistem promptunu döndürür."""
        return cls.load_prompt(cls.SYSTEM_PROMPT)

    @classmethod
    def get_intent_classification_prompt(cls) -> str:
        """Intent sınıflandırma promptunu döndürür."""
        return cls.load_prompt(cls.INTENT_CLASSIFICATION)

    @classmethod
    def get_product_response_prompt(cls) -> str:
        """Ürün yanıt promptunu döndürür."""
        return cls.load_prompt(cls.PRODUCT_RESPONSE)

    @classmethod
    def get_order_response_prompt(cls) -> str:
        """Sipariş yönlendirme promptunu döndürür."""
        return cls.load_prompt(cls.ORDER_RESPONSE)

    @classmethod
    def get_greeting_response_prompt(cls) -> str:
        """Selamlama yanıt promptunu döndürür."""
        return cls.load_prompt(cls.GREETING_RESPONSE)

    @classmethod
    def clear_cache(cls):
        """Prompt cache'ini temizler. Dosya güncellemelerinden sonra kullanılır."""
        cls._cache.clear()
        print("🔄 Prompt cache temizlendi.")

    @classmethod
    def list_prompts(cls) -> list[str]:
        """Mevcut prompt dosyalarını listeler."""
        if not os.path.exists(PROMPTS_DIR):
            return []
        return [f for f in os.listdir(PROMPTS_DIR) if f.endswith('.txt')]
