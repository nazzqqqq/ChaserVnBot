import os
import threading
import re
import sqlite3
import logging
import time
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove
)

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Завантаження змінних середовища
load_dotenv()

TOKEN = "8044468648:AAGemhueIgVFBd4jBEE24WyEmCGsLhluBtA"
ADMIN_IDS = [1095755080]
DB_PATH = "vape_shop.db"

# Ініціалізація бота та диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher()


# Класи станів
class OrderState(StatesGroup):
    waiting_for_contact = State()
    waiting_for_address = State()
    waiting_for_payment = State()  # Новий стан для вибору оплати
    waiting_for_confirmation = State()


class AddProductState(StatesGroup):
    waiting_for_category = State()
    waiting_for_name = State()
    waiting_for_volume = State()
    waiting_for_price = State()
    waiting_for_flavors = State()



def db_connection():
    return sqlite3.connect(DB_PATH, timeout=10)
DB_PATH = "database.db"
DB_TIMEOUT = 10
# Ініціалізація бази даних
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)  # або DB_TIMEOUT, якщо він визначений
    cur = conn.cursor()

    # Таблиця товарів
    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        volume TEXT,
        price REAL NOT NULL
    )
    """)

    # Таблиця смаків
    cur.execute("""
    CREATE TABLE IF NOT EXISTS flavors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        FOREIGN KEY (product_id) REFERENCES products(id)
    )
    """)

    # Таблиця кошиків
    cur.execute("""
    CREATE TABLE IF NOT EXISTS carts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        flavor_id INTEGER NOT NULL,
        quantity INTEGER DEFAULT 1
    )
    """)

    # Таблиця замовлень
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        phone TEXT NOT NULL,
        address TEXT NOT NULL,
        total REAL NOT NULL,
        status TEXT DEFAULT 'new',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        payment_method TEXT NOT NULL
    )
    """)

    # Таблиця елементів замовлення
    cur.execute("""
    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        flavor_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        FOREIGN KEY (order_id) REFERENCES orders(id)
    )
    """)

    conn.commit()
    conn.close()

init_db()
db_lock = threading.Lock()

def safe_db_execute(query, params=()):
    with db_lock:
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            return cur.fetchall()

# Допоміжні функції для роботи з БД
def get_products(category=None, volume=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    query = "SELECT id, name, volume, price FROM products"
    params = []

    if category and volume:
        query += " WHERE category = ? AND volume = ?"
        params = [category, volume]
    elif category:
        query += " WHERE category = ?"
        params = [category]

    cur.execute(query, params)
    products = cur.fetchall()
    conn.close()
    return products


def get_product(product_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, volume, price, category FROM products WHERE id = ?", (product_id,))
    product = cur.fetchone()
    conn.close()
    return product


def get_flavors(product_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM flavors WHERE product_id = ?", (product_id,))
    flavors = cur.fetchall()
    conn.close()
    return flavors


def add_to_cart(user_id, product_id, flavor_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    try:
        # Перевіряємо чи вже є такий товар у кошику
        cur.execute("""
        SELECT id, quantity FROM carts 
        WHERE user_id = ? AND product_id = ? AND flavor_id = ?
        """, (user_id, product_id, flavor_id))
        existing_item = cur.fetchone()

        if existing_item:
            new_quantity = existing_item[1] + 1
            cur.execute("""
            UPDATE carts SET quantity = ? 
            WHERE id = ?
            """, (new_quantity, existing_item[0]))
        else:
            cur.execute("""
            INSERT INTO carts (user_id, product_id, flavor_id, quantity)
            VALUES (?, ?, ?, 1)
            """, (user_id, product_id, flavor_id))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding to cart: {e}")
        return False
    finally:
        conn.close()


def get_cart(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    SELECT c.id, c.product_id, c.flavor_id, c.quantity, 
           p.name, p.price, f.name as flavor_name,
           p.volume
    FROM carts c
    JOIN products p ON c.product_id = p.id
    JOIN flavors f ON c.flavor_id = f.id
    WHERE c.user_id = ?
    """, (user_id,))
    cart_items = cur.fetchall()
    conn.close()
    return cart_items


def remove_from_cart(cart_item_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM carts WHERE id = ?", (cart_item_id,))
    conn.commit()
    conn.close()


def clear_cart(user_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def create_order(user_id, phone, address, total, payment_method):
    try:
        with db_connection() as conn:
            cur = conn.cursor()

            # Починаємо транзакцію
            cur.execute("BEGIN TRANSACTION")

            # Додаємо замовлення
            cur.execute("""
            INSERT INTO orders (user_id, phone, address, total, payment_method)
            VALUES (?, ?, ?, ?, ?)
            """, (user_id, phone, address, total, payment_method))
            order_id = cur.lastrowid

            # Отримуємо товари з кошика в межах однієї транзакції
            cur.execute("""
            SELECT c.product_id, c.flavor_id, c.quantity, p.price
            FROM carts c
            JOIN products p ON c.product_id = p.id
            WHERE c.user_id = ?
            """, (user_id,))
            cart_items = cur.fetchall()

            # Додаємо елементи замовлення
            for item in cart_items:
                product_id, flavor_id, quantity, price = item
                cur.execute("""
                INSERT INTO order_items (order_id, product_id, flavor_id, quantity, price)
                VALUES (?, ?, ?, ?, ?)
                """, (order_id, product_id, flavor_id, quantity, price))

            # Очищаємо кошик
            cur.execute("DELETE FROM carts WHERE user_id = ?", (user_id,))

            # Фіксуємо зміни
            conn.commit()
            return order_id

    except Exception as e:
        logger.error(f"Critical error creating order: {e}")
        return None
    finally:
        conn.close()


def add_product(category, name, volume, price):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("""
        INSERT INTO products (category, name, volume, price)
        VALUES (?, ?, ?, ?)
        """, (category, name, volume, price))
        product_id = cur.lastrowid
        conn.commit()
        return product_id
    except Exception as e:
        logger.error(f"Error adding product: {e}")
        return None
    finally:
        conn.close()


def add_flavors(product_id, flavors):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        for flavor in flavors.split(','):
            flavor_name = flavor.strip()
            if flavor_name:
                cur.execute("""
                INSERT INTO flavors (product_id, name)
                VALUES (?, ?)
                """, (product_id, flavor_name))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error adding flavors: {e}")
        return False
    finally:
        conn.close()


def get_statistics():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Загальна кількість замовлень
    cur.execute("SELECT COUNT(*) FROM orders")
    total_orders = cur.fetchone()[0]

    # Загальна сума продажів
    cur.execute("SELECT SUM(total) FROM orders")
    total_sales = cur.fetchone()[0] or 0

    # Найпопулярніші товари
    cur.execute("""
    SELECT p.name, SUM(oi.quantity) as total_quantity
    FROM order_items oi
    JOIN products p ON oi.product_id = p.id
    GROUP BY p.name
    ORDER BY total_quantity DESC
    LIMIT 5
    """)
    popular_products = cur.fetchall()

    conn.close()
    return {
        'total_orders': total_orders,
        'total_sales': total_sales,
        'popular_products': popular_products
    }


# Клавіатури
def main_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Каталог")],
            [KeyboardButton(text="🛒 Кошик"), KeyboardButton(text="📞 Підтримка")]
        ],
        resize_keyboard=True
    )
    return keyboard


def categories_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="рідина Chaser")],
            [KeyboardButton(text="Картриджі X-ROS"), KeyboardButton(text="Назад")]
        ],
        resize_keyboard=True
    )
    return keyboard


def volumes_keyboard(category):
    if category == "рідина Chaser":
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="10мл")],
                [KeyboardButton(text="30мл"), KeyboardButton(text="Назад")]
            ],
            resize_keyboard=True
        )
    else:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="X-ROS")],
                [KeyboardButton(text="Назад")]
            ],
            resize_keyboard=True
        )
    return keyboard


def back_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Назад")]],
        resize_keyboard=True
    )


def cart_keyboard(cart_items):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚖 Оформити замовлення")],
            [KeyboardButton(text="🧹 Очистити кошик"), KeyboardButton(text="📋 Каталог")]
        ],
        resize_keyboard=True
    )

    # Додаємо кнопки для видалення конкретних товарів
    for item in cart_items:
        cart_id, _, _, _, name, _, flavor_name, _ = item
        keyboard.keyboard.insert(0, [
            KeyboardButton(text=f"❌ Видалити {name} ({flavor_name})")
        ])

    return keyboard


def contact_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Надіслати контакт", request_contact=True)],
            [KeyboardButton(text="📝 Ввести вручну")],  # Нова опція ручного вводу
            [KeyboardButton(text="Скасувати")]
        ],
        resize_keyboard=True
    )

def create_order_with_retry(user_id, phone, address, total, payment_method, retries=3):
    for attempt in range(retries):
        try:
            return create_order(user_id, phone, address, total, payment_method)
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < retries - 1:
                logger.warning(f"DB locked, retrying ({attempt+1}/{retries})")
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
# Уніфікований обробник контактів
@dp.message(OrderState.waiting_for_contact)
async def process_contact(message: types.Message, state: FSMContext):
    phone = None

    # Обробка контакту
    if message.contact:
        phone = message.contact.phone_number

    # Обробка ручного вводу
    elif message.text == "📝 Ввести вручну":
        await message.answer(
            "📱 Введіть свій номер телефону у форматі: +380XXXXXXXXX",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Скасувати")]],
                resize_keyboard=True
            )
        )
        return

    # Перевірка введеного номеру
    elif message.text and re.match(r'^\+?[0-9]{10,12}$', message.text.replace(" ", "")):
        phone = message.text

    # Скасування
    elif message.text == "Скасувати":
        await state.clear()
        await message.answer("Замовлення скасовано", reply_markup=main_menu())
        return

    # Повідомлення про помилку
    else:
        await message.answer(
            "❌ Неправильний формат номеру. Використовуйте формат: +380XXXXXXXXX",
            reply_markup=contact_keyboard()
        )
        return

    # Зберігаємо номер та переходимо до адреси
    if phone:
        await state.update_data(phone=phone)
        await state.set_state(OrderState.waiting_for_address)
        await message.answer(
            "📍 Оберіть адресу доставки:",
            reply_markup=address_keyboard()
        )


def address_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="космонавтів 78")],
            [KeyboardButton(text="Премьер Товер")],
            [KeyboardButton(text="АТБ на Космонавтів")],
            [KeyboardButton(text="АТБ на Юності")],
            [KeyboardButton(text="Школа №10")],
            [KeyboardButton(text="Парк Дружби Народів")],
            [KeyboardButton(text="Скасувати")]
        ],
        resize_keyboard=True
    )


def payment_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💵 Готівка при отриманні")],
            [KeyboardButton(text="💳 Оплата карткою")],
            [KeyboardButton(text="Скасувати")]
        ],
        resize_keyboard=True
    )


def confirm_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Підтвердити замовлення")],
            [KeyboardButton(text="❌ Скасувати")]
        ],
        resize_keyboard=True
    )


def admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="➕ Додати товар"), KeyboardButton(text="📦 Замовлення")],
            [KeyboardButton(text="🔙 Головне меню")]
        ],
        resize_keyboard=True
    )


# Обробники команд
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Вітаємо у вейп-магазині!\n"
        "🛍️ Оберіть товар у каталозі та додайте до кошика",
        reply_markup=main_menu()
    )


@dp.message(F.text == "🔙 Головне меню")
async def back_to_main(message: types.Message):
    await message.answer("Головне меню:", reply_markup=main_menu())


@dp.message(F.text == "📋 Каталог")
async def show_categories(message: types.Message):
    await message.answer("Оберіть категорію:", reply_markup=categories_keyboard())


@dp.message(F.text == "Назад")
async def back_handler(message: types.Message):
    await show_categories(message)


@dp.message(F.text.in_(["рідина Chaser", "Картриджі X-ROS"]))
async def show_volumes(message: types.Message, state: FSMContext):
    category = message.text
    await state.update_data(category=category)
    await message.answer("Оберіть об'єм:", reply_markup=volumes_keyboard(category))


@dp.message(F.text.in_(["10мл", "30мл", "X-ROS"]))
async def show_products(message: types.Message, state: FSMContext):
    volume = message.text
    data = await state.get_data()
    category = data.get('category', '')

    products = get_products(category, volume)
    if not products:
        await message.answer("Товари відсутні", reply_markup=back_keyboard())
        return

    for product in products:
        prod_id, name, prod_volume, price = product
        flavors = get_flavors(prod_id)
        if not flavors:
            await message.answer("Смаки відсутні", reply_markup=back_keyboard())
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for flavor in flavors:
            flavor_id, flavor_name = flavor
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{flavor_name}",
                    callback_data=f"flavor_{prod_id}_{flavor_id}"
                )
            ])

        await message.answer(
            f"🏷️ {name} ({prod_volume})\n"
            f"💵 Ціна: {price} грн\n"
            f"👅 Оберіть смак:",
            reply_markup=keyboard
        )


@dp.callback_query(F.data.startswith("flavor_"))
async def select_flavor(callback: types.CallbackQuery):
    _, product_id, flavor_id = callback.data.split('_')
    product = get_product(int(product_id))
    if not product:
        await callback.answer("Товар не знайдено")
        return

    flavor = next((f for f in get_flavors(int(product_id)) if f[0] == int(flavor_id)), None)
    if not flavor:
        await callback.answer("Смак не знайдено")
        return

    add_to_cart(callback.from_user.id, int(product_id), int(flavor_id))

    # Створюємо клавіатуру з кнопкою переходу до кошика
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 До кошика", callback_data="go_to_cart")]
    ])

    await callback.message.edit_text(
        f"✅ {product[1]} ({flavor[1]}) додано до кошика!",
        reply_markup=keyboard
    )


# Новий обробник для кнопки переходу до кошика
@dp.callback_query(F.data == "go_to_cart")
async def go_to_cart_handler(callback: types.CallbackQuery):
    await callback.answer()
    # Використовуємо callback.from_user.id замість callback.message
    await show_cart(callback.from_user.id, callback.message)


@dp.message(F.text == "🛒 Кошик")
async def show_cart(user_id: int, message: types.Message):
    cart_items = get_cart(user_id)
    if not cart_items:
        # Використовуємо оригінальне повідомлення для відповіді
        await message.answer("🛒 Ваш кошик порожній", reply_markup=main_menu())
        return

    total = 0
    cart_text = "🛒 Ваш кошик:\n\n"
    for item in cart_items:
        cart_id, product_id, flavor_id, quantity, name, price, flavor_name, volume = item
        item_total = quantity * price
        total += item_total
        cart_text += f"▪️ {name} ({volume}) - {flavor_name}\n"
        cart_text += f"   Кількість: {quantity} x {price} грн = {item_total} грн\n\n"

    cart_text += f"💸 Загальна сума: {total} грн"
    # Використовуємо оригінальне повідомлення для відповіді
    await message.answer(cart_text, reply_markup=cart_keyboard(cart_items))


@dp.message(F.text.startswith("❌ Видалити"))
async def remove_item_handler(message: types.Message):
    cart_items = get_cart(message.from_user.id)
    if not cart_items:
        await message.answer("🛒 Ваш кошик порожній", reply_markup=main_menu())
        return

    # Знаходимо відповідний товар у кошику
    for item in cart_items:
        cart_id, _, _, _, name, _, flavor_name, _ = item
        if f"❌ Видалити {name} ({flavor_name})" == message.text:
            remove_from_cart(cart_id)
            await message.answer(f"🗑️ {name} ({flavor_name}) видалено з кошика")
            break

    # Показуємо оновлений кошик
    await show_cart(message)


@dp.message(F.text == "🧹 Очистити кошик")
async def clear_cart_handler(message: types.Message):
    clear_cart(message.from_user.id)
    await message.answer("🗑️ Кошик очищено", reply_markup=main_menu())


@dp.message(F.text == "🚖 Оформити замовлення")
async def start_order(message: types.Message, state: FSMContext):
    cart_items = get_cart(message.from_user.id)
    if not cart_items:
        await message.answer("🛒 Ваш кошик порожній", reply_markup=main_menu())
        return

    await state.set_state(OrderState.waiting_for_contact)
    await message.answer(
        "📱 Будь ласка, поділіться своїм контактом для оформлення замовлення",
        reply_markup=contact_keyboard()
    )


@dp.message(OrderState.waiting_for_contact, F.contact)
async def process_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await state.set_state(OrderState.waiting_for_address)
    await message.answer(
        "📍 Оберіть адресу доставки:",
        reply_markup=address_keyboard()
    )


@dp.message(OrderState.waiting_for_address, F.text)
async def process_address(message: types.Message, state: FSMContext):
    if message.text == "Скасувати":
        await state.clear()
        await message.answer("Замовлення скасовано", reply_markup=main_menu())
        return

    address = message.text
    await state.update_data(address=address)
    await state.set_state(OrderState.waiting_for_payment)
    await message.answer(
        "💳 Оберіть спосіб оплати:",
        reply_markup=payment_keyboard()
    )


@dp.message(OrderState.waiting_for_payment, F.text)
async def process_payment(message: types.Message, state: FSMContext):
    if message.text == "Скасувати":
        await state.clear()
        await message.answer("Замовлення скасовано", reply_markup=main_menu())
        return

    payment_method = message.text
    await state.update_data(payment_method=payment_method)

    # Розрахунок загальної суми
    cart_items = get_cart(message.from_user.id)
    total = sum(item[5] * item[3] for item in cart_items)  # price * quantity

    data = await state.get_data()
    phone = data.get('phone', '')
    address = data.get('address', '')

    # Формуємо деталі замовлення для підтвердження
    order_text = "📝 Деталі замовлення:\n\n"
    order_text += f"📱 Телефон: {phone}\n"
    order_text += f"📍 Адреса: {address}\n"
    order_text += f"💳 Спосіб оплати: {payment_method}\n\n"

    # Додаємо список товарів
    order_text += "🛒 Ваше замовлення:\n"
    for item in cart_items:
        cart_id, _, _, quantity, name, price, flavor_name, volume = item
        item_total = quantity * price
        order_text += f"▪️ {name} ({volume}) - {flavor_name}\n"
        order_text += f"   Кількість: {quantity} x {price} грн = {item_total} грн\n\n"

    order_text += f"💸 Загальна сума: {total} грн\n\n"

    # Додаємо інформацію про оплату карткою
    if payment_method == "💳 Оплата карткою":
        order_text += "ℹ️ Будь ласка, здійсніть оплату на картку:\n"
        order_text += "💳 Номер картки: `4441111006147130`\n"
        order_text += "👤 Отримувач: Максим\n\n"

    order_text += "✅ Підтвердіть замовлення кнопкою нижче 👇"

    await state.update_data(total=total)
    await state.set_state(OrderState.waiting_for_confirmation)
    await message.answer(order_text, reply_markup=confirm_keyboard())


@dp.message(OrderState.waiting_for_confirmation, F.text == "✅ Підтвердити замовлення")
async def confirm_order(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        phone = data.get('phone', '')
        address = data.get('address', '')
        total = data.get('total', 0)
        payment_method = data.get('payment_method', '')

        cart_items = get_cart(message.from_user.id)
        if not cart_items:
            await message.answer("❌ Ваш кошик порожній, неможливо оформити замовлення")
            await state.clear()
            return

        # Формуємо повідомлення для менеджера з правильними індексами
        manager_message = "🚨 ОБОВ'ЯЗКОВО ПЕРЕСЛАТИ ЦЕ СПОВІЩЕННЯ МЕНЕДЖЕРУ @SSWERTYSS 🚨\n\n"
        manager_message += "🚨 НОВЕ ЗАМОВЛЕНЯ 🚨\n\n"
        manager_message += f"👤 Користувач: @{message.from_user.username} ({message.from_user.full_name})\n"
        manager_message += f"📱 Телефон: {phone}\n"
        manager_message += f"📍 Місце зустрічі: {address}\n"
        manager_message += f"💳 Спосіб оплати: {payment_method}\n\n"

        # Деталі товарів - виправлені індекси
        manager_message += "🛒 ЗАМОВЛЕНІ ТОВАРИ:\n"
        for item in cart_items:
            cart_id, product_id, flavor_id, quantity, name, price, flavor_name, volume = item
            item_total = quantity * price
            manager_message += f"▪️ {name} ({volume}) - {flavor_name}\n"
            manager_message += f"   Кількість: {quantity} шт. x {price} грн = {item_total} грн\n"

        manager_message += f"\n💸 ЗАГАЛЬНА СУМА: {total} грн\n\n"

        # Додаткова інформація в залежності від способу оплати
        if payment_method == "💳 Оплата карткою":
            manager_message += "ℹ️ Клієнт повинен здійснити оплату на картку:\n"
            manager_message += "💳 Номер картки: `4441111006147130`\n"
            manager_message += "👤 Отримувач: Максим М\n\n"
        else:
            manager_message += "ℹ️ Оплата буде здійснена готівкою при отриманні\n\n"

        manager_message += "⚠️ БУДЬ ЛАСКА, ПЕРЕСЛЯГНІТЬ ЦЕ ПОВІДОМЛЕННЯ МЕНЕДЖЕРУ @SSWERTYSS ДЛЯ ОБРОБКИ ЗАМОВЛЕННЯ!"

        # Створюємо замовлення
        order_id = create_order(message.from_user.id, phone, address, total, payment_method)

        if order_id:
            # Відповідь користувачу - виправлені індекси
            user_message = f"🎉 Замовлення #{order_id} оформлено!\n\n"
            user_message += "Ваше замовлення:\n"

            for item in cart_items:
                cart_id, product_id, flavor_id, quantity, name, price, flavor_name, volume = item
                item_total = quantity * price
                user_message += f"▪️ {name} ({volume}) - {flavor_name}\n"
                user_message += f"   {quantity} шт. x {price} грн = {item_total} грн\n"

            user_message += f"\n💸 Загальна сума: {total} грн\n"
            user_message += f"📍 Місце зустрічі: {address}\n"
            user_message += f"💳 Спосіб оплати: {payment_method}\n\n"

            if payment_method == "💳 Оплата карткою":
                user_message += "ℹ️ Будь ласка, здійсніть оплату на картку:\n"
                user_message += "💳 Номер картки: `4441111006147130`\n"
                user_message += "👤 Отримувач: Максим М\n\n"

            user_message += "Очікуйте сповіщення для підтвердження замовлення. Дякуємо!"

            await message.answer(user_message, reply_markup=main_menu())

            # Відправляємо повідомлення менеджеру
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, manager_message, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Не вдалося надіслати повідомлення адміністратору {admin_id}: {e}")
        else:
            await message.answer(
                "❌ Помилка при оформленні замовлення",
                reply_markup=main_menu()
            )
    except Exception as e:
        logger.error(f"Помилка при оформленні замовлення: {e}", exc_info=True)
        await message.answer("❌ Сталася помилка при оформленні замовлення. Будь ласка, спробуйте ще раз.")
    finally:
        await state.clear()


@dp.message(F.text == "📞 Підтримка")
async def support(message: types.Message):
    await message.answer(
        "📞 Зв'яжіться з нами:\n"
        "Телефон: +380682708322\n"
        "Telegram: @SSWERTYSS",
        reply_markup=main_menu()
    )


# Адмін-панель
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Доступ заборонено")
        return

    await message.answer("👑 Адмін-панель", reply_markup=admin_keyboard())


@dp.message(F.text == "📊 Статистика")
async def show_statistics(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Доступ заборонено")
        return

    stats = get_statistics()
    response = (
        "📊 Статистика магазину:\n\n"
        f"🛒 Всього замовлень: {stats['total_orders']}\n"
        f"💰 Загальний оборот: {stats['total_sales']} грн\n\n"
        "🔥 Топ товарів:\n"
    )

    for i, product in enumerate(stats['popular_products'], 1):
        name, quantity = product
        response += f"{i}. {name} - {quantity} шт.\n"

    await message.answer(response)


@dp.message(F.text == "➕ Додати товар")
async def add_product_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Доступ заборонено")
        return

    await state.set_state(AddProductState.waiting_for_category)
    await message.answer("Оберіть категорію товару:", reply_markup=categories_keyboard())


@dp.message(AddProductState.waiting_for_category, F.text.in_(["рідина Chaser", "Картриджі X-ROS"]))
async def add_product_category(message: types.Message, state: FSMContext):
    await state.update_data(category=message.text)
    await state.set_state(AddProductState.waiting_for_name)
    await message.answer("Введіть назву товару:", reply_markup=back_keyboard())


@dp.message(AddProductState.waiting_for_name, F.text)
async def add_product_name(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await add_product_start(message, state)
        return

    await state.update_data(name=message.text)
    await state.set_state(AddProductState.waiting_for_volume)

    data = await state.get_data()
    category = data.get('category', '')
    await message.answer("Введіть об'єм товару:", reply_markup=volumes_keyboard(category))


@dp.message(AddProductState.waiting_for_volume, F.text)
async def add_product_volume(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await add_product_start(message, state)
        return

    await state.update_data(volume=message.text)
    await state.set_state(AddProductState.waiting_for_price)
    await message.answer("Введіть ціну товару (грн):", reply_markup=back_keyboard())


@dp.message(AddProductState.waiting_for_price, F.text)
async def add_product_price(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        data = await state.get_data()
        category = data.get('category', '')
        await state.set_state(AddProductState.waiting_for_volume)
        await message.answer("Введіть об'єм товару:", reply_markup=volumes_keyboard(category))
        return

    try:
        price = float(message.text)
        await state.update_data(price=price)
        await state.set_state(AddProductState.waiting_for_flavors)
        await message.answer("Введіть смаки (через кому):", reply_markup=back_keyboard())
    except ValueError:
        await message.answer("❌ Будь ласка, введіть число")


@dp.message(AddProductState.waiting_for_flavors, F.text)
async def add_product_flavors(message: types.Message, state: FSMContext):
    if message.text == "Назад":
        await state.set_state(AddProductState.waiting_for_price)
        await message.answer("Введіть ціну товару (грн):", reply_markup=back_keyboard())
        return

    data = await state.get_data()
    product_id = add_product(
        data['category'],
        data['name'],
        data['volume'],
        data['price']
    )

    if product_id:
        add_flavors(product_id, message.text)
        await message.answer("✅ Товар успішно доданий!", reply_markup=admin_keyboard())
    else:
        await message.answer("❌ Помилка при додаванні товару", reply_markup=admin_keyboard())

    await state.clear()


# Запуск бота
async def main():
    # Видаляємо стару базу даних для уникнення конфліктів
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

    # Додаємо тестові дані для демонстрації
    add_test_data()

    await dp.start_polling(bot)


def add_test_data():
    # Додаємо тестові товари
    liquid_10ml_id = add_product("рідина Chaser", "Фруктовий мікс", "10мл", 160)
    if liquid_10ml_id:
        add_flavors(liquid_10ml_id,
                    "м'ята🍃,кавун ментол🍃🍉,вишня ментол🍒🍃,чорниця ментол🫐🍃,жовтий драгон фрукт💛🐲,жовта черешня🍒💛,Гранат🍅,Виноград🍇,Кавун🍉")

    liquid_30ml_id = add_product("рідина Chaser", "Фруктовий мікс", "30мл", 300)
    if liquid_30ml_id:
        add_flavors(liquid_30ml_id,
                    "Виноград🍇,Полуниця🍓,банан полуниця🍓🍌")

    cartridge_id = add_product("Картриджі X-ROS немає", "картридж", "X-ROS", 150)



if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
