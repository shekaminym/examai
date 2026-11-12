# Файл: translator.py

TRANSLATIONS = {
    "UA": {
        "title": "EXAM AI",
        "tab_ai": "AI",
        "tab_settings": "Налаштування",
        "tab_docs": "Конспекти",
        "btn_listen": "СЛУХАТИ",
        "btn_stop": "СТОП",
        "btn_copy": "КОПІЮВАТИ",
        "lbl_question": "ПИТАННЯ:",
        "lbl_answer": "ВІДПОВІДЬ AI:",
        "btn_clear_all": "Очистити все",
        "auto_send": "Автоматично надсилати з аудіо",
        "btn_send": "Надіслати питання",
        "status_ready": "Готовий",
        "privacy_on": "Захист вікна: УВІМК.",
        "privacy_off": "Захист вікна: ВИМК.",
        "toast_changed": "✓ Мову змінено на Українську",
        
        # Налаштування
        "set_gemini": "GEMINI API KEY:",
        "set_keys_hint": "Ключ Gemini діє до закриття програми.",
        "set_stt_plan": "Мова: Gemini Live → локальний Whisper при збої",
        "set_model": "МОДЕЛЬ AI:",
        "set_audio_source": "ДЖЕРЕЛО АУДІО:",
        "set_audio_device": "АУДІОПРИСТРІЙ:",
        "btn_save_settings": "ЗБЕРЕГТИ НАЛАШТУВАННЯ",
        "toast_saved": "✓ НАЛАШТУВАННЯ ЗБЕРЕЖЕНО",
        
        # Конспекти
        "btn_add_doc": "+ ДОДАТИ КОНСПЕКТ ДО ПРЕДМЕТА",
        "search_placeholder": "Введи термін для пошуку...",
        "btn_search": "ЗНАЙТИ",
        "btn_summary": "АВТОКОНСПЕКТ",
        "btn_del_doc": "✕ ВИДАЛИТИ ВИБРАНИЙ ФАЙЛ",
        "docs_placeholder": "Тут з'являться результати пошуку або автоконспект...",
        "lbl_subject": "ПРЕДМЕТ:"
    },
    "ENG": {
        "title": "EXAM AI",
        "tab_ai": "AI",
        "tab_settings": "Settings",
        "tab_docs": "Notes",
        "btn_listen": "LISTEN",
        "btn_stop": "STOP",
        "btn_copy": "COPY",
        "lbl_question": "QUESTION:",
        "lbl_answer": "AI RESPONSE:",
        "btn_clear_all": "Clear both",
        "auto_send": "Auto-send with audio",
        "btn_send": "Submit question",
        "status_ready": "Ready",
        "privacy_on": "Window Stealth: ON",
        "privacy_off": "Window Stealth: OFF",
        "toast_changed": "✓ Language changed to English",
        
        # Settings
        "set_gemini": "GEMINI API KEY:",
        "set_keys_hint": "The Gemini key lasts until app close.",
        "set_stt_plan": "Speech: Gemini Live → local Whisper on failure",
        "set_model": "AI MODEL:",
        "set_audio_source": "AUDIO SOURCE:",
        "set_audio_device": "AUDIO DEVICE:",
        "btn_save_settings": "SAVE SETTINGS",
        "toast_saved": "✓ SETTINGS SAVED",
        
        # Notes
        "btn_add_doc": "+ ADD NOTE TO SUBJECT",
        "search_placeholder": "Enter term to search...",
        "btn_search": "SEARCH",
        "btn_summary": "AUTO-SUMMARY",
        "btn_del_doc": "✕ DELETE SELECTED FILE",
        "docs_placeholder": "Search results or auto-summary will appear here...",
        "lbl_subject": "SUBJECT:"
    }
}

class Translator:
    def __init__(self, default_lang="UA"):
        self.current_lang = default_lang

    def set_language(self, lang):
        if lang in TRANSLATIONS:
            self.current_lang = lang

    def get(self, key):
        return TRANSLATIONS.get(self.current_lang, TRANSLATIONS["UA"]).get(key, key)
