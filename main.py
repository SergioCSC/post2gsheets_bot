import gspread
import google.auth
import requests

import os
import re
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)

# Environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
SHEET_ID = os.environ.get('SHEET_ID')

# Regex patterns
HW_PREFIX_PATTERN = re.compile(r'^(?:Homework on the topic|Домашка по теме|Домашнее задание по теме|дз по теме|домашнее задание|домашнее|дз|домашка):?', re.IGNORECASE)
HW_TOPIC_PATTERN = re.compile(r'["«]([^"»]+)["»]')
SCORE_PATTERN = re.compile(r'(?:Total|Итого):?\s*(\d+)\s*(?:out of|из)\s*(\d+)', re.IGNORECASE)

def telegram_bot(request):
    """HTTP Cloud Function to handle Telegram webhook."""
    if request.method != 'POST':
        return 'Only POST requests are accepted', 405

    update = request.get_json(silent=True)
    if not update or 'message' not in update:
        return 'Invalid update', 200

    message = update['message']
    text = message.get('text', '')
    chat = message.get('chat', {})
    chat_id = chat.get('id')
    
    logging.info(f"Received message: {message} from chat ID: {chat_id}")
    
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

    # Clean pupil name for worksheet title
    pupil_name = re.sub(r'[\\/:\?\*\[\]]', '_', pupil_name)[:31]

    try:
        if HW_PREFIX_PATTERN.match(text):
            topic_match = HW_TOPIC_PATTERN.search(text)
            if topic_match:
                topic = topic_match.group(1).strip()
                add_homework(pupil_name, topic)
                send_telegram_message(chat_id, f"✅ Записано домашнее задание: {topic} для {pupil_name}")
            else:
                send_telegram_message(chat_id, f"⚠️ Найдено домашнее задание, но тема дз не обнаружена. Пожалуйста, заключите тему в кавычки (например: дз: \"Тема\").")
        elif SCORE_PATTERN.search(text):
            match = SCORE_PATTERN.search(text)
            score = match.group(1)
            max_score = match.group(2)
            add_score(pupil_name, score, max_score)
            send_telegram_message(chat_id, f"✅ Записана оценка: {score}/{max_score} для {pupil_name}")
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
        requests.post(url, json=payload)
    except Exception as e:
        logging.error(f"Failed to send message: {e}")

def get_sheet():
    credentials, project = google.auth.default(
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    gc = gspread.authorize(credentials)
    return gc.open_by_key(SHEET_ID)

def add_homework(pupil_name, topic):
    logging.info(f"Adding homework for {pupil_name}: {topic}")
    sh = get_sheet()
    try:
        worksheet = sh.worksheet(pupil_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=pupil_name, rows="100", cols="5")
        worksheet.append_row(['Date', 'Topic', 'Score', 'Max Score'])
    
    date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    worksheet.append_row([date_str, topic, '', ''])

def add_score(pupil_name, score, max_score):
    logging.info(f"Adding score for {pupil_name}: {score}/{max_score}")
    sh = get_sheet()
    try:
        worksheet = sh.worksheet(pupil_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=pupil_name, rows="100", cols="5")
        worksheet.append_row(['Date', 'Topic', 'Score', 'Max Score'])
        date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        worksheet.append_row([date_str, 'Unknown Topic', score, max_score])
        return

    values = worksheet.get_all_values()
    if len(values) < 2:
        date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        worksheet.append_row([date_str, 'Unknown Topic', score, max_score])
        return

    # Look for the last row with empty score
    for i in range(len(values), 1, -1):
        row = values[i-1]
        if len(row) < 3 or not str(row[2]).strip():
            worksheet.update_cell(i, 3, score)
            worksheet.update_cell(i, 4, max_score)
            return
    
    # If no empty score found, update the very last row
    last_row_idx = len(values)
    worksheet.update_cell(last_row_idx, 3, score)
    worksheet.update_cell(last_row_idx, 4, max_score)
