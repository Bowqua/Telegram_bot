from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, ForeignKey, UniqueConstraint, Text, JSON

class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name_ru: Mapped[str] = mapped_column(String(128), unique=True, index=True)   # НОВОЕ


class Stone(Base):
    __tablename__ = "stones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name_ru: Mapped[str] = mapped_column(String(128), unique=True, index=True)   # НОВОЕ


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    price: Mapped[int]
    stock: Mapped[int]
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    stone_id: Mapped[int] = mapped_column(ForeignKey("stones.id"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)          # НОВОЕ
    photos: Mapped[list] = mapped_column(JSON, default=list, nullable=False)      # НОВОЕ

    __table_args__ = (
        UniqueConstraint("category_id", "stone_id", "title",
                         name="uq_products_cat_stone_title"),
    )
