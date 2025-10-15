import os
import re
import uuid
import asyncio
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile, CallbackQuery
import yt_dlp

# ⚠️ ВСТАВЬТЕ СВОЙ API-ТОКЕН
API_TOKEN = '7778413375:AAHX7zqBmRVh-ihK0tX580JJtNDRuV5UMMo'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()


def sanitize_filename(name: str) -> str:
    """Удаляем запрещённые символы из имени файла"""
    return re.sub(r'[\\/*?:"<>|]', '', name)


# 🔧 Общие настройки для yt_dlp
YDL_COMMON = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'geo_bypass': True,
    'retries': 3,  # повторить при неудаче
    'socket_timeout': 30,  # тайм-аут соединения
    'http_headers': {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/125.0.0.0 Safari/537.36'
        )
    },
}


async def search_tracks(query: str) -> list:
    """Ищем видео на YouTube по запросу, возвращаем до 10 результатов"""
    ydl_opts = YDL_COMMON.copy()
    ydl_opts.update({
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'outtmpl': '%(title)s.%(ext)s',
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch10:{query}", download=False)
            entries = info.get('entries', [])
            results = [{'id': e.get('id'), 'title': e.get('title')} for e in entries if e]
            return results
    except Exception as e:
        print(f"[Ошибка поиска] {e}")
        return []


async def download_audio(video_url: str) -> str:
    """Скачиваем аудио с YouTube и возвращаем имя файла .mp3 с использованием прокси"""
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'retries': 10,  # количество попыток при ошибке сети
        'socket_timeout': 15,  # таймаут соединения
        'nocheckcertificate': True,  # пропустить SSL-проверку
        'proxy': 'socks5://user:pass@host:port',  # <--- вставь свой прокси сюда
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': '%(title)s.%(ext)s',
    }

    try:
        # ограничим время выполнения на случай зависания
        async def run_ydl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(video_url, download=True)
                title = info_dict.get('title', 'audio')
                filename = sanitize_filename(f"{title}.mp3")
                return filename

        filename = await asyncio.wait_for(run_ydl(), timeout=120)  # максимум 2 минуты
        return filename

    except asyncio.TimeoutError:
        raise Exception("⏱️ Превышено время ожидания загрузки.")
    except Exception as e:
        raise Exception(f"Ошибка при скачивании: {e}")


@router.message(Command(commands=['start']))
async def send_welcome(message: types.Message):
    await message.reply(
        "👋 Привет! Я бот для скачивания музыки с YouTube 🎵\n\n"
        "Просто напиши название песни, и я найду её для тебя 🔍"
    )


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
        buttons.append([
            InlineKeyboardButton(
                text=f"🎵 {i + 1}. {title[:45]}",
                callback_data=f"get_{video_id}"
            )
        ])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("🎧 Выбери трек для скачивания:", reply_markup=markup)


@router.callback_query(F.data.startswith("get_"))
async def handle_download(callback: CallbackQuery):
    video_id = callback.data[4:]
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    await callback.answer("⏬ Загружаю аудио, подожди...")
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

        await bot.send_audio(callback.from_user.id, FSInputFile(filename))
        os.remove(filename)

        await status_message.edit_text("✅ Готово! Трек отправлен 🎵")

    except Exception as e:
        await status_message.edit_text(f"⚠️ Произошла ошибка: {e}")


dp.include_router(router)


async def main():
    print("🚀 Бот запущен и готов к работе!")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
