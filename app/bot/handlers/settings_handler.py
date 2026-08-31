from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
import structlog

from app.db.session import async_session
from app.db.repositories.chat_repository import ChatRepository
from app.db.repositories.user_repository import UserRepository
from app.services.post_service import PostService
from app.services.media_manager import MediaManager
from app.services.delivery_service import DeliveryService
from app.queue.api_queue import APIQueue
from app.joyreactor.client import JoyReactorClient
from app.bot.states import ChatSettingsStates

logger = structlog.get_logger()
router = Router()

@router.message(Command("settings"))
async def cmd_settings(message: types.Message):
    chat_id = message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        
        text = (
            f"⚙️ *Настройки JoyBot*\n\n"
            f"✅ Автоотправка: {'Вкл' if config.auto_send else 'Выкл'}\n"
            f"🕒 Время: {config.schedule} ({config.timezone})\n"
            f"📦 Лимит (Регламент): {config.schedule_max_posts} постов\n"
            f"📦 Лимит (/next): {config.next_max_posts} постов\n"
            f"📥 Include: {', '.join(config.include_tags) if config.include_tags else 'все'}\n"
            f"🚫 Exclude: {', '.join(config.exclude_tags) if config.exclude_tags else 'нет'}"
        )
        
        kb = InlineKeyboardBuilder()
        kb.button(text="🔍 Поиск тегов", callback_data="set_tags")
        kb.button(text="⏰ Время", callback_data="set_schedule")
        kb.button(text="🛑 Стоп/Старт", callback_data="toggle_auto")
        kb.adjust(1)
        
        await message.answer(text, parse_mode="Markdown", reply_markup=kb.as_markup())

@router.message(Command("search_tags"))
async def cmd_search_tags(message: types.Message, state: FSMContext, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    if not message.text or len(message.text.split()) < 2:
        await message.answer("Используйте: `/search_tags <запрос>`")
        return
    
    query = " ".join(message.text.split()[1:])
    await state.set_state(ChatSettingsStates.waiting_for_tag_search)
    await state.update_data(tag_query=query)
    
    await process_tag_search(message, query, state, api_queue, jr_client)

async def process_tag_search(message: types.Message, query: str, state: FSMContext, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    async with async_session() as session:
        try:
            tags = await api_queue.enqueue(jr_client.search_tags, query, priority=1)
            if not tags:
                await message.answer("Теги не найдены 😕")
                return
            
            kb = InlineKeyboardBuilder()
            for tag in tags[:6]:
                kb.button(text=tag.name, callback_data=f"tag_select:{tag.name}")
            kb.adjust(1)
            
            await message.answer(f"Результаты по запросу *{query}*:", parse_mode="Markdown", reply_markup=kb.as_markup())
        except Exception as e:
            logger.error("tag_search_failed", error=str(e))
            await message.answer("Произошла ошибка при поиске тегов.")

@router.callback_query(F.data.startswith("tag_select:"))
async def cb_tag_selected(callback: types.CallbackQuery, state: FSMContext):
    tag_name = callback.data.split(":")[1]
    await state.update_data(selected_tag=tag_name)
    await state.set_state(ChatSettingsStates.confirming_tag_action)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить в INCLUDE", callback_data="tag_add_inc")
    kb.button(text="🚫 Добавить в EXCLUDE", callback_data="tag_add_exc")
    kb.button(text="❌ Отмена", callback_data="tag_cancel")
    kb.adjust(1)
    
    await callback.message.edit_text(f"Что сделать с тегом *{tag_name}*?", parse_mode="Markdown", reply_markup=kb.as_markup())

@router.callback_query(F.data == "tag_add_inc")
async def cb_tag_inc(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tag = data.get("selected_tag")
    chat_id = callback.message.chat.id
    
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        
        new_inc = list(set(config.include_tags + [tag]))
        new_exc = [t for t in config.exclude_tags if t != tag]
        
        await chat_repo.update_tags(chat_id, new_inc, new_exc)
        await callback.message.edit_text(f"Тег *{tag}* добавлен в список разрешенных ✅", parse_mode="Markdown")
        await state.clear()

@router.callback_query(F.data == "tag_add_exc")
async def cb_tag_exc(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tag = data.get("selected_tag")
    chat_id = callback.message.chat.id
    
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        
        new_exc = list(set(config.exclude_tags + [tag]))
        new_inc = [t for t in config.include_tags if t != tag]
        
        await chat_repo.update_tags(chat_id, new_inc, new_exc)
        await callback.message.edit_text(f"Тег *{tag}* добавлен в список исключений 🚫", parse_mode="Markdown")
        await state.clear()

@router.callback_query(F.data == "tag_cancel")
async def cb_tag_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Действие отменено.")
    await state.clear()

@router.callback_query(F.data == "toggle_auto")
async def cb_toggle_auto(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(chat_id)
        
        new_state = not config.auto_send
        await chat_repo.set_auto_send(chat_id, new_state)
        
        status = "Вкл" if new_state else "Выкл"
        await callback.answer(f"Автоотправка: {status}")
        
        text = (
            f"⚙️ *Настройки JoyBot*\n\n"
            f"✅ Автоотправка: {status}\n"
            f"🕒 Время: {config.schedule} ({config.timezone})\n"
            f"📦 Лимит (Регламент): {config.schedule_max_posts} постов\n"
            f"📦 Лимит (/next): {config.next_max_posts} постов\n"
            f"📥 Include: {', '.join(config.include_tags) if config.include_tags else 'все'}\n"
            f"🚫 Exclude: {', '.join(config.exclude_tags) if config.exclude_tags else 'нет'}"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="🔍 Поиск тегов", callback_data="set_tags")
        kb.button(text="⏰ Время", callback_data="set_schedule")
        kb.button(text="🛑 Стоп/Старт", callback_data="toggle_auto")
        kb.adjust(1)
        
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.as_markup())

@router.callback_query(F.data == "set_schedule")
async def cb_set_schedule(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ChatSettingsStates.setting_schedule)
    await callback.message.answer("Введите время в формате HH:MM (например, 10:00):")
    await callback.answer()

@router.message(ChatSettingsStates.setting_schedule)
async def proc_set_schedule(message: types.Message, state: FSMContext):
    import re
    time_pattern = r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    if not re.match(time_pattern, message.text):
        await message.answer("Неверный формат! Пожалуйста, введите время как HH:MM (например, 18:30).")
        return
    
    chat_id = message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        from app.core.config import settings
        await chat_repo.update_schedule(chat_id, message.text, settings.default_timezone)
        
    await message.answer(f"⏰ Время автоматической отправки установлено на {message.text}")
    await state.clear()

@router.message(Command("stop"))
async def cmd_stop(message: types.Message):
    chat_id = message.chat.id
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        await chat_repo.set_auto_send(chat_id, False)
    await message.answer("🛑 Бот остановлен для этого чата. Автоматическая рассылка выключена.")

@router.message(Command("delete_my_data"))
async def cmd_delete_data(message: types.Message, state: FSMContext):
    chat_id = message.chat.id
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data="confirm_delete_yes")
    kb.button(text="❌ Отмена", callback_data="confirm_delete_no")
    
    await message.answer(
        "⚠️ *Внимание!*\n\nВы действительно хотите удалить все свои данные? "
        "Ваш аккаунт будет заморожен на 30 дней, после чего данные будут удалены окончательно.",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )
    await state.set_state(ChatSettingsStates.confirm_deletion)

@router.callback_query(F.data == "confirm_delete_yes")
async def cb_delete_yes(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    
    async with async_session() as session:
        user_repo = UserRepository(session)
        chat_repo = ChatRepository(session)
        
        await user_repo.request_deletion(user_id)
        await chat_repo.set_auto_send(chat_id, False)
        
    await callback.message.edit_text("🗑 Данные заморожены. Вы можете отменить удаление в течение 30 дней с помощью команды `/restore`.")
    await state.clear()

@router.callback_query(F.data == "confirm_delete_no")
async def cb_delete_no(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Запрос на удаление отменен.")
    await state.clear()

@router.message(Command("restore"))
async def cmd_restore(message: types.Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_user(user_id)
        
        if not user.is_frozen:
            await message.answer("Ваш аккаунт не находится в состоянии удаления.")
            return
            
        await user_repo.cancel_deletion(user_id)
        await message.answer("✅ Удаление отменено! Ваши данные восстановлены.")
