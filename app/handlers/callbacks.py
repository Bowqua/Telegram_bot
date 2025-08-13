from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
from app.data.catalog import PRODUCTS, PRODUCTS_BY_ID
from aiogram.filters import Command, CommandObject, BaseFilter
from app.db.bootstrap import load_catalog_to_memory
import asyncio, shlex
from decimal import Decimal
from typing import List, Tuple
from aiogram.types import Message
from sqlalchemy import select, func
from app.db.session import Session
from app.db.models import Category, Stone, Product
from app.utils.slug import slugify_ru

REMOVE_ON_ZERO = True
SHOW_DELETE_BUTTON = False
SHOW_LABEL_ROW = False

load_dotenv()
router = Router()

USER_CTX = {}
CART = {}
DELIVERY_CTX = {}
INPUT_MODE = {}

ADMIN_IDS = {920975453, 6888030186}

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def norm_phone(s: str) -> str | None:
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits if 10 <= len(digits) <= 15 else None


def is_email(s: str) -> bool:
    return "@" in s and "." in s.split("@")[-1] and " " not in s


async def safe_edit(message, text, reply_markup):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


class WaitsInput(BaseFilter):
    async def __call__(self, m: Message) -> bool:
        return INPUT_MODE.get(m.from_user.id) is not None


async def _ru_labels(category_code: str, stone_code: str) -> tuple[str, str]:
    async with Session() as s:
        cat_ru = (await s.execute(
            select(Category.name_ru).where(Category.code == category_code)
        )).scalar_one_or_none() or category_code
        stone_ru = (await s.execute(
            select(Stone.name_ru).where(Stone.code == stone_code)
        )).scalar_one_or_none() or stone_code
    return cat_ru, stone_ru


async def create_or_get_category(session: Session, name_ru: str) -> Category:
    name_ru = name_ru.strip()
    c = (await session.execute(
        select(Category).where(func.lower(Category.name_ru) == func.lower(name_ru))
    )).scalar_one_or_none()

    if c:
        return c
    code = slugify_ru(name_ru)
    postfix = 1
    base = code

    while (await session.execute(select(Category).where(Category.code == code))).scalar_one_or_none():
        postfix += 1
        code = f"{base}-{postfix}"

    c = Category(code=code, name_ru=name_ru)
    session.add(c)
    await session.flush()
    return c


async def create_or_get_stone(session: Session, name_ru: str) -> Stone:
    name_ru = name_ru.strip()
    s = (await session.execute(
        select(Stone).where(func.lower(Stone.name_ru) == func.lower(name_ru))
    )).scalar_one_or_none()

    if s:
        return s
    code = slugify_ru(name_ru)
    postfix = 1
    base = code

    while (await session.execute(select(Stone).where(Stone.code == code))).scalar_one_or_none():
        postfix += 1
        code = f"{base}-{postfix}"

    s = Stone(code=code, name_ru=name_ru)
    session.add(s)
    await session.flush()
    return s


async def add_product_from_args(m: Message, args: str, photos: List[str] | None):
    if not is_admin(m.from_user.id):
        return
    try:
        parts = shlex.split(args)
    except ValueError:
        return await m.answer('Неверный синтаксис. Пример:\n'
                              '/add браслеты аметист "Браслет Морозный" 3990 4 "Описание (опционально)"')

    if len(parts) < 5:
        return await m.answer('Нужно минимум 5 параметров:\n'
                              '/add <категория> <камень> "<Название>" <цена> <шт> ["Описание"]')

    cat_ru, stone_ru = parts[0], parts[1]
    title = parts[2]
    try:
        price = int(Decimal(parts[3]))
        stock = int(parts[4])
    except Exception:
        return await m.answer("Цена/количество должны быть числами.")

    description = parts[5] if len(parts) > 5 else None
    photos = (photos or [])[:5]

    async with Session() as s:
        cat = await create_or_get_category(s, cat_ru)
        stn = await create_or_get_stone(s, stone_ru)

        exists = (await s.execute(
            select(Product.id)
            .where(Product.category_id == cat.id)
            .where(Product.stone_id == stn.id)
            .where(func.lower(Product.title) == func.lower(title))
        )).scalar_one_or_none()
        if exists:
            return await m.answer("Этот товар уже добавлен.")

        p = Product(
            title=title, price=price, stock=stock,
            category_id=cat.id, stone_id=stn.id,
            description=description, photos=photos
        )

        s.add(p)
        await s.commit()
        await s.refresh(p)
    await load_catalog_to_memory()

    await m.answer(
        f"Добавлено: #{p.id}\n"
        f"{cat.name_ru} / {stn.name_ru}\n"
        f"{p.title} — {p.price} ₽, {p.stock} шт."
        + (f"\nОписание: {description}" if description else "")
        + (f"\nФото: {len(photos)}" if photos else "")
    )


def delivery_back_only_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в корзину", callback_data="cart|open|")]
    ])


def keyboard_welcome():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Выбор ассортиментов", callback_data="catalog1|open|")],
        [InlineKeyboardButton(text="Связь с менеджером", callback_data="contacts|open|")]
    ])


def cart_count(user_id: int) -> int:
    return sum(CART.get(user_id, {}).values())

def clear_cart(user_id: int, restore_stock: bool = False):
    items = CART.get(user_id, {})
    if restore_stock:
        for pid, qty, in items.items():
            inc_stock(pid, qty)
    CART[user_id] = {}


def dec_stock(pid: int, n: int = 1) -> bool:
    p = PRODUCTS_BY_ID.get(pid)
    if not p or p["stock"] < n:
        return False
    p["stock"] -= n
    return True


def inc_stock(pid: int, n: int = 1) -> None:
    p = PRODUCTS_BY_ID.get(pid)
    if p:
        p["stock"] += n


def render_product_text(p: dict, pos: int, total: int, category: str, stone: str) -> str:
    return (
        f"<b>{p['title']}</b>\n"
        f"Категория: {category}\n"
        f"Камень: {stone}\n"
        f"Цена: {p['price']} ₽\n\n"
        f"В наличии: {p['stock']} шт\n\n"
        f"Товар {pos+1} из {total}"
    )


def product_keyboard(category: str, stone: str, product_id: int, user_id: int, pos: int, total: int):
    left_disabled = (pos == 0)
    right_disabled = (pos == total - 1)
    in_stock = PRODUCTS_BY_ID[product_id]["stock"] > 0

    row_nav = [
        InlineKeyboardButton(text="⬅️", callback_data="product|nav|prev") if not left_disabled
        else InlineKeyboardButton(text="🚫", callback_data="noop"),
        InlineKeyboardButton(
            text=("Приобрести" if in_stock else "Нет в наличии"),
            callback_data=(f"product|add|{product_id}" if in_stock else "noop")
        ),
        InlineKeyboardButton(text="➡️", callback_data="product|nav|next") if not right_disabled
        else InlineKeyboardButton(text="🚫", callback_data="noop"),
    ]
    row_cart = [InlineKeyboardButton(text=f"🧺 Корзина ({cart_count(user_id)})", callback_data="cart|open|")]
    row_back = [InlineKeyboardButton(text="⬅️ Назад к камням", callback_data=f"catalog2|open|{category}")]

    return InlineKeyboardMarkup(inline_keyboard=[row_nav, row_cart, row_back])


async def render_product_screen(cb: CallbackQuery, category: str, stone: str, idx: int):
    key = (category, stone)
    products = PRODUCTS.get(key, [])
    if not products:
        await cb.message.edit_text(
            "Пока нет товаров для выбранной комбинации",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к камням", callback_data=f"catalog2|open|{category}")]
            ])
        )
        return

    idx = max(0, min(idx, len(products) - 1))
    USER_CTX[cb.from_user.id] = {"key": key, "idx": idx}
    p = products[idx]

    cat_ru, stone_ru = await _ru_labels(category, stone)
    await safe_edit(
        cb.message,
        render_product_text(p, idx, len(products), cat_ru, stone_ru),  # ← ПЕРЕДАЁМ РУССКИЕ
        product_keyboard(category, stone, p["id"], cb.from_user.id, idx, len(products))
    )

@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    return await cb.answer()


@router.callback_query(F.data == "welcome|open|")
async def cb_welcome(cb: CallbackQuery):
    await cb.message.edit_text("👋 Добро пожаловать! Это черновик приветствия.\n\nВыберите действие ниже.",
                               reply_markup=keyboard_welcome())
    return await cb.answer()


@router.callback_query(F.data.startswith("contacts|"))
async def cb_contacts(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="welcome|open|")]
    ])
    await cb.message.edit_text("📲 Связь с менеджером:\nUsername with @\nПричины: обмен, кастом и т.д.",
                               reply_markup=kb)
    return await cb.answer()


@router.callback_query(F.data.startswith("catalog1|"))
async def cb_catalog1(cb: CallbackQuery):
    codes = sorted({cat for (cat, _stone) in PRODUCTS.keys()})
    if not codes:
        kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="welcome|open|")]
                ])
        await cb.message.edit_text("Пока нет категорий.", reply_markup=kb)
        return await cb.answer()

    async with Session() as s:
        rows = (await s.execute(
            select(Category.code, Category.name_ru).where(Category.code.in_(codes))
        )).all()
    labels = {code: name_ru for code, name_ru in rows}
    rows_kb = [[InlineKeyboardButton(text=labels.get(code, code),
                                     callback_data=f"catalog2|open|{code}")]
               for code in codes]
    rows_kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="welcome|open|")])
    await cb.message.edit_text("Выберите ассортимент:",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb))
    return await cb.answer()


@router.callback_query(F.data.startswith("catalog2|open|"))
async def cb_catalog2(cb: CallbackQuery):
    category = cb.data.split("|", 2)[-1]
    stones = sorted({stone for (cat, stone) in PRODUCTS.keys() if cat == category})

    if not stones:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog1|open|")]
        ])
        await cb.message.edit_text(f"Для категории «{category}» пока нет камней. Добавьте товары через админ-команды.",
                                   reply_markup=kb)
        return await cb.answer()

    async with Session() as s:
        rows = (await s.execute(
            select(Stone.code, Stone.name_ru).where(Stone.code.in_(stones))
        )).all()
    labels = {code: name_ru for code, name_ru in rows}
    rows_kb = [[InlineKeyboardButton(text=labels.get(st, st),
                                     callback_data=f"product|open|{category}:{st}")]
               for st in stones]
    rows_kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog1|open|")])
    await cb.message.edit_text("Выберите камень для категории:",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows_kb))
    return await cb.answer()


@router.callback_query(F.data.startswith("product|open|"))
async def cb_product_open(cb: CallbackQuery):
    try:
        category, stone = cb.data.split("|", 2)[-1].split(":", 1)
    except ValueError:
        category, stone = "bracelets", "amethyst"
    await render_product_screen(cb, category, stone, idx=0)
    return await cb.answer()


@router.callback_query(F.data == "product|nav|next")
async def cb_product_next(cb: CallbackQuery):
    ctx = USER_CTX.get(cb.from_user.id)
    if not ctx:
        await render_product_screen(cb, "bracelets", "amethyst", 0)
        return await cb.answer()
    category, stone = ctx["key"]
    await render_product_screen(cb, category, stone, ctx.get("idx", 0) + 1)
    return await cb.answer()


@router.callback_query(F.data == "product|nav|prev")
async def cb_product_prev(cb: CallbackQuery):
    ctx = USER_CTX.get(cb.from_user.id)
    if not ctx:
        await render_product_screen(cb, "bracelets", "amethyst", 0)
        return await cb.answer()
    category, stone = ctx["key"]
    await render_product_screen(cb, category, stone, ctx.get("idx", 0) - 1)
    return await cb.answer()


@router.callback_query(F.data.startswith("product|add|"))
async def cb_product_add(cb: CallbackQuery):
    pid = int(cb.data.split("|", 2)[-1])
    if not dec_stock(pid, 1):
        ctx = USER_CTX.get(cb.from_user.id)
        if ctx:
            category, stone = ctx["key"]
            await render_product_screen(cb, category, stone, ctx.get("idx", 0))
        return await cb.answer("Этого товара больше нет на складе")

    CART.setdefault(cb.from_user.id, {})
    CART[cb.from_user.id][pid] = CART[cb.from_user.id].get(pid, 0) + 1

    ctx = USER_CTX.get(cb.from_user.id)
    if ctx:
        category, stone = ctx["key"]
        await render_product_screen(cb, category, stone, ctx.get("idx", 0))
    return await cb.answer("Добавлено в корзину")


def money(x: int) -> str:
    return f"{x:,}".replace(",", " ") + " ₽"


def cart_totals(user_id: int):
    items = CART.get(user_id, {})
    total_qty, total_sum = 0, 0
    lines = []
    for pid, qty in items.items():
        p = PRODUCTS_BY_ID.get(pid)
        if not p:
            continue
        line_sum = p["price"] * qty
        total_qty += qty
        total_sum += line_sum
        lines.append((p, qty, line_sum))
    return lines, total_qty, total_sum


def short_title(title: str, max_len: int = 20) -> str:
    return title if len(title) < max_len else title[:max_len - 1] + "..."


def circ_num(n: int) -> str:
    circ = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    return circ[n - 1] if 1 <= n <= len(circ) else f"{n}."


def cart_keyboard(user_id: int, lines):
    rows = []
    for i, (p, qty, _) in enumerate(lines, start=1):
        if SHOW_LABEL_ROW:
            rows.append([InlineKeyboardButton(text=f"• {short_title(p['title'])}", callback_data="noop")])

        can_inc = PRODUCTS_BY_ID[p["id"]]["stock"] > 0
        row = [
            InlineKeyboardButton(text=circ_num(i), callback_data="noop"),
            InlineKeyboardButton(text="–",   callback_data=f"cart|dec|{p['id']}"),
            InlineKeyboardButton(text=f"x{qty}", callback_data="noop"),
            InlineKeyboardButton(
                text=("+" if can_inc else "🚫"),
                callback_data=(f"cart|inc|{p['id']}" if can_inc else "noop")
            ),
        ]

        if SHOW_DELETE_BUTTON:
            row.append(InlineKeyboardButton(text="Удалить", callback_data=f"cart|del|{p['id']}"))
        rows.append(row)

    rows.append([InlineKeyboardButton(text="Очистить", callback_data="cart|clear|")])
    rows.append([InlineKeyboardButton(text="Перейти к службе доставки", callback_data="delivery|open|")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к товарам", callback_data="product|nav|prev")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_cart(cb: CallbackQuery):
    items = CART.get(cb.from_user.id, {})
    if not items:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к товарам", callback_data="product|nav|prev")]
        ])
        await cb.message.edit_text("Корзина пуста.", reply_markup=kb)
        return

    lines, total_qty, total_sum = cart_totals(cb.from_user.id)
    text_lines = ["<b>Корзина</b>"]

    for p, qty, line_sum in lines:
        text_lines.append(f"• {p['title']} — x{qty} = {money(line_sum)}")
    text_lines.append(f"\nИтого: {total_qty} шт на сумму {money(total_sum)}")

    await safe_edit(cb.message, "\n".join(text_lines), cart_keyboard(cb.from_user.id, lines))


@router.callback_query(F.data == "cart|open|")
async def cb_cart_open(cb: CallbackQuery):
    await render_cart(cb)
    return await cb.answer()


@router.callback_query(F.data.startswith("cart|inc|"))
async def cb_cart_inc(cb: CallbackQuery):
    pid = int(cb.data.split("|", 2)[-1])
    p = PRODUCTS_BY_ID.get(pid)

    if not p or p["stock"] <= 0:
        return await cb.answer("Больше нет на складе")

    p["stock"] -= 1
    CART.setdefault(cb.from_user.id, {})
    CART[cb.from_user.id][pid] = CART[cb.from_user.id].get(pid, 0) + 1

    await render_cart(cb)
    return await cb.answer()


@router.callback_query(F.data.startswith("cart|dec|"))
async def cb_cart_dec(cb: CallbackQuery):
    pid = int(cb.data.split("|", 2)[-1])

    changed = False
    if cb.from_user.id in CART and pid in CART[cb.from_user.id]:
        cur = CART[cb.from_user.id][pid]
        if cur > 1:
            CART[cb.from_user.id][pid] = cur - 1
            inc_stock(pid, 1)
            changed = True
        elif cur == 1:
            if REMOVE_ON_ZERO:
                del CART[cb.from_user.id][pid]
                inc_stock(pid, 1)
                changed = True
            else:
                pass

    if changed:
        await render_cart(cb)
    else:
        await cb.answer("Этой позиции нет в корзине", show_alert=False)
    return await cb.answer()


@router.callback_query(F.data.startswith("cart|del|"))
async def cb_cart_del(cb: CallbackQuery):
    pid = int(cb.data.split("|", 2)[-1])
    qty = 0
    if cb.from_user.id in CART and pid in CART[cb.from_user.id]:
        qty = CART[cb.from_user.id][pid]
        del CART[cb.from_user.id][pid]
    if qty:
        inc_stock(pid, qty)
    await render_cart(cb)
    return await cb.answer("Удалено")


@router.callback_query(F.data == "cart|clear|")
async def cb_cart_clear(cb: CallbackQuery):
    items = CART.get(cb.from_user.id, {})
    for pid, qty in items.items():
        inc_stock(pid, qty)
    CART[cb.from_user.id] = {}
    await render_cart(cb)
    return await cb.answer("Очищено")


def carrier_label(code: str | None) -> str:
    mapping = {"cdek": "СДЭК", "yandex": "Яндекс Доставка", "post": "Почта России"}
    return mapping.get(code or "", "не выбрано")


def delivery_choose_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="СДЭК", callback_data="delivery|form|cdek")],
        [InlineKeyboardButton(text="Яндекс Доставка", callback_data="delivery|form|yandex")],
        [InlineKeyboardButton(text="Почта России", callback_data="delivery|form|post")],
        [InlineKeyboardButton(text="⬅️ Назад в корзину", callback_data="cart|open|")],
    ])


def delivery_form_text(user_id: int) -> str:
    ctx = DELIVERY_CTX.get(user_id, {})
    filled = bool(ctx.get("phone") and ctx.get("email") and ctx.get("address"))
    tail = "Теперь можно перейти к оплате." if filled else "Заполните недостающее и продолжите."
    return (
        "<b>Доставка</b>\n"
        f"Служба: {carrier_label(ctx.get('carrier'))}\n"
        f"Телефон: {ctx.get('phone') or '—'}\n"
        f"E‑mail: {ctx.get('email') or '—'}\n"
        f"Адрес / ПВЗ: {ctx.get('address') or '—'}\n\n"
        f"{tail}"
    )


def delivery_form_keyboard(user_id: int):
    ctx = DELIVERY_CTX.get(user_id, {})
    filled = bool(ctx.get("phone") and ctx.get("email") and ctx.get("address"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=("📱 Изменить телефон" if ctx.get("phone") else "📱 Ввести телефон"),
                              callback_data="delivery|ask_phone|")],
        [InlineKeyboardButton(text=("✉️ Изменить e‑mail"  if ctx.get("email") else "✉️ Ввести e‑mail"),
                              callback_data="delivery|ask_email|")],
        [InlineKeyboardButton(text=("🏷 Изменить адрес/ПВЗ" if ctx.get("address") else "🏷 Ввести адрес/ПВЗ"),
                              callback_data="delivery|ask_address|")],
        [InlineKeyboardButton(text=("Перейти к оплате" if filled else "Перейти к оплате — заполните данные"),
                              callback_data=("payment|start|current" if filled else "noop"))],
        [InlineKeyboardButton(text="⬅️ Сменить службу", callback_data="delivery|choose|")],
        [InlineKeyboardButton(text="⬅️ Назад в корзину", callback_data="cart|open|")],
    ])


@router.callback_query(F.data == "delivery|open|")
@router.callback_query(F.data == "delivery|choose|")
async def cb_delivery_choose(cb: CallbackQuery):
    INPUT_MODE[cb.from_user.id] = None
    DELIVERY_CTX.setdefault(cb.from_user.id, {"carrier": None, "phone": None, "email": None, "address": None})

    await safe_edit(cb.message, "Выберите службу доставки:", delivery_choose_keyboard())
    return await cb.answer()


@router.callback_query(F.data.startswith("delivery|form|"))
async def cb_delivery_form(cb: CallbackQuery):
    carrier = cb.data.split("|", 2)[-1]
    INPUT_MODE[cb.from_user.id] = None
    DELIVERY_CTX.setdefault(cb.from_user.id, {"carrier": None, "phone": None, "email": None, "address": None})
    DELIVERY_CTX[cb.from_user.id]["carrier"] = carrier

    await safe_edit(cb.message, delivery_form_text(cb.from_user.id), delivery_form_keyboard(cb.from_user.id))
    return await cb.answer(f"Выбрано: {carrier_label(carrier)}")


@router.callback_query(F.data == "delivery|ask_phone|")
async def cb_delivery_ask_phone(cb: CallbackQuery):
    INPUT_MODE[cb.from_user.id] = "phone"
    await safe_edit(cb.message, "📱 Отправьте телефон одним сообщением:", delivery_back_only_keyboard())
    return await cb.answer()


@router.callback_query(F.data == "delivery|ask_email|")
async def cb_delivery_ask_email(cb: CallbackQuery):
    INPUT_MODE[cb.from_user.id] = "email"
    await safe_edit(cb.message, "✉️ Отправьте Email одним сообщением:", delivery_back_only_keyboard())
    return await cb.answer()


@router.callback_query(F.data == "delivery|ask_address|")
async def cb_delivery_ask_address(cb: CallbackQuery):
    INPUT_MODE[cb.from_user.id] = "address"
    await safe_edit(cb.message, "🏷 Отправьте адрес или код ПВЗ (пока текстом).", delivery_back_only_keyboard())
    return await cb.answer()



@router.message(WaitsInput(), F.text)
async def on_text_input(m: Message):
    mode = INPUT_MODE.get(m.from_user.id)
    if not mode:
        return

    DELIVERY_CTX.setdefault(m.from_user.id, {"carrier": None, "phone": None, "email": None, "address": None})

    if mode == "phone":
        norm = norm_phone(m.text)
        if not norm:
            await m.reply("Не похоже на телефон. Пришлите ещё раз.")
            return
        DELIVERY_CTX[m.from_user.id]["phone"] = "+" + norm

    elif mode == "email":
        if not is_email(m.text.strip()):
            await m.reply("Не похоже на Email. Пришлите ещё раз.")
            return
        DELIVERY_CTX[m.from_user.id]["email"] = m.text.strip()

    elif mode == "address":
        DELIVERY_CTX[m.from_user.id]["address"] = m.text.strip()

    INPUT_MODE[m.from_user.id] = None
    await m.answer(delivery_form_text(m.from_user.id), reply_markup=delivery_form_keyboard(m.from_user.id))


@router.callback_query(F.data == "payment|start|current")
async def cb_payment_start(cb: CallbackQuery):
    ctx = DELIVERY_CTX.get(cb.from_user.id) or {}
    if not (ctx.get("carrier") and ctx.get("phone") and ctx.get("email") and ctx.get("address")):
        return await cb.answer("Заполните все данные доставки", show_alert=True)

    await safe_edit(cb.message,
                    "💳 (Заглушка) Оплата: здесь будет выставление счёта.",
                    InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Провести оплату", callback_data="payment|mock_success|")],
                        [InlineKeyboardButton(text="⬅️ Назад к доставке", callback_data="delivery|open|")],
                    ]))
    return await cb.answer()


@router.callback_query(F.data == "payment|mock_success|")
async def cb_payment_mock_success(cb: CallbackQuery):
    clear_cart(cb.from_user.id, restore_stock=True) # !!!!!!! в бизнесе нужно поставить на False
    await safe_edit(cb.message, "🎉 Спасибо за покупку! Сейчас будет выдан трек.",
                    InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ В каталог", callback_data="catalog1|open|")],
                        [InlineKeyboardButton(text="🧺 Корзина", callback_data="cart|open|")],
                    ]))
    return await cb.answer()


def _next_product_id() -> int:
    return (max(PRODUCTS_BY_ID.keys()) + 1) if PRODUCTS_BY_ID else 100


def add_product_mem(category: str, stone: str, title: str, price: int, stock: int) -> int:
    pid = _next_product_id()
    p = {"id": pid, "title": title.strip(), "price": int(price), "stock": int(stock)}
    PRODUCTS.setdefault((category, stone), []).append(p)
    PRODUCTS_BY_ID[pid] = p
    return pid


def delete_product_mem(pid: int) -> bool:
    if pid not in PRODUCTS_BY_ID:
        return False
    del PRODUCTS_BY_ID[pid]
    to_del_keys = []
    for key, items in PRODUCTS.items():
        new_items = [it for it in items if it["id"] != pid]
        if len(new_items) != len(items):
            PRODUCTS[key] = new_items
        if not new_items:
            to_del_keys.append(key)
    for k in to_del_keys:
        del PRODUCTS[k]
    if "CART" in globals():
        for uid in list(CART.keys()):
            CART[uid].pop(pid, None)
            if not CART[uid]:
                CART[uid] = {}
    return True


def set_stock_mem(pid: int, qty: int) -> bool:
    p = PRODUCTS_BY_ID.get(pid)
    if not p:
        return False
    p["stock"] = max(0, int(qty))
    return True


@router.message(Command("admin"))
async def cmd_admin_help(m: Message):
    if not is_admin(m.from_user.id):
        return await m.answer("⛔ Нет доступа.")
    await m.answer(
        "Админ-команды:\n"
        "<code>/add &lt;категория&gt; &lt;камень&gt; \"Название\" &lt;цена&gt; &lt;остаток&gt; [\"Описание\"]</code>\n"
        "<code>/del &lt;id&gt;</code>\n"
        "<code>/set &lt;id&gt; &lt;кол-во&gt;</code>\n"
        "<code>/list</code>\n\n"
        "Примеры:\n"
        "<code>/add браслеты аметист \"Браслет Морозный\" 3990 4 \"Серебро 925\"</code>\n"
        "<code>/del 42</code>\n"
        "<code>/set 42 7</code>"
    )


@router.message(Command("add"))
async def admin_add_text(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    await add_product_from_args(m, command.args or "", photos=[])


@router.message(Command("del"))
async def admin_del(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    if not command.args:
        return await m.answer("Формат: /del <id>")

    try:
        pid = int(command.args.strip())
    except ValueError:
        return await m.answer("ID должен быть числом.")

    async with Session() as s:
        p = await s.get(Product, pid)
        if not p:
            return await m.answer("Товар не найден.")
        await s.delete(p)
        await s.commit()
        await load_catalog_to_memory()

    await m.answer(f"Удалено: #{pid}")


@router.message(Command("set"))
async def admin_set(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    if not command.args:
        return await m.answer("Формат: /set <id> <stock>  (например: /set 42 7)")

    parts = command.args.split()
    if len(parts) != 2:
        return await m.answer("Формат: /set <id> <stock>")

    try:
        pid = int(parts[0]); new_stock = int(parts[1])
    except ValueError:
        return await m.answer("ID и stock должны быть числами.")

    async with Session() as s:
        p = await s.get(Product, pid)
        if not p:
            return await m.answer("Товар не найден.")
        p.stock = new_stock
        await s.commit()
        await load_catalog_to_memory()

    await m.answer(f"#{pid}: новое количество = {new_stock}")



@router.message(Command("list"))
async def admin_list(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    async with Session() as s:
        rows = (await s.execute(
            select(Product, Category.name_ru, Stone.name_ru)
            .join(Category, Product.category_id == Category.id)
            .join(Stone, Product.stone_id == Stone.id)
            .order_by(Product.id.desc())
            .limit(30)
        )).all()
    if not rows:
        return await m.answer("Список пуст.")
    lines = []
    for p, cat_ru, stone_ru in rows:
        lines.append(f"#{p.id} • {cat_ru} / {stone_ru}\n{p.title} — {p.price} ₽, {p.stock} шт.")
    await m.answer("\n\n".join(lines))


@router.message(F.photo)
async def admin_add_single_photo(m: Message):
    if not is_admin(m.from_user.id):
        return
    if not m.caption or not m.caption.lstrip().startswith("/add"):
        return
    args = m.caption.lstrip()[len("/add"):].strip()
    photos = [m.photo[-1].file_id]
    await add_product_from_args(m, args, photos)


ALBUM_BUF: dict[Tuple[int, str], dict] = {}

async def _flush_album(key: Tuple[int, str]):
    await asyncio.sleep(1.0)
    buf = ALBUM_BUF.pop(key, None)
    if not buf:
        return
    m0: Message = buf["first_msg"]
    caption: str | None = buf.get("caption")
    photos: list[str] = buf.get("photos", [])[:5]
    if not caption or not caption.lstrip().startswith("/add"):
        return
    args = caption.lstrip()[len("/add"):].strip()
    await add_product_from_args(m0, args, photos)


@router.message(F.media_group_id & F.photo)
async def admin_add_album(m: Message):
    if not is_admin(m.from_user.id):
        return
    key = (m.chat.id, m.media_group_id)
    buf = ALBUM_BUF.get(key)
    if not buf:
        buf = ALBUM_BUF[key] = {"photos": [], "caption": None, "first_msg": m}
        asyncio.create_task(_flush_album(key))
    buf["photos"].append(m.photo[-1].file_id)
    if m.caption and m.caption.lstrip().startswith("/add"):
        buf["caption"] = m.caption
