from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.utils.exceptions import BotBlocked, CantParseEntities

from app import db
from app.modules import user_module, user_record_module, helpers, base_exc
from configs import bot as bot_settings


class ExtensionBot(Bot):
    """Расширение базового класса бота"""

    async def send_message(self, *args, **kwargs) -> types.Message:
        """
        Добавлено:
          - всегда текст сообщений представляется как html;
          - сохранять все отправленные сообщения пользователям.
        """
        kwargs['parse_mode'] = 'html'
        try:
            message = await super(ExtensionBot, self).send_message(*args, **kwargs)
        except CantParseEntities:
            kwargs['parse_mode'] = None
            message = await super(ExtensionBot, self).send_message(*args, **kwargs)
        await save_user_step(message)
        return message


async def save_user_step(message: types.Message):
    """Сохранить информацию о сообщении"""
    user_step = db.UserHistory(chat_id=message.chat.id,
                               message_id=message.message_id,
                               )
    db.Session().add(user_step)
    db.Session().commit()
    db.Session().close()


bot = ExtensionBot(token=bot_settings.TOKEN)

dp = Dispatcher(bot)


def auto_registration(func):
    """
    Автоматическая регистрация пользователей.
    Сохранение сообщений пользователя.
    Аналог Middleware.
    """

    async def decorator(message: types.Message):
        # Авто регистрация
        if not user_module.TelegramUserRepository.is_exist(str(message.chat.id)):
            user = user_module.schemas.UserBot(username=message.chat.username, chat_id=message.chat.id)
            user_module.TelegramUserRepository.create(user)
        # Сохранить сообщение пользователя
        await save_user_step(message)
        return await func(message)

    return decorator


@dp.message_handler(commands='start')
@auto_registration
async def main_menu(message: types.Message):
    """Главное меню"""
    await bot.send_message(
        chat_id=message.chat.id,
        text=f'BOT ID: {message.chat.id}\n'
             f'USERNAME: {message.chat.username}\n'
             'Доступные команды:\n'
             '/start - Главное меню\n'
             '/add - Добавить новую запись\n'
             '/get - Посмотреть все свои записи\n'
             '/get {Номер записи} - Посмотреть в отдельности\n'
             '/del {Номер записи} - Удалить запись\n'
             '/hide - отчистить историю сообщений\n',
    )


@dp.message_handler(commands='add')
@auto_registration
async def add_record(message: types.Message):
    """Добавить новую запись"""
    new_text = message.html_text[len('/add'):].strip()
    record = user_record_module.schemas.Record(text=new_text)
    new_record = user_record_module.UserRecordModule(str(message.chat.id)).add(record)
    await bot.send_message(
        chat_id=message.chat.id,
        text=f'Запись успешно сохранена под номером: {new_record.id}',
    )


@dp.message_handler(commands='get')
@auto_registration
async def get_records(message: types.Message):
    """Показать все записи"""
    record_id = helpers.parse_record_id('/get', message.text)
    if record_id:
        record = user_record_module.UserRecordModule(str(message.chat.id)).find(record_id)
        await message.reply(f'Запись {record.id}\n'
                            f'{record.text}')
    else:
        records = user_record_module.UserRecordModule(str(message.chat.id)).find_all()
        text = '\n\n'.join([f'Запись {record.id}\n{record.text}' for record in records])
        await bot.send_message(chat_id=message.chat.id, text=text)


@dp.message_handler(commands='del')
@auto_registration
async def del_record(message: types.Message):
    """Удалить запись"""
    record_id = helpers.parse_record_id('/del', message.text)
    user_record_module.UserRecordModule(str(message.chat.id)).delete(record_id)
    await bot.send_message(chat_id=message.chat.id, text='Запись удалена')


@dp.message_handler(commands='hide')
@auto_registration
async def hide_all_message_history(message: types.Message, user: user_module.schemas.UserBotFromDB):
    """Спрятать все сообщения"""
    await user_module.MessageHistoryController(message).clear_history_bot()
    await bot.send_message(
        chat_id=user.chat_id,
        text=f'BOT ID: {user.chat_id}\n'
             f'USERNAME: {user.username}\n'
             'Доступные команды:\n'
             '/start - Главное меню\n'
             '/add - Добавить новую запись\n'
             '/get - Посмотреть все свои записи\n'
             '/get {Номер записи} - Посмотреть в отдельности\n'
             '/del {Номер записи} - Удалить запись\n'
             '/hide - отчистить историю сообщений\n',
    )


@dp.message_handler(regexp=bot_settings.SEARCH_TAGS_REGEX)
@auto_registration
async def search_by_tags(message: types.Message):
    """Поиск записей по тегам"""
    tags = helpers.parse_tags(message.text, bot_settings.SEARCH_TAGS_REGEX)
    records = user_record_module.UserRecordModule(str(message.chat.id)).find_all(
        text=[f'#{tag}' for tag in tags])
    if records:
        text = '\n\n'.join([f'Запись {record.id}\n{record.text}' for record in records])
    else:
        text = f'Записи по тегам {"# ".join(tags)} не найдены'
    await bot.send_message(chat_id=message.chat.id, text=text)


@dp.errors_handler(exception=BotBlocked)
async def disable_user(update: types.Update, _):
    """Пометить как неактивный пользователь"""
    user: db.TelegramUser = db.Session().query(db.TelegramUser).filter_by(telegram_id=str(update.message.chat.id)).one()
    user.inactive_at = datetime.now()
    db.Session().commit()
    return True


@dp.errors_handler(exception=user_record_module.exc.RecordNotFounded)
async def record_not_founded(update: types.Update, exc: user_record_module.exc.RecordNotFounded):
    """Запись не найдена."""
    await bot.send_message(chat_id=update.message.chat.id, text=exc.message)
    return True


@dp.errors_handler(exception=base_exc.WrongFormat)
async def wrong_format(update: types.Update, exc: base_exc.WrongFormat):
    """Запись не найдена."""
    await bot.send_message(chat_id=update.message.chat.id, text=exc.message)
    return True


@dp.errors_handler(exception=base_exc.CommandDoesNotExist)
async def unknown_command(update: types.Update, exc: base_exc.CommandDoesNotExist):
    """Неизвестная команда"""
    await bot.send_message(chat_id=update.message.chat.id, text=exc.message)
    return True


@dp.errors_handler(exception=base_exc.CommandNotFounded)
async def unknown_command(update: types.Update, exc: base_exc.CommandNotFounded):
    """Команда не найдена"""
    await bot.send_message(chat_id=update.message.chat.id, text=exc.message)
    return True
