import re
import asyncio, shlex
import time

from aiogram import F, Router, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery, Message, InputMediaPhoto
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

from app.data import catalog
from app.data.catalog import PRODUCTS, PRODUCTS_BY_ID, CAT_LABELS, STONE_LABELS
from aiogram.filters import Command, CommandObject, BaseFilter
from app.db.bootstrap import cache_delete_product, cache_refresh_single, load_catalog_to_memory, cleanup_orphan_refs
from decimal import Decimal
from sqlalchemy import select, func, delete, or_
from app.db.session import Session
from app.db.models import Category, Stone, Product, Order, OrderItem, OrderStatus
from app.utils.slug import slugify_ru
from contextlib import suppress
from typing import Dict, List
from app.config import settings

REMOVE_ON_ZERO = True
SHOW_DELETE_BUTTON = False
SHOW_LABEL_ROW = False
DELETE_PRODUCT_WHEN_STOCK_ZERO = True

load_dotenv()
router = Router()

USER_CTX = {}
CART = {}
CART_TTL_SEC = 60 * 60 * 12
CART_META: dict[int, float] = {}
DELIVERY_CTX = {}
INPUT_MODE = {}

album_buffers: Dict[str, dict] = {}
ALBUM_SETTLE_SEC = 0.9

ADMINS_IDS=set(settings.ADMIN_IDS)

def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


def norm_phone(s: str) -> str | None:
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits if 10 <= len(digits) <= 15 else None


def is_email(s: str) -> bool:
    return "@" in s and "." in s.split("@")[-1] and " " not in s


async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        s = str(e)
        if "message is not modified" in s:
            return
        with suppress(Exception):
            new = await message.answer(text, reply_markup=reply_markup)
            await message.delete()


def uc_first(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def touch_cart(user_id: int) -> None:
    CART_META[user_id] = time.time()


def purge_expired_carts() -> None:
    now = time.time()
    stale = [uid for uid, ts in CART_META.items() if now - ts > CART_TTL_SEC]
    for uid in stale:
        for pid, qty in (CART.get(uid, {}) or {}).items():
            inc_stock(pid, qty)

        CART.pop(uid, None)
        DELIVERY_CTX.pop(uid, None)
        USER_CTX.pop(uid, None)
        CART_META.pop(uid, None)


class WaitsInput(BaseFilter):
    async def __call__(self, m: Message) -> bool:
        return INPUT_MODE.get(m.from_user.id) is not None


def ru_labels(category_code: str, stone_code: str) -> tuple[str, str]:
    return uc_first(CAT_LABELS.get(category_code, category_code)), uc_first(STONE_LABELS.get(stone_code, stone_code))


async def get_or_create_category(session: Session, name_ru: str) -> Category:
    name_ru = name_ru.strip()
    code = slugify_ru(name_ru)
    row = (await session.execute(select(Category).where((Category.code == code) | (Category.name_ru == name_ru)))).scalar_one_or_none()

    if row:
        return row

    row = Category(code=code, name_ru=name_ru)
    session.add(row)
    await session.flush()
    return row


async def get_or_create_stone(session: Session, name_ru: str) -> Stone:
    name_ru = name_ru.strip()
    code = slugify_ru(name_ru)
    row = (await session.execute(select(Stone).where((Stone.code == code) | (Stone.name_ru == name_ru)))).scalar_one_or_none()

    if row:
        return row

    row = Stone(code=code, name_ru=name_ru)
    session.add(row)
    await session.flush()
    return row


async def find_category_by_term(session: Session, term: str) -> Category | None:
    term = term.strip()
    if not term:
        return None
    code = slugify_ru(term)
    return (
        await session.execute(
            select(Category).where(
                or_(
                    func.lower(Category.name_ru) == func.lower(term),
                    Category.code == code,
                )
            )
        )
    ).scalar_one_or_none()


async def find_stone_by_term(session: Session, term: str) -> Stone | None:
    term = term.strip()
    if not term:
        return None
    code = slugify_ru(term)
    return (
        await session.execute(
            select(Stone).where(
                or_(
                    func.lower(Stone.name_ru) == func.lower(term),
                    Stone.code == code,
                )
            )
        )
    ).scalar_one_or_none()


async def add_product_from_args(m: Message, args: str, photos: List[str] | None):
    if not is_admin(m.from_user.id):
        return
    try:
        args = re.sub(r"[«»“”]", '"', args)
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
        cat = await get_or_create_category(s, cat_ru)
        stn = await get_or_create_stone(s, stone_ru)

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
        await cache_refresh_single(s, p.id)

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
    lines = [
        f"<b>{p['title']}</b>",
        f"Категория: {category}",
        f"Камень: {stone}",
        f"Цена: {p['price']} ₽",
    ]

    desc = (p.get("description") or "").strip()
    if desc:
        lines += ["", "<b>Описание:</b>", desc]

    lines += ["", f"В наличии: {p['stock']} шт", "", f"Товар {pos+1} из {total}"]
    return "\n".join(lines)


def product_keyboard(
    category: str, stone: str,
    product_id: int, user_id: int,
    pos: int, total: int,
    img_idx: int = 0
):
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

    rows = []

    photos = (PRODUCTS_BY_ID.get(product_id, {}).get("photos") or [])
    if len(photos) > 1:
        rows.append([
            InlineKeyboardButton(text="◀️", callback_data=f"pimg|prev|{category}:{stone}:{pos}:{img_idx}"),
            InlineKeyboardButton(text=f"{(img_idx % len(photos)) + 1}/{len(photos)}", callback_data="noop"),
            InlineKeyboardButton(text="▶️", callback_data=f"pimg|next|{category}:{stone}:{pos}:{img_idx}"),
        ])

    rows += [row_nav, row_cart, row_back]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_product_screen(cb: CallbackQuery, category: str, stone: str, idx: int):
    key = (category, stone)
    products = PRODUCTS.get(key, [])
    if not products:
        await safe_edit(cb.message,
            "Пока нет товаров для выбранной комбинации",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к камням", callback_data=f"catalog2|open|{category}")]
            ])
        )
        return

    idx = max(0, min(idx, len(products) - 1))
    prev = USER_CTX.get(cb.from_user.id) or {}
    img_idx = prev.get("img_idx", 0) if prev.get("key") == key and prev.get("idx") == idx else 0

    USER_CTX[cb.from_user.id] = {"key": key, "idx": idx, "img_idx": img_idx}
    p = products[idx]

    await show_product(cb, p, idx, len(products), category, stone, img_idx=img_idx)


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    return await cb.answer()


@router.callback_query(F.data == "welcome|open|")
async def cb_welcome(cb: CallbackQuery):
    await safe_edit(cb.message, "👋 Добро пожаловать! Это черновик приветствия.\n\nВыберите действие ниже.",
                               reply_markup=keyboard_welcome())
    return await cb.answer()


@router.callback_query(F.data.startswith("contacts|"))
async def cb_contacts(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="welcome|open|")]
    ])
    await safe_edit(cb.message, "📲 Связь с менеджером:\nUsername with @\nПричины: обмен, кастом и т.д.",
                               reply_markup=kb)
    return await cb.answer()


@router.callback_query(F.data.startswith("catalog1|"))
async def cb_catalog1(cb: CallbackQuery):
    codes = sorted({cat for (cat, _stone) in PRODUCTS.keys()})
    if not codes:
        kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="welcome|open|")]
                ])
        await safe_edit(cb.message, "Пока нет категорий.", reply_markup=kb)
        return await cb.answer()

    async with Session() as s:
        rows = (await s.execute(
            select(Category.code, Category.name_ru).where(Category.code.in_(codes))
        )).all()

    labels = {code: name_ru for code, name_ru in rows}
    rows_kb = [[InlineKeyboardButton(text=uc_first(labels.get(code, CAT_LABELS.get(code, code))),
                                     callback_data=f"catalog2|open|{code}")]
               for code in codes]
    rows_kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="welcome|open|")])
    await safe_edit(cb.message, "Выберите ассортимент:",
                    InlineKeyboardMarkup(inline_keyboard=rows_kb))
    return await cb.answer()


@router.callback_query(F.data.startswith("catalog2|open|"))
async def cb_catalog2(cb: CallbackQuery):
    category = cb.data.split("|", 2)[-1]
    stones = sorted({stone for (cat, stone) in PRODUCTS.keys() if cat == category})

    if not stones:
        await safe_edit(cb.message,
                        f"Для категории «{category}» пока нет камней.",
                        InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog1|open|")]
                        ]))
        return await cb.answer()

    async with Session() as s:
        rows = (await s.execute(
            select(Stone.code, Stone.name_ru).where(Stone.code.in_(stones))
        )).all()

    labels = {code: name_ru for code, name_ru in rows}
    rows_kb = [[InlineKeyboardButton(text=uc_first(labels.get(st, STONE_LABELS.get(st, st))),
                                     callback_data=f"product|open|{category}:{st}")]
               for st in stones]
    rows_kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog1|open|")])
    await safe_edit(cb.message,"Выберите камень для категории:", InlineKeyboardMarkup(inline_keyboard=rows_kb))
    return await cb.answer()


@router.callback_query(F.data.startswith("product|open|"))
async def cb_product_open(cb: CallbackQuery):
    payload = cb.data.split("|", 2)[-1]
    if ":" not in payload:
        if PRODUCTS:
            category, stone = next(iter(PRODUCTS.keys()))
        else:
            await safe_edit(
                cb.message,
                "Каталог пуст.",
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="catalog1|open|")]
                ])
            )
            return await cb.answer()
    else:
        category, stone = payload.split(":", 1)

    await render_product_screen(cb, category, stone, idx=0)
    return await cb.answer()

@router.callback_query(F.data == "product|nav|next")
async def cb_product_next(cb: CallbackQuery):
    ctx = USER_CTX.get(cb.from_user.id)
    if not ctx:
        await safe_edit(
            cb.message,
            "Выберите ассортимент:",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Открыть каталог", callback_data="catalog1|open|")]
            ])
        )
        return await cb.answer()

    category, stone = ctx["key"]
    await render_product_screen(cb, category, stone, ctx.get("idx", 0) + 1)
    return await cb.answer()


@router.callback_query(F.data == "product|nav|prev")
async def cb_product_prev(cb: CallbackQuery):
    ctx = USER_CTX.get(cb.from_user.id)
    if not ctx:
        await safe_edit(
            cb.message,
            "Выберите ассортимент:",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Открыть каталог", callback_data="catalog1|open|")]
            ])
        )
        return await cb.answer()

    category, stone = ctx["key"]
    await render_product_screen(cb, category, stone, ctx.get("idx", 0) - 1)
    return await cb.answer()


@router.callback_query(F.data.startswith("product|add|"))
async def cb_product_add(cb: CallbackQuery):
    purge_expired_carts()
    pid = int(cb.data.split("|", 2)[-1])
    if not dec_stock(pid, 1):
        ctx = USER_CTX.get(cb.from_user.id)
        if ctx:
            category, stone = ctx["key"]
            await render_product_screen(cb, category, stone, ctx.get("idx", 0))
        return await cb.answer("Этого товара больше нет на складе")

    CART.setdefault(cb.from_user.id, {})
    CART[cb.from_user.id][pid] = CART[cb.from_user.id].get(pid, 0) + 1
    touch_cart(cb.from_user.id)

    ctx = USER_CTX.get(cb.from_user.id)
    if ctx:
        category, stone = ctx["key"]
        await render_product_screen(cb, category, stone, ctx.get("idx", 0))
    return await cb.answer("Добавлено в корзину")


@router.callback_query(F.data.startswith("pimg|"))
async def cb_photo_nav(cb: CallbackQuery):
    try:
        _p, action, payload = cb.data.split("|", 2)
        category, stone, idx_s, img_idx_s = payload.split(":", 3)
        idx = int(idx_s); img_idx = int(img_idx_s)
    except Exception:
        return await cb.answer("Ошибка параметров.", show_alert=True)

    key = (category, stone)
    products = PRODUCTS.get(key, [])
    if not products:
        return await cb.answer("Нет товаров.", show_alert=True)
    if not (0 <= idx < len(products)):
        return await cb.answer("Нет такого товара.", show_alert=True)

    p = products[idx]
    photos = p.get("photos") or []
    if len(photos) < 2:
        return await cb.answer("Здесь только одно фото.", show_alert=True)

    if action == "prev":
        img_idx = (img_idx - 1) % len(photos)
    else:
        img_idx = (img_idx + 1) % len(photos)

    USER_CTX[cb.from_user.id] = {"key": key, "idx": idx, "img_idx": img_idx}

    await show_product(cb, p, idx, len(products), category, stone, img_idx=img_idx)
    return await cb.answer()


def cart_photo_kb(pid: int, idx: int, total: int):
    back = InlineKeyboardButton(text="⬅️ Вернуться в корзину", callback_data="cart|open|")

    if total <= 1:
        return InlineKeyboardMarkup(inline_keyboard=[[back]])

    left  = InlineKeyboardButton(text="◀️", callback_data=f"cartimg|nav|prev|{pid}:{idx}")
    mid   = InlineKeyboardButton(text=f"{(idx % total)+1}/{total}", callback_data="noop")
    right = InlineKeyboardButton(text="▶️", callback_data=f"cartimg|nav|next|{pid}:{idx}")

    return InlineKeyboardMarkup(inline_keyboard=[[left, mid, right], [back]])


async def render_cart_photo(cb: CallbackQuery, pid: int, idx: int):
    p = PRODUCTS_BY_ID.get(pid)
    if not p:
        return await cb.answer("Товар не найден.", show_alert=True)
    photos = p.get("photos") or []
    if not photos:
        return await cb.answer("У товара нет фото.", show_alert=True)

    idx = idx % len(photos)
    fid = photos[idx]
    caption = f"📷 {p['title']}\nФото {idx+1} из {len(photos)}"
    kb = cart_photo_kb(pid, idx, len(photos))
    media = InputMediaPhoto(media=fid, caption=caption)

    if cb.message.content_type == "photo":
        try:
            await cb.message.edit_media(media=media, reply_markup=kb)
        except TelegramBadRequest:
            new = await cb.message.answer_photo(fid, caption=caption, reply_markup=kb)
            with suppress(Exception):
                await cb.message.delete()
    else:
        new = await cb.message.answer_photo(fid, caption=caption, reply_markup=kb)
        with suppress(Exception):
            await cb.message.delete()


@router.callback_query(F.data.startswith("cartimg|open|"))
async def cb_cartimg_open(cb: CallbackQuery):
    payload = cb.data.split("|", 2)[-1]
    pid_s, idx_s = payload.split(":")
    await render_cart_photo(cb, int(pid_s), int(idx_s))
    return await cb.answer()


@router.callback_query(F.data.startswith("cartimg|nav|"))
async def cb_cartimg_nav(cb: CallbackQuery):
    _, _, direction, payload = cb.data.split("|", 3)
    pid_s, idx_s = payload.split(":")
    pid, idx = int(pid_s), int(idx_s)

    photos = (PRODUCTS_BY_ID.get(pid, {}) or {}).get("photos") or []
    if len(photos) < 2:
        return await cb.answer("Здесь только одно фото.", show_alert=True)

    idx = (idx - 1) % len(photos) if direction == "prev" else (idx + 1) % len(photos)
    await render_cart_photo(cb, pid, idx)
    return await cb.answer()


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
            InlineKeyboardButton(text="–", callback_data=f"cart|dec|{p['id']}"),
            InlineKeyboardButton(text=f"x{qty}", callback_data="noop"),
            InlineKeyboardButton(
                text=("+" if can_inc else "🚫"),
                callback_data=(f"cart|inc|{p['id']}" if can_inc else "noop")
            ),
        ]

        if SHOW_DELETE_BUTTON:
            row.append(InlineKeyboardButton(text="Удалить", callback_data=f"cart|del|{p['id']}"))
        rows.append(row)

        if p.get("photos") or []:
            rows.append([
                InlineKeyboardButton(text="📷 Фото", callback_data=f"cartimg|open|{p['id']}:0"),
            ])

    rows.append([InlineKeyboardButton(text="Очистить", callback_data="cart|clear|")])
    rows.append([InlineKeyboardButton(text="Перейти к службе доставки", callback_data="delivery|open|")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к товарам", callback_data="catalog1|open|")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_cart(cb: CallbackQuery):
    items = CART.get(cb.from_user.id, {})
    if not items:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к товарам", callback_data="catalog1|open|")]
        ])
        await safe_edit(cb.message, "Корзина пуста.", reply_markup=kb)
        return

    lines, total_qty, total_sum = cart_totals(cb.from_user.id)
    text_lines = ["<b>Корзина</b>"]

    for p, qty, line_sum in lines:
        text_lines.append(f"• {p['title']} — x{qty} = {money(line_sum)}")
    text_lines.append(f"\nИтого: {total_qty} шт на сумму {money(total_sum)}")

    await safe_edit(cb.message, "\n".join(text_lines), cart_keyboard(cb.from_user.id, lines))


@router.callback_query(F.data == "cart|open|")
async def cb_cart_open(cb: CallbackQuery):
    purge_expired_carts()
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
    purge_expired_carts()
    items = CART.get(cb.from_user.id, {})
    for pid, qty in items.items():
        inc_stock(pid, qty)
    CART[cb.from_user.id] = {}
    CART_META.pop(cb.from_user.id, None)
    await render_cart(cb)
    return await cb.answer("Очищено")


PENDING_ORDERS: dict[str, dict] = {}


def build_cart_snapshot(user_id: int) -> dict:
    items = []
    for pid, qty in CART.get(user_id, {}).items():
        if qty > 0:
            p = catalog.PRODUCTS_BY_ID.get(pid)
            if p:
                items.append({
                    "pid": pid,
                    "title": p["title"],
                    "price": p["price"],
                    "qty": qty,
                    "photos": p.get("photos", []),
                })
    total_rub = sum(it["price"] * it["qty"] for it in items)
    return {"items": items, "total_rub": total_rub}


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
                              callback_data=("delivery:open" if filled else "noop"))],
        [InlineKeyboardButton(text="⬅️ Сменить службу", callback_data="delivery|choose|")],
        [InlineKeyboardButton(text="⬅️ Назад в корзину", callback_data="cart|open|")],
    ])


def rub_to_kopecks(rub: int | float) -> int:
    return int(round(float(rub) * 100))


def cart_to_prices(items: list[dict]) -> list[LabeledPrice]:
    prices = []
    for it in items:
        label = f'{it["title"]} × {it["qty"]}'
        amount = rub_to_kopecks(it["price"] * it["qty"])
        prices.append(LabeledPrice(label=label, amount=amount))
    return prices


def make_invoice_description(items: list[dict]) -> str:
    lines = [f'{it["title"]} × {it["qty"]} — {it["price"]} ₽' for it in items]
    return "\n".join(lines) if lines else "Позиции не найдены"


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


@router.callback_query(F.data == "delivery|show|")
async def cb_delivery_show(cb: CallbackQuery):
    await safe_edit(cb.message, delivery_form_text(cb.from_user.id), delivery_form_keyboard(cb.from_user.id))
    return await cb.answer()


@router.callback_query(F.data == "payment|start|current")
async def cb_payment_start(cb: CallbackQuery):
    ctx = DELIVERY_CTX.get(cb.from_user.id) or {}
    if not (ctx.get("carrier") and ctx.get("phone") and ctx.get("email") and ctx.get("address")):
        return await cb.answer("Заполните все данные доставки", show_alert=True)

    await safe_edit(cb.message,
                    "💳 (Заглушка) Оплата: здесь будет выставление счёта.",
                    InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Провести оплату", callback_data="payment|mock_success|")],
                        [InlineKeyboardButton(text="⬅️ Назад к доставке", callback_data="delivery|show|")],
                    ]))
    return await cb.answer()


@router.callback_query(F.data == "payment|mock_success|")
async def cb_payment_mock_success(cb: CallbackQuery):
    clear_cart(cb.from_user.id, restore_stock=False)
    await safe_edit(cb.message, "🎉 Спасибо за покупку! Сейчас будет выдан трек.",
                    InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ В каталог", callback_data="catalog1|open|")],
                        [InlineKeyboardButton(text="🧺 Корзина", callback_data="cart|open|")],
                    ]))
    return await cb.answer()


@router.message(Command("admin"))
async def cmd_admin_help(m: Message):
    if not is_admin(m.from_user.id):
        return

    txt = (
        "<b>Команды админа</b>\n\n"

        "<b>/add</b>\n"
        "<code>/add &lt;категория&gt; &lt;камень&gt; &quot;&lt;название&gt;&quot; "
        "&lt;цена&gt; &lt;кол-во&gt; [&quot;&lt;описание&gt;&quot;]</code>\n"
        "Примеры:\n"
        "<code>/add браслеты аметист \"Заря\" 1234 1\n</code>"
        "<code>/add колье цитрин \"Солнечный путь\" 2490 3 \"Длина 45 см\"</code>\n\n"

        "<b>/set</b>\n"
        "<code>/set &lt;id&gt; &lt;кол-во|+n|-n&gt;</code>\n"
        "<code>/set &lt;id&gt; price &lt;цена&gt;</code>\n"
        "<code>/set &lt;id&gt; title &lt;новое название&gt;</code>\n"
        "<code>/set &lt;id&gt; desc &lt;текст|-&gt;</code>\n"
        "<code>/set &lt;id&gt; category &lt;тип&gt;</code>\n"
        "<code>/set &lt;id&gt; stone &lt;камень&gt;</code>\n"
        "Примеры:\n"
        "<code>/set 25 +2</code>\n"
        "<code>/set 25 price 1490</code>\n"
        "<code>/set 25 title Небесная гроза</code>\n"
        "<code>/set 25 desc Ожерелье диаметра 15 см</code>\n"
        "<code>/set 25 category браслеты</code>\n"
        "<code>/set 25 stone аметист</code>\n\n"

        "<b>/del</b>\n"
        "<code>/del &lt;id&gt;</code>\n"
        "<code>/del &lt;id1,id2,id3&gt;</code>\n"
        "<code>/del &lt;id1 id2 id3&gt;</code>\n"
        "Примеры:\n"
        "<code>/del 42</code>\n"
        "<code>/del 7, 12, 15</code>\n"
        "<code>/del 7 12 15</code>\n\n"

        "<b>/list</b>\n"
        "<code>/list</code>\n"
        "<code>/list category &lt;тип&gt;</code>\n"
        "<code>/list stone &lt;камень&gt;</code>\n"
        "<code>/list &lt;тип&gt; &lt;камень&gt;</code>\n"
        "Примеры:\n"
        "<code>/list</code>\n"
        "<code>/list category браслеты</code>\n"
        "<code>/list stone аметист</code>\n"
        "<code>/list браслеты аметист</code>\n"
    )

    await m.answer(txt)



@router.message(Command("add"), ~F.photo, ~F.media_group_id)
async def admin_add_text(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    await add_product_from_args(m, command.args or "", photos=[])


@router.message(Command("del"))
async def admin_del_text(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return

    raw = (command.args or "").strip()
    ids = [int(x) for x in re.findall(r"\d+", raw)]
    if not ids:
        return await m.answer("Укажи ID(ы): /del 123  или  /del 1,2,3")

    ids = sorted(set(ids))
    async with Session() as s:
        found = (await s.execute(select(Product.id).where(Product.id.in_(ids)))).scalars().all()
        if not found:
            return await m.answer("Ни одного товара с такими ID не найдено.")
        await s.execute(delete(Product).where(Product.id.in_(found)))
        await s.commit()

    for pid in found:
        cache_delete_product(pid)
    await cleanup_orphan_refs()

    not_found = [str(i) for i in ids if i not in set(found)]
    msg = [f"✅ Удалено: {', '.join(map(str, found))}"]
    if not_found:
        msg.append(f"⚠️ Не найдены: {', '.join(not_found)}")
    await m.answer("\n".join(msg))


@router.message(Command("set"))
async def admin_set(message: Message, command: CommandObject):
    args = shlex.split(command.args or "")
    if len(args) < 2:
        return await message.answer(
            "Как пользоваться:\n"
            "/set <id> <кол-во|+n|-n>\n"
            "/set <id> price <цена>\n"
            "/set <id> title <название>\n"
            "/set <id> desc <текст|->\n"
            "/set <id> category <тип (рус.)>\n"
            "/set <id> stone <камень (рус.)>"
        )

    try:
        pid = int(args[0])
    except ValueError:
        return await message.answer("Первый аргумент — это ID товара (число).")

    async with Session() as s:
        p: Product | None = await s.get(Product, pid)
        if not p:
            return await message.answer(f"Товар с id={pid} не найден.")

        if len(args) == 2 and args[1]:
            delta = args[1]
            if delta.startswith(("+", "-")):
                try:
                    diff = int(delta)
                except ValueError:
                    return await message.answer("Количество должно быть числом (например, +2 или -1).")
                p.stock = max(0, p.stock + diff)
            else:
                try:
                    qty = int(delta)
                except ValueError:
                    return await message.answer("Количество должно быть числом.")
                p.stock = max(0, qty)
            await s.commit()
            await load_catalog_to_memory()
            return await message.answer(f"✅ Обновлено: ID #{p.id}\nНовое количество: {p.stock}")

        if len(args) < 3:
            return await message.answer("Не хватает значения. Пример: /set 12 price 1490")

        field = args[1].lower()
        value = " ".join(args[2:]).strip()

        if field in ("price", "стоимость"):
            try:
                price = int(value)
            except ValueError:
                return await message.answer("Цена должна быть числом (без пробелов).")
            if price < 0:
                return await message.answer("Цена не может быть отрицательной.")
            p.price = price

        elif field in ("title", "name", "название"):
            if not value:
                return await message.answer("Название не может быть пустым.")
            p.title = value

        elif field in ("desc", "описание"):
            if value in ("-", "—", "none", "нет"):
                p.description = ""
            else:
                p.description = value

        elif field in ("category", "категория", "type", "тип"):
            cat = await get_or_create_category(s, value)
            p.category_id = cat.id

        elif field in ("stone", "камень"):
            st = await get_or_create_stone(s, value)
            p.stone_id = st.id

        else:
            return await message.answer(
                "Неизвестное поле. Можно: stock, price, title, desc, category, stone."
            )

        await s.commit()
        await s.refresh(p)
        await load_catalog_to_memory()

        try:
            cat = (await s.get(Category, p.category_id)).name_ru
            stn = (await s.get(Stone, p.stone_id)).name_ru
        except Exception:
            cat, stn = "—", "—"

    text = render_product_text(
        {
            "id": p.id,
            "title": p.title,
            "price": p.price,
            "stock": p.stock,
            "description": p.description or "",
        },
        pos=0,
        total=1,
        category=cat,
        stone=stn,
    )
    await message.answer("✅ Обновлено.\n\n" + text)


@router.message(Command("list"))
async def admin_list(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return

    args = (command.args or "").strip()
    if args.lower() in {"types", "категории"}:
        async with Session() as session:
            rows = (await session.execute(
                select(Category.name_ru, func.count(Product.id))
                .join(Product, Product.category_id == Category.id)
                .group_by(Category.id, Category.name_ru)
                .order_by(Category.name_ru.asc())
            )).all()

        if not rows:
            return await m.answer("Категорий пока нет.")
        text = "\n".join(f"• {name} — {cnt} шт." for name, cnt in rows)
        return await m.answer("<b>По ассортиментам</b>:\n" + text)

    if args.lower() in {"stones", "камни"}:
        async with Session() as session:
            rows = (await session.execute(
                select(Stone.name_ru, func.count(Product.id))
                .join(Product, Product.stone_id == Stone.id)
                .group_by(Stone.id, Stone.name_ru)
                .order_by(Stone.name_ru.asc())
            )).all()

            if not rows:
                return await m.answer("Камней пока нет.")
            text = "\n".join(f"• {name} — {cnt} шт." for name, cnt in rows)
            return await m.answer("<b>По камням</b>:\n" + text)

    terms = shlex.split(args) if args else []
    async with Session() as s:
        cat = stn = None

        if len(terms) == 1:
            t = terms[0]
            c = await find_category_by_term(s, t)
            s_ = await find_stone_by_term(s, t)
            if c and not s_:
                cat = c
            elif s_ and not c:
                stn = s_
            elif c and s_:
                return await m.answer(
                    "Уточни, это категория или камень?\n"
                    "Можно явно указать два слова: <code>/list &lt;категория&gt; &lt;камень&gt;</code>\n"
                    "Либо посмотреть агрегаты: <code>/list types</code> или <code>/list stones</code>"
                )
            else:
                return await m.answer("Ничего не найдено по этому слову.")
        elif len(terms) >= 2:
            cat = await find_category_by_term(s, terms[0])
            stn = await find_stone_by_term(s, " ".join(terms[1:])) if len(terms) > 2 else await find_stone_by_term(s, terms[1])
            if not cat and not stn:
                return await m.answer("Не удалось распознать ни категорию, ни камень.")
        q = (
            select(Product, Category.name_ru, Stone.name_ru)
            .join(Category, Product.category_id == Category.id)
            .join(Stone, Product.stone_id == Stone.id)
            .order_by(Product.id.desc())
            .limit(30)
        )

        if cat:
            q = q.where(Product.category_id == cat.id)
        if stn:
            q = q.where(Product.stone_id == stn.id)
        rows = (await s.execute(q)).all()

    if not rows:
        label = []
        if cat: label.append(cat.name_ru)
        if stn: label.append(stn.name_ru)
        hint = f" по фильтру: {' / '.join(label)}" if label else ""
        return await m.answer(f"Список пуст{hint}.")

    lines = []
    for p, cat_ru, stone_ru in rows:
        nphotos = len(p.photos or [])
        has_desc = " + описание" if p.description else ""
        lines.append(
            f"#{p.id} • {cat_ru} / {stone_ru}\n"
            f"{p.title} — {p.price} ₽, {p.stock} шт. (фото: {nphotos}{has_desc})"
        )

    await m.answer("\n\n".join(lines))


async def show_product(
        cb: CallbackQuery,
        p: dict,
        idx: int,
        total: int,
        category: str,
        stone: str,
        img_idx: int = 0,
) -> None:
    cat_ru, stone_ru = ru_labels(category, stone)
    caption = render_product_text(p, idx, total, cat_ru, stone_ru)
    kb = product_keyboard(category, stone, p["id"], cb.from_user.id, idx, total, img_idx=img_idx)

    photos = p.get("photos") or []
    if photos:
        img_idx = img_idx % len(photos)
        fid = photos[img_idx]
        media = InputMediaPhoto(media=fid, caption=caption)

        if cb.message.content_type == "photo":
            try:
                await cb.message.edit_media(media=media, reply_markup=kb)
            except TelegramBadRequest:
                new = await cb.message.answer_photo(fid, caption=caption, reply_markup=kb)
                with suppress(Exception):
                    await cb.message.delete()

        else:
            new = await cb.message.answer_photo(fid, caption=caption, reply_markup=kb)
            with suppress(Exception):
                await cb.message.delete()

    else:
        if cb.message.content_type == "photo":
            new = await cb.message.answer(caption, reply_markup=kb)
            with suppress(Exception):
                await cb.message.delete()

        else:
            await safe_edit(cb.message, caption, kb)

@router.message(Command("add"), F.photo, ~F.media_group_id)
async def admin_add_single_photo(m: Message, command: CommandObject):
    if not is_admin(m.from_user.id):
        return
    fid = m.photo[-1].file_id
    await add_product_from_args(m, command.args or "", photos=[fid])


@router.message(F.photo, ~F.media_group_id, ~F.caption.startswith("/add"))
async def admin_photo_without_add(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.reply("Чтобы прикрепить фото к товару, добавь команду в подпись: "
                  "<code>/add &lt;категория&gt; &lt;камень&gt; \"Название\" &lt;цена&gt; &lt;остаток&gt; [\"Описание\"]</code>")


@router.message(F.media_group_id, F.photo)
async def collect_album(m: Message):
    if not is_admin(m.from_user.id):
        return
    mgid = str(m.media_group_id)
    buf = album_buffers.setdefault(mgid, {"admin_id": m.from_user.id, "photos": [], "args_text": None, "message": m})
    buf["photos"].append(m.photo[-1].file_id)
    buf["message"] = m

    cap = (m.caption or "").strip()
    if cap.startswith("/add"):
        buf["args_text"] = cap.split(None, 1)[1] if " " in cap else ""

    asyncio.create_task(finalize_album_after_pause(mgid))


async def finalize_album_after_pause(mgid: str):
    await asyncio.sleep(ALBUM_SETTLE_SEC)
    buf = album_buffers.pop(mgid, None)
    if not buf:
        return
    if not is_admin(buf["admin_id"]):
        return

    photos = list(dict.fromkeys(buf["photos"]))[:5]

    if buf["args_text"]:
        await add_product_from_args(buf["message"], buf["args_text"], photos=photos)
    else:
        await buf["message"].reply(
            "В подписи альбома не найдено /add. "
            "Отправь альбом заново с подписью вида:\n"
            "<code>/add &lt;категория&gt; &lt;камень&gt; \"Название\" &lt;цена&gt; &lt;остаток&gt; [\"Описание\"]</code>"
        )


@router.pre_checkout_query()
async def pre_checkout(pre: PreCheckoutQuery, bot: Bot):
    await pre.answer(ok=True)


@router.message(F.successful_payment)
async def on_success_payment(m: Message, bot: Bot):
    sp = m.successful_payment
    payload = sp.invoice_payload
    snap = PENDING_ORDERS.pop(payload, None)
    if not snap:
        await m.answer("Не удалось найти заказ по платежу.")
        return

    async with Session() as s:
        order = Order(
            user_id=snap["user_id"],
            chat_id=snap["chat_id"],
            full_name=m.from_user.full_name or "",
            username=m.from_user.username or "",
            currency=snap["currency"],
            total_amount=snap["total_kop"],
            payload=payload,
            status=OrderStatus.paid,
        )
        s.add(order)
        await s.flush()

        for it in snap["items"]:
            pid = it["pid"]
            qty = it["qty"]

            p: Product | None = await s.get(Product, pid)
            if not p:
                continue

            real_qty = min(qty, max(0, p.stock))
            s.add(OrderItem(
                order_id=order.id,
                product_id=pid,
                title=p.title,
                price=rub_to_kopecks(it["price"]),
                qty=real_qty,
                photos=it.get("photos", []),
            ))

            p.stock = max(0, p.stock - real_qty)
            if DELETE_PRODUCT_WHEN_STOCK_ZERO and p.stock == 0:
                await s.delete(p)
                cache_delete_product(pid)
            else:
                await cache_refresh_single(s, pid)

        await s.commit()
        await cleanup_orphan_refs()

    CART[m.from_user.id] = {}
    CART_META.pop(m.from_user.id, None)

    await m.answer("Спасибо за покупку! ✨")
    await notify_managers_about_order(bot, m, order, snap)


async def notify_managers_about_order(bot: Bot, m: Message, order: Order, snap: dict):
    user = m.from_user
    lines = [
        f"🧾 <b>Новый заказ #{order.id}</b>",
        f"Покупатель: <a href='tg://user?id={user.id}'>{user.full_name}</a> @{user.username or '—'}",
        f"Способ: самовывоз",
        f"Сумма: {order.total_amount / 100:.2f} {order.currency}",
        "",
        "Состав:",
    ]
    for it in snap["items"]:
        lines.append(f"• {it['title']} — {it['price']} ₽ × {it['qty']}")

    text = "\n".join(lines)

    for admin_id in settings.MANAGER_IDS:
        try:
            await bot.send_message(admin_id, text, disable_web_page_preview=True)
        except Exception:
            pass

        for it in snap["items"]:
            photos = (it.get("photos") or [])[:5]
            if not photos:
                continue
            media = [InputMediaPhoto(media=ph) for ph in photos]
            try:
                await bot.send_media_group(admin_id, media)
            except Exception:
                for ph in photos:
                    with suppress(Exception):
                        await bot.send_photo(admin_id, ph)
