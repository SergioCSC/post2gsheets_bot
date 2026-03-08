import gspread
import google.auth
import requests

import os
import re
import logging
from datetime import datetime, timezone, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO)

# HTTP / requests configuration
REQUEST_TIMEOUT_SECONDS = 10

# Environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
SHEET_ID = os.environ.get('SHEET_ID')

# Regex patterns
HW_PREFIX_PATTERN = re.compile(r'^(?:Homework on the topic|Домашка по теме|Домашнее задание по теме|дз по теме|домашнее задание|домашнее|дз|домашка):?', re.IGNORECASE)
HW_TOPIC_PATTERN = re.compile(r'["«]([^"»]+)["»]')
SCORE_PATTERN = re.compile(r'(?:Total|Итого):?\s*.*?(\d+(?:[.,]\d+)?)\s*(?:out of|из)\s*(\d+)', re.IGNORECASE)

def telegram_bot(request):
    """HTTP Cloud Function to handle Telegram webhook."""
    if request.method != 'POST':
        return 'Only POST requests are accepted', 405

    update = request.get_json(silent=True)
    if not update or 'message' not in update:
        return 'Invalid update', 200

    message = update['message']
    text = message.get('text', '') or message.get('caption', '')
    chat = message.get('chat', {})
    chat_id = chat.get('id')
    
    logging.info(f"Received message: {message} from chat ID: {chat_id}")
    logging.info(f"Mu from chat ID: {chat_id}")
    
    # Get pupil name from:
    # 1. Forwarded from chat (groups/channels)
    # 2. Forwarded from user
    # 3. Forwarded sender name (privacy enabled)
    # 4. Current chat title (if bot is in group)
    # 5. Current chat sender (if private chat with bot)
    
    pupil_name = None
    if 'forward_from_chat' in message:
        pupil_name = message['forward_from_chat'].get('title')
    elif 'forward_from' in message:
        user = message['forward_from']
        pupil_name = user.get('first_name', '')
        if user.get('last_name'):
            pupil_name += f" {user['last_name']}"
    elif 'forward_sender_name' in message:
        pupil_name = message['forward_sender_name']
    
    if not pupil_name:
        pupil_name = chat.get('title') or chat.get('first_name') or "Unknown"

    # Get chat title
    chat_title = chat.get('title') or chat.get('first_name') or pupil_name
    
    # Clean chat title for worksheet title
    chat_title = re.sub(r'[\\/:\?\*\[\]]', '_', chat_title)[:31]

    message_unixtimestamp: float = message.get('forward_origin', {}).get('forward_date') or message.get('forward_date') or message.get('date')
    message_unixtimestamp = message_unixtimestamp or get_georgian_timestamp()
    message_time_str = datetime.fromtimestamp(message_unixtimestamp).strftime('%Y-%m-%d %H:%M:%S')

    logging.info(f"Determined chat title: {chat_title}")
    
    try:
        if HW_PREFIX_PATTERN.match(text):
            logging.info(f"Detected homework message: {text.splitlines()[0]}")
            topic_match = HW_TOPIC_PATTERN.search(text)
            if topic_match:
                topic = topic_match.group(1).strip()
                add_homework(chat_title, message_time_str, topic)
                send_telegram_message(chat_id, f"✅ Записано домашнее задание: {topic} для {chat_title}")
            else:
                send_telegram_message(chat_id, f"⚠️ Найдено домашнее задание, но тема дз не обнаружена. Пожалуйста, заключите тему в кавычки (например: дз: \"Тема\").")
        elif SCORE_PATTERN.search(text):
            logging.info(f"Detected score message: {text.splitlines()[-1]}")
            match = SCORE_PATTERN.search(text)
            raw_score = match.group(1)
            max_score = match.group(2)

            # Normalize decimal separator to dot so that values like "2,5" become "2.5"
            normalized_score = raw_score.replace(',', '.')

            add_score(chat_title, message_time_str, normalized_score, max_score)
            send_telegram_message(chat_id, f"✅ Записана оценка: {normalized_score}/{max_score} для {chat_title}")
    except Exception as e:
        logging.error(f"Error: {e}")
        send_telegram_message(chat_id, f"❌ Ошибка: {str(e)}")

    return 'OK', 200


def send_telegram_message(chat_id, text):
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN not set")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text}
    try:
        requests.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as e:
        logging.error(f"Failed to send message: {e}")

def get_sheet():
    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    gc = gspread.authorize(credentials)
    return gc.open_by_key(SHEET_ID)

def get_georgian_timestamp() -> float:
    """Get current time in Georgia (UTC+4)."""
    tbilisi_tz = timezone(timedelta(hours=4))
    return datetime.now(tbilisi_tz).timestamp()

def add_homework(chat_title, time_str, topic):
    logging.info(f"Adding homework for {chat_title}: {topic}")
    sh = get_sheet()
    try:
        worksheet = sh.worksheet(chat_title)
        logging.info(f"Before adding homework: {worksheet.get_all_values()}")
        if not worksheet.get_all_values() or worksheet.get_all_values() == [[]]:
            worksheet.append_row(['Время ДЗ', 'Время проверки', 'Тема', 'Оценка', 'Макс. балл'])
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=chat_title, rows="100", cols="6")
        worksheet.append_row(['Время ДЗ', 'Время проверки', 'Тема', 'Оценка', 'Макс. балл'])
    
    worksheet.append_row([time_str, '', topic, '', ''])

def add_score(chat_title, time_str, score, max_score):
    logging.info(f"Adding score for {chat_title}: {score}/{max_score}")
    sh = get_sheet()
    try:
        worksheet = sh.worksheet(chat_title)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=chat_title, rows="100", cols="6")
        worksheet.append_row(['Время ДЗ', 'Время проверки', 'Тема', 'Оценка', 'Макс. балл'])
        worksheet.append_row(['', time_str, 'Без темы', score, max_score])
        return

    values = worksheet.get_all_values()
    if len(values) < 2:
        worksheet.append_row(['', time_str, 'Без темы', score, max_score])
        return
    
    # Update the very last row
    last_row_idx = len(values)
    worksheet.update_cell(last_row_idx, 2, time_str)
    worksheet.update_cell(last_row_idx, 4, score)
    worksheet.update_cell(last_row_idx, 5, max_score)