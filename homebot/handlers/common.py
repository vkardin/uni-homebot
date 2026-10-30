"""Главное меню, /start с приветствием UNI, /cancel, раздел «Сервер»."""

from __future__ import annotations

import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from homebot.config import Settings
from homebot.services.home import HomeService
from homebot.ui.callbacks import MenuAction, MenuCb
from homebot.ui.home_text import render_home
from homebot.ui.keyboards import main_menu, server_menu
from homebot.ui.texts import SERVER_MENU_TEXT
from homebot.utils.telegram import ASSETS, edit_or_send

log = logging.getLogger(__name__)
router = Router(name="common")

WELCOME_IMAGE = ASSETS / "uni.jpg"


async def home_text(settings: Settings, home: HomeService | None) -> str:
    """Стартовый экран: приветствие + погода, курс, нагрузка, пара новостей (если сервис подключён)."""
    data = None
    if home is not None:
        try:
            data = await home.data()
        except Exception:  # меню должно открыться в любом случае
            log.exception("Home summary failed")
    return render_home(datetime.now(settings.tz), settings.tz, data, settings.actual_currency)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, settings: Settings, home: HomeService | None = None) -> None:
    await state.clear()
    text = await home_text(settings, home)
    if WELCOME_IMAGE.exists():
        try:
            await message.answer_photo(FSInputFile(WELCOME_IMAGE), caption=text, reply_markup=main_menu())
            return
        except TelegramAPIError as exc:  # картинка — украшение, без неё меню всё равно должно открыться
            log.warning("Cannot send welcome image: %s", exc)
    await message.answer(text, reply_markup=main_menu())


@router.message(Command("menu", "help"))
async def cmd_menu(message: Message, state: FSMContext, settings: Settings, home: HomeService | None = None) -> None:
    await state.clear()
    await message.answer(await home_text(settings, home), reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, settings: Settings, home: HomeService | None = None) -> None:
    had_state = await state.get_state() is not None
    await state.clear()
    text = "Готово, вышел из режима 👌" if had_state else "Сейчас нечего отменять 🙂"
    await message.answer(f"{text}\n\n{await home_text(settings, home)}", reply_markup=main_menu())


@router.callback_query(MenuCb.filter(F.action == MenuAction.HOME))
async def cb_home(call: CallbackQuery, state: FSMContext, settings: Settings, home: HomeService | None = None) -> None:
    await call.answer()
    await state.clear()
    await edit_or_send(call, await home_text(settings, home), main_menu())


@router.message(Command("server"))
async def cmd_server(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(SERVER_MENU_TEXT, reply_markup=server_menu())


@router.callback_query(MenuCb.filter(F.action == MenuAction.SERVER))
async def cb_server(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await state.clear()
    await edit_or_send(call, SERVER_MENU_TEXT, server_menu())
