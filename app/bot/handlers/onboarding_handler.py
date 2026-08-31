from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram import Bot
from app.bot.states import OnboardingStates, ChatSettingsStates
from app.db.session import async_session
from app.db.repositories.user_repository import UserRepository
from app.db.repositories.chat_repository import ChatRepository
from app.joyreactor.client import JoyReactorClient
from app.services.post_service import PostService
from app.queue.api_queue import APIQueue
from app.core.config import settings
import structlog
from app.bot.handlers.next_handler import handle_next_request

logger = structlog.get_logger()

router = Router()
jr_client = JoyReactorClient()

async def get_post_button():
    return InlineKeyboardButton(text="▶️ Получить пост", callback_data="get_first_post")

async def get_add_tag_button():
    return InlineKeyboardButton(text="➕ Добавить ещё тег", callback_data="add_more_tags")

async def get_exclude_tag_button():
    return InlineKeyboardButton(text="🚫 Добавить исключение", callback_data="add_exclude_tags")

async def get_settings_button():
    return InlineKeyboardButton(text="⚙️ Настройки", callback_data="go_to_settings")

@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    async with async_session() as session:
        user_repo = UserRepository(session)
        chat_repo = ChatRepository(session)
        
        user = await user_repo.get_user(message.from_user.id)
        config = await chat_repo.get_config(message.chat.id)
        
        if user.is_frozen:
            await message.answer("Ваш аккаунт заморожен. Обратитесь в поддержку.")
            return

        if not config.include_tags:
            # Onboarding flow
            text = (
                "👋 Привет!\n\n"
                "Я JoyBot — бот для поиска и отправки постов с JoyReactor прямо в Telegram.\n\n"
                "Я умею:\n"
                "• находить посты по тегам;\n"
                "• исключать нежелательные темы;\n"
                "• не отправлять один и тот же пост повторно;\n"
                "• отправлять посты вручную или по расписанию.\n\n"
                "Давай настроим тебя за пару шагов.\n\n"
                "🏷 Сначала добавь первый тег, который хочешь видеть в своей ленте."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔎 Найти тег", callback_data="find_tag")]
            ])
            await message.answer(text, reply_markup=kb)
            await state.set_state(OnboardingStates.waiting_for_first_tag)
        else:
            # Existing user flow
            tags_str = "\n• " + "\n• ".join(config.include_tags)
            ex_tags_str = "\n• " + "\n• ".join(config.exclude_tags) if config.exclude_tags else "\n(пусто)"
            
            text = (
                "👋 С возвращением!\n\n"
                "✅ Показывать:\n" + tags_str + "\n\n"
                "🚫 Исключать:" + ex_tags_str + "\n\n"
                "Готов к работе."
            )
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [await get_post_button()],
                [InlineKeyboardButton(text="🏷 Изменить теги", callback_data="change_tags")],
                [InlineKeyboardButton(text="⏰ Расписание", callback_data="set_schedule")],
                [await get_settings_button()]
            ])
            await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "find_tag")
async def find_tag_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите название тега для поиска:")
    await state.set_state(OnboardingStates.waiting_for_first_tag)

@router.message(OnboardingStates.waiting_for_first_tag)
async def process_tag_search(message: Message, state: FSMContext):
    query = message.text
    try:
        tags = await jr_client.search_tags(query)
        if not tags:
            await message.answer("Ничего не найдено. Попробуйте другой запрос.")
            return
        
        kb_list = []
        for tag in tags[:10]: # Limit to 10 results
            kb_list.append([InlineKeyboardButton(text=tag, callback_data=f"tag_select:{tag}")])
        
        kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
        await message.answer(f"Найдены следующие теги:\n{query}", reply_markup=kb)
        await state.set_state(OnboardingStates.selecting_tag)
    except Exception as e:
        logger.error("tag_search_error", error=str(e))
        await message.answer("Произошла ошибка при поиске тегов. Попробуйте позже.")

@router.callback_query(F.data.startswith("tag_select:"))
async def select_tag(callback: CallbackQuery, state: FSMContext):
    tag = callback.data.split(":")[1]
    await callback.answer()
    
    async with async_session() as session:
        chat_repo = ChatRepository(session)
        config = await chat_repo.get_config(callback.message.chat.id)
        
        # Avoid duplicates
        if tag not in config.include_tags:
            config.include_tags.append(tag)

            await chat_repo.update_tags(callback.message.chat.id, config.include_tags, config.exclude_tags)
        
        await callback.message.answer(f"✅ Тег «{tag}» добавлен в список предпочтительных.")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [await get_post_button()],
            [await get_add_tag_button()],
            [await get_exclude_tag_button()],
            [await get_settings_button()]
        ])
        await callback.message.answer(
            "Теперь вы можете получить первый пост или настроить бота дальше.",
            reply_markup=kb
        )
        await state.clear()

@router.callback_query(F.data == "get_first_post")
async def get_first_post_handler(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    await callback.message.answer("Ищу подходящий пост...")

    from aiogram.types import Message
    dummy_msg = Message(
        message_id=callback.message.message_id,
        chat=callback.message.chat,
        from_user=callback.from_user,
        text="/next"
    )
    await handle_next_request(dummy_msg, bot)

