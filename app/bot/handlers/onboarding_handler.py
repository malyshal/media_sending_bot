from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from app.bot.states import OnboardingStates, ChatSettingsStates
from app.bot.menu import build_home_text, build_home_keyboard, home_back_button
from app.db.session import async_session
from app.db.repositories.user_repository import UserRepository
from app.db.repositories.chat_repository import ChatRepository
from app.db.repositories.post_repository import PostRepository
from app.services.post_service import PostService
from app.queue.api_queue import APIQueue
import structlog

logger = structlog.get_logger()

router = Router()


@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    # Reset any lingering FSM state: /start is the universal "home" action
    await state.clear()
    await state.set_data({})

    async with async_session() as session:
        user_repo = UserRepository(session)
        chat_repo = ChatRepository(session)

        user = await user_repo.get_user(message.from_user.id)
        config = await chat_repo.get_config(message.chat.id)

        if user.is_frozen:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔄 Восстановить", callback_data="restore_account")
            ]])
            await message.answer(
                "Ваш аккаунт заморожен (запрошено удаление данных).\n"
                "Вы можете восстановить его в течение 30 дней.",
                reply_markup=kb,
            )
            return

        if not config.include_tags:
            # Onboarding flow: ask for the first tag
            text = (
                "👋 Привет!\n\n"
                "Я JoyBot — бот для поиска и отправки постов с JoyReactor прямо в Telegram.\n\n"
                "Я умею:\n"
                "• находить посты по тегам;\n"
                "• исключать нежелательные темы;\n"
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
            await message.answer(build_home_text(config), reply_markup=build_home_keyboard())


@router.callback_query(F.data == "restore_account")
async def cb_restore_account(callback: CallbackQuery):
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_user(callback.from_user.id)
        if user.is_frozen:
            await user_repo.cancel_deletion(callback.from_user.id)
            await callback.answer("Аккаунт восстановлен ✅", show_alert=True)
        else:
            await callback.answer("Аккаунт не заморожен")


@router.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
    await callback.message.edit_text(
        build_home_text(config), reply_markup=build_home_keyboard()
    )


@router.callback_query(F.data == "home_next")
async def cb_home_next(callback: CallbackQuery, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    await callback.answer("Ищу посты...")
    chat_id = callback.message.chat.id
    async with async_session() as session:
        config = await ChatRepository(session).get_config(chat_id)
        post_service = PostService(jr_client, api_queue, PostRepository(session))
        from app.services.delivery_service import DeliveryService
        from app.services.media_manager import MediaManager
        delivery = DeliveryService(bot, post_service, MediaManager())

        sent_count = await delivery.send_batch_posts(
            chat_id=chat_id,
            include_tags=config.include_tags,
            exclude_tags=config.exclude_tags,
            max_posts=config.next_max_posts,
            ignore_history=False,
        )
    if sent_count == 0:
        await callback.message.answer(
            "Не нашлось новых постов по вашим фильтрам 😢 Попробуйте позже или измените теги."
        )


@router.callback_query(F.data == "home_search_tags")
async def cb_home_search_tags(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите название тега для поиска:")
    await state.set_state(OnboardingStates.waiting_for_first_tag)


@router.callback_query(F.data == "home_settings")
async def cb_home_settings(callback: CallbackQuery):
    from app.bot.handlers.settings_handler import open_settings
    await open_settings(callback)


@router.callback_query(F.data == "home_help")
async def cb_home_help(callback: CallbackQuery):
    await callback.answer()
    from app.bot.handlers.help_handler import help_text
    await callback.message.answer(help_text(), reply_markup=None)


@router.callback_query(F.data == "find_tag")
async def find_tag_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.answer("Введите название тега для поиска:")
    await state.set_state(OnboardingStates.waiting_for_first_tag)


@router.message(OnboardingStates.waiting_for_first_tag)
async def process_tag_search(message: Message, state: FSMContext, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    query = message.text
    try:
        tags = await api_queue.enqueue(jr_client.search_tags, query)
        if not tags:
            await message.answer("Ничего не найдено. Попробуйте другой запрос.")
            return

        kb_list = []
        for tag in tags[:10]:
            kb_list.append([InlineKeyboardButton(text=tag.name, callback_data=f"tag_select:{tag.name}")])

        kb = InlineKeyboardMarkup(inline_keyboard=kb_list)
        await message.answer(f"Найдены следующие теги по запросу «{query}»:", reply_markup=kb)
        await state.set_state(OnboardingStates.selecting_tag)
    except Exception as e:
        logger.error("tag_search_error", error=str(e))
        await message.answer("Произошла ошибка при поиске тегов. Попробуйте позже.")


@router.callback_query(F.data.startswith("tag_select:"))
async def select_tag(callback: CallbackQuery, state: FSMContext):
    tag = callback.data.split(":", 1)[1]
    await callback.answer()

    await state.update_data(selected_tag=tag)
    await state.set_state(ChatSettingsStates.confirming_tag_action)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить в INCLUDE", callback_data="tag_add_inc")],
        [InlineKeyboardButton(text="🚫 Добавить в EXCLUDE", callback_data="tag_add_exc")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="tag_cancel")]
    ])

    await callback.message.edit_text(f"Что сделать с тегом *{tag}*?", parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data == "get_first_post")
async def get_first_post_handler(callback: CallbackQuery, bot: Bot, api_queue: 'APIQueue', jr_client: 'JoyReactorClient'):
    await callback.answer("Ищу подходящий пост...")

    async with async_session() as session:
        config = await ChatRepository(session).get_config(callback.message.chat.id)

        from app.services.delivery_service import DeliveryService
        from app.services.media_manager import MediaManager

        post_service = PostService(jr_client, api_queue, PostRepository(session))
        media_manager = MediaManager()
        delivery = DeliveryService(bot, post_service, media_manager)

        sent_count = await delivery.send_batch_posts(
            chat_id=callback.message.chat.id,
            include_tags=config.include_tags,
            exclude_tags=config.exclude_tags,
            max_posts=1,
            ignore_history=False
        )

        if sent_count == 0:
            await callback.message.answer("К сожалению, не удалось найти подходящий пост прямо сейчас. Попробуйте позже!")


@router.callback_query(F.data == "change_tags")
async def cb_change_tags(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    # Open the tag management screen (search + current lists)
    from app.bot.handlers.settings_handler import open_tag_menu
    await open_tag_menu(callback, state)


@router.callback_query(F.data == "go_to_settings")
async def cb_go_to_settings(callback: CallbackQuery):
    from app.bot.handlers.settings_handler import open_settings
    await open_settings(callback)


@router.callback_query(F.data == "set_schedule")
async def cb_set_schedule_entry(callback: CallbackQuery, state: FSMContext):
    from app.bot.handlers.settings_handler import open_schedule_menu
    await open_schedule_menu(callback, state)