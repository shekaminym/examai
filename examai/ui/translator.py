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
        "btn_select_screen": "ВИДІЛИТИ НА ЕКРАНІ",
        "lbl_question": "Питання",
        "lbl_answer": "Відповідь EXAM AI",
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
        "btn_select_screen": "SELECT ON SCREEN",
        "lbl_question": "Question",
        "lbl_answer": "EXAM AI answer",
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

# Strings shared by the settings panel, Dock and product page.
LABELS = {
    "ВІКНО ЗАМІРІВ": "TIMING WINDOW",
    "Розпізнавання непевне. Перевірте текст і натисніть «Надіслати питання».":
        "Recognition is uncertain. Review the draft and select Send question.",
    "Наступне питання в черзі…": "Next question queued…",
    "Не вдалося захопити екран. Спробуйте ще раз.": "Could not capture the screen. Please try again.",
    "Виділення скасовано.": "Selection canceled.",
    "Не вдалося отримати список аудіопристроїв.": "Could not list audio devices.",
    "Пристрій відтворення Windows за замовчуванням": "Default Windows playback device",
    "Мікрофон за замовчуванням": "Default Windows microphone",
    "Загальні": "General", "ШІ та моделі": "AI & models",
    "Аудіо та розпізнавання": "Audio & recognition", "Інтерфейс і теми": "Interface & themes",
    "Продуктивність": "Performance", "Гарячі клавіші": "Hotkeys", "Діагностика": "Diagnostics",
    "Закріпити праворуч": "Dock to the right", "Повернутися": "Back", "Зберегти": "Save",
    "Тема": "Theme", "Вибрати колір Custom Accent": "Choose Custom Accent color",
    "Прозорість вікна": "Window transparency", "Скинути": "Reset",
    "0% — непрозоре вікно. Вища прозорість зменшує контраст.": "0% is opaque. More transparency reduces contrast.",
    "Інтенсивність тонування": "Tint intensity", "Системне розмиття (якщо сумісне)": "System blur (when supported)",
    "Зменшити рух": "Reduce motion", "Інтенсивність анімацій": "Animation intensity",
    "Повна": "Full", "Помірна": "Subtle", "Вимкнено": "Off", "Закріплення": "Dock alignment",
    "Монітор": "Monitor", "Основний": "Primary", "Відновити вікно · Ctrl+Shift+F12": "Restore window · Ctrl+Shift+F12",
    "Системний звук (Loopback)": "System audio (Loopback)", "Мікрофон": "Microphone",
    "Точніше (small)": "Higher accuracy (small)", "Швидше (base)": "Faster (base)",
    "Налаштована модель": "Configured model", "Темп мовлення: Auto": "Speech pace: Auto",
    "Темп мовлення: звичайний": "Speech pace: normal", "Темп мовлення: повільний / з паузами": "Speech pace: slow / pauses",
    "Повне питання: довгі паузи": "Complete question: long pauses",
    "Зберігати питання та відповіді локально": "Save questions and answers locally",
    "Автоматично надсилати голосові питання": "Auto-send voice questions",
    "Виводити транскрипції в консоль": "Log transcripts to the console",
    "Може містити приватний текст. Діє до закриття програми. Текст виводиться локально у stderr.":
        "May contain private text. Applies until the app closes. Text is written locally to stderr.",
    "Ctrl+Alt+R · слухати / зупинити": "Ctrl+Alt+R · listen / stop",
    "Ctrl+Shift+F12 · повернути Dock": "Ctrl+Shift+F12 · restore Dock",
    "ШВИДКОДІЯ ІНТЕРФЕЙСУ:": "INTERFACE PERFORMANCE:",
    "Помилка збереження. Спробуйте ще раз.": "Could not save settings. Please try again.",
    "ІСТОРІЯ ВІДПОВІДЕЙ": "ANSWER HISTORY", "Питання без тексту": "Question without text",
    "Можливості": "Capabilities", "Розробники": "Developers", "Версія": "Version",
    "Подяки": "Acknowledgments", "Скопіювати версію": "Copy version",
    "Голосові питання, потокові відповіді FAST / SMART, Compact Dock з Live Cards, пошук у конспектах, аналіз виділеної області екрана.":
        "Voice questions, FAST / SMART streaming, Compact Dock with Live Cards, notes search, and screen-region analysis.",
    "EXAM AI — інтелектуальний настільний асистент для роботи з голосовими запитаннями, програмним кодом, документами та зображеннями. Поєднує розпізнавання мовлення, пошук за змістом і швидку генерацію відповідей у зручному інтерфейсі Windows.":
        "EXAM AI is a Windows desktop assistant for voice questions, code, documents and images. It combines speech recognition, content search and streaming answers.",
    "Дякуємо спільнотам відкритого ПЗ за інструменти, на яких працює EXAM AI.":
        "Thanks to the open-source communities behind the tools that power EXAM AI.",
    "Питання": "Question", "Відповідь EXAM AI": "EXAM AI answer",
    "Слухає…": "Listening…", "Перевірте текст": "Review transcript",
    "Готово": "Ready", "Помилка": "Error",
    "Надіслати": "Send", "Надіслати питання": "Send question", "Виправити": "Edit",
    "Виправити чернетку": "Edit draft", "Сховати картки": "Hide cards",
    "Попередня відповідь": "Previous answer", "Остання відповідь": "Latest answer",
    "Копіювати": "Copy", "Копіювати відповідь": "Copy answer", "Історія": "History",
    "Відкрити історію": "Open history", "Розгорнути відповідь": "Expand answer",
    "Згорнути відповідь": "Collapse answer", "Закріпити": "Pin", "Відкріпити": "Unpin",
    "Згорнути або розгорнути панель": "Expand or collapse panel",
    "EXAM AI слухає…": "EXAM AI is listening…", "EXAM AI аналізує…": "EXAM AI is processing…",
    "EXAM AI відповідає…": "EXAM AI is answering…", "Зупиняю…": "Stopping…",
    "Зупинено": "Stopped", "Ще розпізнаю питання…": "Still recognizing the question…",
    "Відповідь затримується…": "The answer is delayed…", "Помилка AI": "AI error",
    "Готовий": "Ready", "Додайте ключ Gemini у налаштуваннях.": "Add a Gemini key in Settings.",
    "Приховати з панелі завдань у Ghost Mode": "Hide from taskbar in Ghost Mode",
    "Також прибирає вікно з Alt+Tab. Повернення: Ctrl+Shift+F12.":
        "Also removes the window from Alt+Tab. Restore with Ctrl+Shift+F12.",
}
TRANSLATIONS["UA"]["save_error"] = "Помилка збереження. Спробуйте ще раз."
TRANSLATIONS["ENG"]["save_error"] = LABELS[TRANSLATIONS["UA"]["save_error"]]


class Translator:
    def __init__(self, default_lang="UA"):
        self.current_lang = default_lang

    def set_language(self, lang):
        if lang in TRANSLATIONS:
            self.current_lang = lang

    def get(self, key):
        return TRANSLATIONS.get(self.current_lang, TRANSLATIONS["UA"]).get(key, key)


    def local(self, text):
        return LABELS.get(text, text) if self.current_lang == "ENG" else text
