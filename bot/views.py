from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def button(text, data): return InlineKeyboardButton(text=text, callback_data=data)
def keyboard(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
