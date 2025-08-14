from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, ForeignKey, Text, JSON

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
    description: Mapped[str | None] = mapped_column(Text)
    photos: Mapped[list] = mapped_column(json_type, default=list, nullable=False)

    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    stone_id:    Mapped[int] = mapped_column(ForeignKey("stones.id"))
    category = relationship("Category", back_populates="products")
    stone    = relationship("Stone",    back_populates="products")
