import os
import re
import uuid
import asyncio
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile, CallbackQuery
import yt_dlp

API_TOKEN = '7778413375:AAHX7zqBmRVh-ihK0tX580JJtNDRuV5UMMo'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()

def sanitize_filename(name: str) -> str:
    """Удаляем запрещённые символы из имени файла"""
    return re.sub(r'[\\/*?:"<>|]', '', name)

async def search_tracks(query: str) -> list:
    """Ищем видео на YouTube по запросу, возвращаем до 10 результатов"""
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',  # Чтобы не скачивать страницы
        'outtmpl': '%(title)s.%(ext)s',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch10:{query}", download=False)
        entries = info.get('entries', [])
        results = []
        for entry in entries:
            if entry:
                results.append({
                    'id': entry.get('id'),
                    'title': entry.get('title')
                })
        return results

async def download_audio(video_url: str) -> str:
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'geo_bypass': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/125.0.0.0 Safari/537.36'
        },
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': '%(title)s.%(ext)s',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(video_url, download=True)
        title = info_dict.get('title', 'audio')
        filename = sanitize_filename(f"{title}.mp3")
        return filename

@router.message(Command(commands=['start']))
async def send_welcome(message: types.Message):
    await message.reply("👋 Привет! Я бот для скачивания музыки с YouTube 🎵\n"
                        "\n Просто напиши название песни, и я найду её для тебя 🔍")

@router.message(F.text)
async def handle_search(message: types.Message):
    query = message.text.strip()
    if not query:
        await message.answer("❗ Пожалуйста, укажи название песни.")
        return

    await message.answer("🔍 Ищу музыку...")

    tracks = await search_tracks(query)
    if not tracks:
        await message.answer("😔 Ничего не найдено по твоему запросу.")
        return

    buttons = []
    for i, track in enumerate(tracks):
        title = track['title']
        video_id = track['id']
        buttons.append([InlineKeyboardButton(text=f"🎵 {i+1}. {title[:45]}", callback_data=f"get_{video_id}")])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🎧 Выбери трек для скачивания:", reply_markup=markup)

@router.callback_query(F.data.startswith("get_"))
async def handle_download(callback: CallbackQuery):
    video_id = callback.data[4:]
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    # ✅ Быстрое подтверждение (до 10 секунд)
    await callback.answer("⏬ Загружаю аудио, подожди...")

    # ⚙️ Сообщаем пользователю, что начали скачивание
    status_message = await callback.message.answer("🎧 Скачиваю трек... Это может занять до минуты ⏳")

    try:
        filename = await download_audio(video_url)

        if not os.path.exists(filename):
            await status_message.edit_text("❌ Ошибка: аудиофайл не был создан.")
            return

        if os.path.getsize(filename) < 10 * 1024:
            await status_message.edit_text("❌ Ошибка: файл слишком маленький.")
            os.remove(filename)
            return

        audio = FSInputFile(filename)
        await bot.send_audio(callback.from_user.id, audio)

        os.remove(filename)
        await status_message.edit_text("✅ Готово! Трек отправлен 🎵")

    except Exception as e:
        await status_message.edit_text(f"⚠️ Произошла ошибка: {e}")


dp.include_router(router)

async def main():
    print("🚀 Бот запущен")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
