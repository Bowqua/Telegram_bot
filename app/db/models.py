from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, ForeignKey, Text, JSON, Enum as SAEnum, BigInteger, DateTime
from sqlalchemy.sql import func
import enum

json_type = JSON().with_variant(JSONB, "postgresql")

class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name_ru: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Stone(Base):
    __tablename__ = "stones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name_ru: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    products: Mapped[list["Product"]] = relationship(back_populates="stone")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), index=True)
    price: Mapped[int] = mapped_column(Integer)
    stock: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photos: Mapped[list] = mapped_column(json_type, default=list, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    stone_id:    Mapped[int] = mapped_column(ForeignKey("stones.id"))
    category = relationship("Category", back_populates="products")
    stone    = relationship("Stone",    back_populates="products")


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    cancelled = "cancelled"


class Order(Base):
    __tablename__ = "orders"

    id = mapped_column(Integer, primary_key=True)
    user_id = mapped_column(BigInteger, nullable=False)
    chat_id = mapped_column(BigInteger, nullable=False)
    full_name = mapped_column(String(255), default="")
    username = mapped_column(String(255), default="")
    currency = mapped_column(String(2), default="RUB")
    total_amount = mapped_column(Integer, nullable=False)
    payload = mapped_column(String(128), nullable=False)
    status = mapped_column(SAEnum(OrderStatus), default=OrderStatus.pending, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id = mapped_column(Integer, primary_key=True)
    order_id = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id = mapped_column(ForeignKey("products.id"))
    title = mapped_column(String(255), nullable=False)
    price = mapped_column(Integer, nullable=False)
    qty = mapped_column(Integer, nullable=False)
    photos = mapped_column(json_type, default=list, nullable=False)

    order = relationship("Order", back_populates="items")
