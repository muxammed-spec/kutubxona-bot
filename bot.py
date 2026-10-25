import asyncio
import logging
import os
import sqlite3

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
DB_PATH = "library.db"
PAGE_SIZE = 8

LANGS = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

TEXTS = {
    "uz": {
        "welcome": "📚 Kutubxonaga xush kelibsiz!\nKategoriyani tanlang:",
        "no_books": "Bu tilda hozircha kitoblar yo'q.",
        "books_in": "📂 {cat} — kitoblar:",
        "back": "⬅️ Orqaga",
        "prev": "◀️",
        "next": "▶️",
        "lang_set": "✅ Til tanlandi: {lang}",
        "author": "Muallif",
    },
    "ru": {
        "welcome": "📚 Добро пожаловать в библиотеку!\nВыберите категорию:",
        "no_books": "На этом языке пока нет книг.",
        "books_in": "📂 {cat} — книги:",
        "back": "⬅️ Назад",
        "prev": "◀️",
        "next": "▶️",
        "lang_set": "✅ Язык выбран: {lang}",
        "author": "Автор",
    },
    "en": {
        "welcome": "📚 Welcome to the library!\nChoose a category:",
        "no_books": "There are no books in this language yet.",
        "books_in": "📂 {cat} — books:",
        "back": "⬅️ Back",
        "prev": "◀️",
        "next": "▶️",
        "lang_set": "✅ Language selected: {lang}",
        "author": "Author",
    },
}

# ---------------------------------------------------------------- DB
def q(sql, args=(), one=False, commit=False):
    with sqlite3.connect(DB_PATH) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute(sql, args)
        if commit:
            con.commit()
            return cur.lastrowid
        return cur.fetchone() if one else cur.fetchall()


def init_db():
    q("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, lang TEXT)", commit=True)
    q("CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL)", commit=True)
    q(
        """CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            description TEXT,
            lang TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            file_id TEXT NOT NULL
        )""",
        commit=True,
    )


def get_lang(uid):
    row = q("SELECT lang FROM users WHERE id=?", (uid,), one=True)
    return row["lang"] if row and row["lang"] in TEXTS else None


def set_lang(uid, lang):
    q("INSERT INTO users (id, lang) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET lang=excluded.lang",
      (uid, lang), commit=True)


# ---------------------------------------------------------------- Keyboards
def lang_kb(prefix="lang"):
    kb = InlineKeyboardBuilder()
    for code, name in LANGS.items():
        kb.button(text=name, callback_data=f"{prefix}:{code}")
    kb.adjust(1)
    return kb.as_markup()


def categories_kb(lang):
    rows = q(
        """SELECT c.id, c.name, COUNT(b.id) AS n FROM categories c
           JOIN books b ON b.category_id=c.id AND b.lang=?
           GROUP BY c.id ORDER BY c.name""",
        (lang,),
    )
    if not rows:
        return None
    kb = InlineKeyboardBuilder()
    for r in rows:
        kb.button(text=f"{r['name']} ({r['n']})", callback_data=f"cat:{r['id']}:0")
    kb.adjust(1)
    return kb.as_markup()


# ---------------------------------------------------------------- User router
user = Router()


async def show_menu(target, lang, edit=False):
    t = TEXTS[lang]
    kb = categories_kb(lang)
    text = t["welcome"] if kb else t["no_books"]
    if edit:
        await target.edit_text(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@user.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🌐 Tilni tanlang / Выберите язык / Choose language:", reply_markup=lang_kb())


@user.message(Command("language"))
async def change_lang(m: Message):
    await m.answer("🌐 Tilni tanlang / Выберите язык / Choose language:", reply_markup=lang_kb())


@user.callback_query(F.data.startswith("lang:"))
async def cb_lang(cb: CallbackQuery):
    lang = cb.data.split(":")[1]
    if lang not in TEXTS:
        return await cb.answer()
    set_lang(cb.from_user.id, lang)
    await cb.message.edit_text(TEXTS[lang]["lang_set"].format(lang=LANGS[lang]))
    await show_menu(cb.message, lang)
    await cb.answer()


@user.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    lang = get_lang(cb.from_user.id)
    if not lang:
        await cb.message.edit_text("🌐 Tilni tanlang / Выберите язык / Choose language:", reply_markup=lang_kb())
    else:
        await show_menu(cb.message, lang, edit=True)
    await cb.answer()


@user.callback_query(F.data.startswith("cat:"))
async def cb_category(cb: CallbackQuery):
    lang = get_lang(cb.from_user.id)
    if not lang:
        await cb.message.edit_text("🌐 Tilni tanlang / Выберите язык / Choose language:", reply_markup=lang_kb())
        return await cb.answer()
    t = TEXTS[lang]
    _, cid, page = cb.data.split(":")
    cid, page = int(cid), int(page)

    cat = q("SELECT name FROM categories WHERE id=?", (cid,), one=True)
    if not cat:
        return await cb.answer("—")
    total = q("SELECT COUNT(*) AS n FROM books WHERE category_id=? AND lang=?", (cid, lang), one=True)["n"]
    books = q(
        "SELECT id, title, author FROM books WHERE category_id=? AND lang=? ORDER BY title LIMIT ? OFFSET ?",
        (cid, lang, PAGE_SIZE, page * PAGE_SIZE),
    )

    kb = InlineKeyboardBuilder()
    for b in books:
        label = b["title"] + (f" — {b['author']}" if b["author"] else "")
        kb.button(text=label[:60], callback_data=f"book:{b['id']}")
    kb.adjust(1)

    nav = InlineKeyboardBuilder()
    if page > 0:
        nav.button(text=t["prev"], callback_data=f"cat:{cid}:{page - 1}")
    if (page + 1) * PAGE_SIZE < total:
        nav.button(text=t["next"], callback_data=f"cat:{cid}:{page + 1}")
    if nav.buttons:
        kb.attach(nav)
    back = InlineKeyboardBuilder()
    back.button(text=t["back"], callback_data="menu")
    kb.attach(back)

    await cb.message.edit_text(t["books_in"].format(cat=cat["name"]), reply_markup=kb.as_markup())
    await cb.answer()


@user.callback_query(F.data.startswith("book:"))
async def cb_book(cb: CallbackQuery):
    lang = get_lang(cb.from_user.id) or "uz"
    book = q("SELECT * FROM books WHERE id=?", (int(cb.data.split(":")[1]),), one=True)
    if not book:
        return await cb.answer("—")
    caption = f"📖 {book['title']}"
    if book["author"]:
        caption += f"\n✍️ {TEXTS[lang]['author']}: {book['author']}"
    if book["description"]:
        caption += f"\n\n{book['description']}"
    await cb.message.answer_document(book["file_id"], caption=caption[:1024])
    await cb.answer()


# ---------------------------------------------------------------- Admin router
admin = Router()
admin.message.filter(F.from_user.id.in_(ADMINS))
admin.callback_query.filter(F.from_user.id.in_(ADMINS))


class AddBook(StatesGroup):
    title = State()
    author = State()
    description = State()
    lang = State()
    category = State()
    file = State()


@admin.message(Command("admin"))
async def admin_help(m: Message):
    await m.answer(
        "🔧 Admin buyruqlari:\n\n"
        "/addcategory Nomi — kategoriya qo'shish\n"
        "/categories — kategoriyalar ro'yxati\n"
        "/delcategory ID — kategoriyani o'chirish (ichida kitob bo'lmasa)\n"
        "/addbook — kitob qo'shish\n"
        "/books — kitoblar ro'yxati\n"
        "/delbook ID — kitobni o'chirish\n"
        "/cancel — bekor qilish"
    )


@admin.message(Command("addcategory"))
async def add_category(m: Message):
    name = (m.text or "").partition(" ")[2].strip()
    if not name:
        return await m.answer("Foydalanish: /addcategory Badiiy adabiyot")
    try:
        q("INSERT INTO categories (name) VALUES (?)", (name,), commit=True)
    except sqlite3.IntegrityError:
        return await m.answer("⚠️ Bunday kategoriya bor.")
    await m.answer(f"✅ Kategoriya qo'shildi: {name}")


@admin.message(Command("categories"))
async def list_categories(m: Message):
    rows = q("SELECT id, name FROM categories ORDER BY id")
    if not rows:
        return await m.answer("Kategoriyalar yo'q. /addcategory Nomi")
    await m.answer("\n".join(f"{r['id']}. {r['name']}" for r in rows))


@admin.message(Command("delcategory"))
async def del_category(m: Message):
    arg = (m.text or "").partition(" ")[2].strip()
    if not arg.isdigit():
        return await m.answer("Foydalanish: /delcategory ID")
    n = q("SELECT COUNT(*) AS n FROM books WHERE category_id=?", (int(arg),), one=True)["n"]
    if n:
        return await m.answer(f"⚠️ Bu kategoriyada {n} ta kitob bor. Avval kitoblarni o'chiring.")
    q("DELETE FROM categories WHERE id=?", (int(arg),), commit=True)
    await m.answer("🗑 O'chirildi.")


@admin.message(Command("books"))
async def list_books(m: Message):
    rows = q("SELECT id, title, lang FROM books ORDER BY id DESC LIMIT 50")
    if not rows:
        return await m.answer("Kitoblar yo'q. /addbook")
    await m.answer("\n".join(f"{r['id']}. [{r['lang']}] {r['title']}" for r in rows))


@admin.message(Command("delbook"))
async def del_book(m: Message):
    arg = (m.text or "").partition(" ")[2].strip()
    if not arg.isdigit():
        return await m.answer("Foydalanish: /delbook ID")
    q("DELETE FROM books WHERE id=?", (int(arg),), commit=True)
    await m.answer("🗑 O'chirildi.")


@admin.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi.")


@admin.message(Command("addbook"))
async def addbook_start(m: Message, state: FSMContext):
    if not q("SELECT 1 FROM categories LIMIT 1", one=True):
        return await m.answer("Avval kategoriya qo'shing: /addcategory Nomi")
    await state.set_state(AddBook.title)
    await m.answer("📖 Kitob nomini yuboring:")


@admin.message(AddBook.title, F.text)
async def ab_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip())
    await state.set_state(AddBook.author)
    await m.answer("✍️ Muallifi (yo'q bo'lsa — yuboring):")


@admin.message(AddBook.author, F.text)
async def ab_author(m: Message, state: FSMContext):
    author = m.text.strip()
    await state.update_data(author="" if author == "-" else author)
    await state.set_state(AddBook.description)
    await m.answer("📝 Qisqa tavsif (yo'q bo'lsa — yuboring):")


@admin.message(AddBook.description, F.text)
async def ab_desc(m: Message, state: FSMContext):
    desc = m.text.strip()
    await state.update_data(description="" if desc == "-" else desc)
    await state.set_state(AddBook.lang)
    await m.answer("🌐 Kitob tili:", reply_markup=lang_kb("addlang"))


@admin.callback_query(AddBook.lang, F.data.startswith("addlang:"))
async def ab_lang(cb: CallbackQuery, state: FSMContext):
    await state.update_data(lang=cb.data.split(":")[1])
    kb = InlineKeyboardBuilder()
    for c in q("SELECT id, name FROM categories ORDER BY name"):
        kb.button(text=c["name"], callback_data=f"addcat:{c['id']}")
    kb.adjust(1)
    await state.set_state(AddBook.category)
    await cb.message.edit_text("📂 Kategoriyani tanlang:", reply_markup=kb.as_markup())
    await cb.answer()


@admin.callback_query(AddBook.category, F.data.startswith("addcat:"))
async def ab_cat(cb: CallbackQuery, state: FSMContext):
    await state.update_data(category_id=int(cb.data.split(":")[1]))
    await state.set_state(AddBook.file)
    await cb.message.edit_text("📎 Endi kitob faylini yuboring (PDF, EPUB, DOCX...):")
    await cb.answer()


@admin.message(AddBook.file, F.document)
async def ab_file(m: Message, state: FSMContext):
    d = await state.get_data()
    q(
        "INSERT INTO books (title, author, description, lang, category_id, file_id) VALUES (?,?,?,?,?,?)",
        (d["title"], d["author"], d["description"], d["lang"], d["category_id"], m.document.file_id),
        commit=True,
    )
    await state.clear()
    await m.answer(f"✅ Kitob qo'shildi: {d['title']} [{d['lang']}]")


# ---------------------------------------------------------------- Run
async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi. .env faylini to'ldiring.")
    init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin)
    dp.include_router(user)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
