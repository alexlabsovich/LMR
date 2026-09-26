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
TELEGRAM_TOKEN = "8416010350:AAHvoGxRI4mgC1GE0P7nL4r5DKDKDkc_5sM"
OPENROUTER_API_KEY = "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"
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
        }
    return user_states[user_id]


def main_menu_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
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
пост — без лишних пояснений от себя."""

RESEARCH_SYSTEM_PROMPT = """Ты — ассистент для глубокого исследования (research), работающий на
уровне продвинутого ассистента с доступом к интернету. Твоя задача:
1. Искать и анализировать информацию по всей сети;
2. Проводить углублённый анализ, а не поверхностный пересказ;
3. Изучать документацию, официальные источники и первичные материалы;
4. Обязательно прикладывать в ответе релевантные ссылки на источники.
Отвечай структурированно, по делу, с фактами и ссылками в конце ответа."""


def generate_post(user_text, use_search):
    messages = [
        {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    return call_openrouter(messages, use_web_search=use_search)


def revise_post(original_text, revision_request):
    messages = [
        {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
        {"role": "assistant", "content": original_text},
        {"role": "user", "content": f"Внеси следующие правки в пост:\n{revision_request}"},
    ]
    return call_openrouter(messages, use_web_search=False)


def research_query(user_text):
    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    return call_openrouter(messages, use_web_search=True)


def research_followup(original_text, clarification):
    messages = [
        {"role": "system", "content": RESEARCH_SYSTEM_PROMPT},
        {"role": "assistant", "content": original_text},
        {"role": "user", "content": f"Уточни и дополни исследование с учётом:\n{clarification}"},
    ]
    return call_openrouter(messages, use_web_search=True)


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
        "Пришлите данные для генерации. В ответ я могу отправить сообщение с примечаниями "
        "в виде цифр:\n\n"
        "1 Пример поста\n"
        "2 Нужно ли проводить дополнительный углублённый ресерч в интернете\n"
        "3 Скопировать стиль / создать свой\n"
        "4 Нужно ли искать в сети похожие посты и использовать их в генерации поста",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.message_handler(func=lambda m: m.text == "Research")
def handle_research_button(message):
    state = get_state(message.from_user.id)
    state["mode"] = "research"
    state["stage"] = "awaiting_input"
    bot.send_message(
        message.chat.id,
        "Я готов к углублённому изучению, опишите ваш запрос.",
        reply_markup=types.ReplyKeyboardRemove(),
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
                result = generate_post(text, use_search=wants_search(text))
            else:
                result = revise_post(state["last_content"], text)

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
