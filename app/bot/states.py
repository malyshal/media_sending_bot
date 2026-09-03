from aiogram.fsm.state import State, StatesGroup

class ChatSettingsStates(StatesGroup):
    waiting_for_tag_search = State()
    confirming_tag_action = State()
    setting_schedule = State()
    setting_schedule_max_posts = State()
    setting_schedule_interval = State()
    setting_next_max_posts = State()
    confirm_deletion = State()

class OnboardingStates(StatesGroup):
    waiting_for_first_tag = State()
    selecting_tag = State()
