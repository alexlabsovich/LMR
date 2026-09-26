"""
LMR Hub — Telegram-бот для генерации контента и ресерча.
Работает на pyTelegramBotAPI + OpenRouter (nvidia/nemotron-3-ultra-550b-a55b:free)

Установка зависимостей:
    pip install pyTelegramBotAPI requests

Запуск:
    python bot.py
"""

import re
import logging
import requests
import telebot
from telebot import types

logging.basicConfig(level=logging.INFO)

# ==================== НАСТРОЙКИ ====================
TELEGRAM_TOKEN = "ВАШ_ТЕЛЕГРАМ_ТОКЕН"
OPENROUTER_API_KEY = "ВАШ_OPENROUTER_КЛЮЧ"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Опционально, но рекомендуется OpenRouter'ом для рейтингов/статистики приложения
SITE_URL = "https://example.com"
SITE_NAME = "LMR Hub Bot"
# =====================================================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# user_id -> состояние диалога
user_states = {}


def get_state(user_id):
    if user_id not in user_states:
        user_states[user_id] = {
            "mode": None,       # "generate" | "research"
            "stage": None,      # "awaiting_input" | "awaiting_revision" | "awaiting_clarify" | "review"
            "last_content": "", # последний сгенерированный текст / исследование
            "style_block": "",  # сохранённые жёсткие правила стиля (пункт 3), актуальны до /start
        }
    return user_states[user_id]


def main_menu_keyboard():
    # Постоянная (не one_time) клавиатура — остаётся на экране после /start
    # и не пропадает, пока сам бот не пришлёт ReplyKeyboardRemove.
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.add(types.KeyboardButton("Generate"), types.KeyboardButton("Research"))
    return kb


def review_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✔️", callback_data="confirm"),
        types.InlineKeyboardButton("🔄", callback_data="revise"),
    )
    return kb


# ==================== ЗАПРОСЫ К OPENROUTER ====================
def call_openrouter(messages, use_web_search=False):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": SITE_URL,
        "X-Title": SITE_NAME,
    }
    payload = {
        "model": MODEL,
        "messages": messages,
    }
    if use_web_search:
        # Встроенный плагин веб-поиска OpenRouter (поиск + вставка источников в контекст)
        payload["plugins"] = [{"id": "web"}]

    resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


HUMANIZE_RULES = """

ПРАВИЛА "ЖИВОГО" ТЕКСТА (обязательны для любого стиля и жанра):
- Пиши так, будто текст написал живой человек, а не ИИ. Стиль (сленговый, деловой,
  ироничный, сухой аналитический — какой угодно) выбирает пользователь или задаёт пример,
  но исполнение всегда должно быть естественным, а не "отглаженным ИИ".
- Не используй типичные ИИ-клише и вводные конструкции: "В современном мире...",
  "Стоит отметить, что...", "Играет важную роль", "Подводя итог", "Таким образом",
  "Это неудивительно, ведь...", "Погрузимся в...", "Давайте разберёмся".
- Не начинай ответ с "Конечно!", "Вот...", "Отлично!", "Разумеется" и подобных
  вступительных фраз-паразитов — сразу переходи к сути.
- Избегай механической симметрии: не строй все предложения по одной и той же длине и
  структуре ("Первое... Второе... Третье..."), не дроби мысль на списки там, где
  человек написал бы обычным связным текстом.
- Не злоупотребляй длинным тире (—) как универсальной ИИ-связкой между любыми фразами;
  используй его только там, где это оправдано по смыслу и стилю.
- Не добавляй дежурные оговорки и дисклеймеры ("не является финансовым советом",
  "проконсультируйтесь со специалистом" и т.п.), если пользователь их не просил.
- Сохраняй естественные несовершенства живой речи там, где это уместно по стилю:
  разговорные обороты, сокращения, эмоциональность, если так написан пример или об
  этом просит пользователь — не "причёсывай" текст до неестественной гладкости.
- Если стиль/пример писался коряво, с сокращениями или сленгом — не превращай его в
  правильный литературный текст: сохраняй именно такую манеру письма."""


GENERATE_SYSTEM_PROMPT = """Ты — ассистент по созданию контента (постов) для соцсетей.
Пользователь присылает данные для генерации поста. В тексте могут встречаться
примечания в виде цифр в начале строки:
1 — пример поста, на который нужно ориентироваться;
2 — нужно ли проводить дополнительный углублённый ресерч в интернете;
3 — нужно скопировать стиль примера или создать свой уникальный стиль;
4 — нужно ли искать в сети похожие посты и использовать их при генерации.

Учитывай эти примечания, если они присутствуют в сообщении. Если пользователь просил ресерч
(пункт 2) или поиск похожих постов (пункт 4), используй результаты веб-поиска, если они тебе
доступны в контексте, и опирайся на них. В финале выдай только готовый, хорошо оформленный
пост — без лишних пояснений от себя.""" + HUMANIZE_RULES

RESEARCH_SYSTEM_PROMPT = """Ты — ассистент для глубокого исследования (research), работающий на
уровне продвинутого ассистента с доступом к интернету. Твоя задача:
1. Искать и анализировать информацию по всей сети;
2. Проводить углублённый анализ, а не поверхностный пересказ;
3. Изучать документацию, официальные источники и первичные материалы;
4. Обязательно прикладывать в ответе релевантные ссылки на источники.

ФОРМАТ ОТВЕТА (строго обязателен, ответ идёт в мессенджер, а не в документ):
- Пиши только простым текстом. НИКАКОГО markdown: без **жирного**, без ## заголовков,
  без таблиц (| ... |), без блок-цитат (>), без тройных кавычек ```.
  Не выделяй слова звёздочками или решётками вообще.
- Это сжатая контекстная выжимка по существу: только ключевые факты, цифры и выводы,
  без вступлений, без повторов и без "воды".
- Не вставляй ссылки внутри текста и не оформляй markdown-гиперссылки вида [текст](url).
  Все ссылки собери в один список в самом конце сообщения под заголовком "Ссылки:",
  каждая ссылка — с новой строки, просто URL (при необходимости — короткая пометка,
  на что ссылка, через тире).
- Если для ответа нужны разные пункты — используй простую нумерацию "1)", "2)" или
  тире "-", без вложенных списков и лишней иерархии.""" + HUMANIZE_RULES


NOTE_LINE_RE = re.compile(r'^\s*([1-4])[\.\)]?\s*(.*)$')


def parse_notes(text):
    """
    Разбирает пункты 1-4 в тексте пользователя. Строка, начинающаяся с цифры 1-4
    (с точкой/скобкой или без), открывает новый пункт; все последующие строки
    до следующей такой строки относятся к текущему пункту (это позволяет пункту 1
    занимать несколько строк, как в примере поста).
    Возвращает dict {"1": "...", "2": "...", "3": "...", "4": "..."} — только
    те пункты, что реально встретились.
    """
    notes = {}
    current = None
    for line in text.splitlines():
        m = NOTE_LINE_RE.match(line)
        if m:
            current = m.group(1)
            notes[current] = m.group(2)
        elif current:
            notes[current] = (notes[current] + "\n" + line).strip("\n")
    return {k: v.strip() for k, v in notes.items()}


def build_style_block(notes):
    """
    Если пользователь просил скопировать стиль примера (пункт 3), формирует
    жёсткий блок инструкций с целевой длиной и запретом на добавление
    форматирования/структуры, которых нет в примере (пункт 1).
    Если пункт 3 содержит другое пожелание по стилю — просто прокидывает его.
    Возвращает "" если пункт 3 не указан.
    """
    note1 = notes.get("1", "").strip()
    note3 = notes.get("3", "").strip()
    if not note3:
        return ""

    copy_markers = ("копир", "скопир", "как в примере", "такой же", "тот же стиль")
    wants_copy = any(marker in note3.lower() for marker in copy_markers)

    if wants_copy and note1:
        word_count = len(note1.split())
        char_count = len(note1)
        return (
            "\n\n=== СТРОГИЕ ПРАВИЛА КОПИРОВАНИЯ СТИЛЯ (обязательны, без исключений) ===\n"
            f"- Целевая длина итогового поста: примерно {word_count} слов "
            f"(~{char_count} символов). Отклонение не более 15-20%. Это жёсткое ограничение, "
            "а не пожелание — не пиши длиннее просто потому что 'так понятнее'.\n"
            "- ЗАПРЕЩЕНО добавлять то, чего нет в примере: заголовки, подзаголовки, "
            "жирный текст (**...**), маркированные списки, эмодзи-иконки перед абзацами "
            "(🚀, 💡, 🎯 и т.п.), разделы вида 'Что изменилось:' — если в примере этого нет, "
            "в результате тоже быть не должно.\n"
            "- Скопируй саму структуру примера: длину предложений, разговорный/сленговый "
            "тон, пунктуацию, расстановку переносов строк, использование заглавных букв, "
            "хэштеги (если есть — в конце, в том же количестве и формате).\n"
            "- Если сомневаешься, добавлять ли элемент оформления — не добавляй.\n\n"
            f"ПРИМЕР, СТИЛЬ И РАЗМЕР КОТОРОГО НУЖНО СКОПИРОВАТЬ ДОСЛОВНО:\n{note1}"
        )

    return f"\n\nПожелание пользователя по стилю (пункт 3): {note3}"


def generate_post(user_text, use_search):
    notes = parse_notes(user_text)
    style_block = build_style_block(notes)
    full_user_content = user_text + style_block
    messages = [
        {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
        {"role": "user", "content": full_user_content},
    ]
    content = call_openrouter(messages, use_web_search=use_search)
    return content, style_block


def revise_post(original_text, revision_request, style_block=""):
    messages = [
        {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
        {"role": "assistant", "content": original_text},
        {
            "role": "user",
            "content": (
                f"Внеси следующие правки в пост:\n{revision_request}"
                + (f"\n\nНапоминание — эти правила всё ещё действуют:{style_block}" if style_block else "")
            ),
        },
    ]
    return call_openrouter(messages, use_web_search=False)


TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')
TABLE_SEP_RE = re.compile(r'^\s*\|?[\s\-:|]+\|\s*$')
MD_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')


def clean_markdown_noise(text):
    """
    Подчищает ответ модели от markdown-разметки на случай, если бесплатная модель
    проигнорировала текстовый формат из системного промпта: убирает **жирный**,
    ## заголовки, > цитаты, ```код```, схлопывает markdown-таблицы в простые строки
    и превращает [текст](url) в "текст: url".
    """
    if not text:
        return text

    text = re.sub(r'```.*?```', '', text, flags=re.S)   # код-блоки целиком
    text = re.sub(r'(?m)^#{1,6}\s*', '', text)           # ## Заголовки
    text = text.replace('**', '').replace('__', '')      # жирный/подчёркнутый
    text = re.sub(r'(?m)^>\s?', '', text)                # > цитаты
    text = MD_LINK_RE.sub(r'\1: \2', text)                # [текст](url) -> текст: url

    cleaned_lines = []
    for line in text.splitlines():
        if TABLE_SEP_RE.match(line):
            continue  # разделитель таблицы |---|---|
        if TABLE_ROW_RE.match(line):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            cleaned_lines.append(' — '.join(c for c in cells if c))
        else:
            cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)

    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def research_query(user_text):
    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    result = call_openrouter(messages, use_web_search=True)
    return clean_markdown_noise(result)


def research_followup(original_text, clarification):
    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "assistant", "content": original_text},
        {"role": "user", "content": f"Уточни и дополни исследование с учётом:\n{clarification}"},
    ]
    result = call_openrouter(messages, use_web_search=True)
    return clean_markdown_noise(result)


def wants_search(user_text):
    """Проверяем, отметил ли пользователь пункт 2 или 4 (ресерч / похожие посты)."""
    for line in user_text.splitlines():
        if re.match(r"\s*[24]\b", line):
            return True
    return False


def safe_send(chat_id, text, **kwargs):
    """Telegram режет сообщения на 4096 символов — режем ответ модели на части."""
    if not text:
        text = "(пустой ответ от модели)"
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [text]
    for i, chunk in enumerate(chunks):
        if i == len(chunks) - 1:
            bot.send_message(chat_id, chunk, **kwargs)
        else:
            bot.send_message(chat_id, chunk)


# ==================== ХЕНДЛЕРЫ ====================
@bot.message_handler(commands=["start"])
def handle_start(message):
    state = get_state(message.from_user.id)
    state["mode"] = None
    state["stage"] = None
    state["last_content"] = ""
    state["style_block"] = ""
    bot.send_message(
        message.chat.id,
        "Добро пожаловать в LMR Hub! Выберите задачу:",
        reply_markup=main_menu_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "Generate")
def handle_generate_button(message):
    state = get_state(message.from_user.id)
    state["mode"] = "generate"
    state["stage"] = "awaiting_input"
    bot.send_message(
        message.chat.id,
        "Пришлите данные для генерации. Также укажите дополнительные критерии:\n\n"
        "1. Пример поста.\n"
        "2. Нужен ли углубленный поиск данных.\n"
        "3. Нужно ли копировать стиль примера / лучше создать новый (укажите стиль).\n"
        "4. Нужно ли искать в сети похожие посты и использовать их в генерации поста.",
        reply_markup=main_menu_keyboard(),
    )


@bot.message_handler(func=lambda m: m.text == "Research")
def handle_research_button(message):
    state = get_state(message.from_user.id)
    state["mode"] = "research"
    state["stage"] = "awaiting_input"
    bot.send_message(
        message.chat.id,
        "Я готов к углублённому изучению, опишите ваш запрос.",
        reply_markup=main_menu_keyboard(),
    )


@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(message):
    state = get_state(message.from_user.id)
    chat_id = message.chat.id
    text = message.text

    try:
        if state["mode"] == "generate" and state["stage"] in ("awaiting_input", "awaiting_revision"):
            bot.send_chat_action(chat_id, "typing")
            if state["stage"] == "awaiting_input":
                result, style_block = generate_post(text, use_search=wants_search(text))
                state["style_block"] = style_block
            else:
                result = revise_post(state["last_content"], text, style_block=state.get("style_block", ""))

            state["last_content"] = result
            state["stage"] = "review"
            safe_send(chat_id, result, reply_markup=review_keyboard())
            return

        if state["mode"] == "research" and state["stage"] in ("awaiting_input", "awaiting_clarify"):
            bot.send_chat_action(chat_id, "typing")
            if state["stage"] == "awaiting_input":
                result = research_query(text)
            else:
                result = research_followup(state["last_content"], text)

            state["last_content"] = result
            state["stage"] = "review"
            safe_send(chat_id, result, reply_markup=review_keyboard())
            return
    except requests.RequestException as e:
        logging.exception("OpenRouter request failed")
        bot.send_message(chat_id, f"Ошибка обращения к OpenRouter: {e}")
        return

    # если бот не ждёт ввода в этом состоянии
    bot.send_message(chat_id, "Пожалуйста, начните с команды /start и выберите задачу.")


@bot.callback_query_handler(func=lambda call: call.data in ("confirm", "revise"))
def handle_callback(call):
    state = get_state(call.from_user.id)
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    # убираем инлайн-кнопки у обработанного сообщения, чтобы не нажимали повторно
    try:
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except Exception:
        pass

    if call.data == "confirm":
        if state["mode"] == "generate":
            bot.send_message(chat_id, "Отлично, готов продолжить в любое время")
        else:
            bot.send_message(chat_id, "Отлично, готов к следующему исследованию")
        state["stage"] = "awaiting_input"

    elif call.data == "revise":
        if state["mode"] == "generate":
            bot.send_message(chat_id, "Какие правки вы хотите внести?")
            state["stage"] = "awaiting_revision"
        else:
            bot.send_message(chat_id, "Что вы хотите уточнить?")
            state["stage"] = "awaiting_clarify"


if __name__ == "__main__":
    logging.info("LMR Hub bot started")
    bot.infinity_polling(skip_pending=True)
