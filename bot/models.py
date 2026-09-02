"""Модели данных: клиенты, услуги, записи и заявки."""

from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Текущее время UTC без таймзоны — единый формат хранения дат."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Клиент бота, id совпадает с telegram id."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    full_name: Mapped[str | None] = mapped_column(String(128), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    is_blocked: Mapped[bool] = mapped_column(default=False)


class Service(Base):
    """Услуга прайса: длительность в минутах и цена в рублях."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    duration_min: Mapped[int]
    price: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True)
    sort: Mapped[int] = mapped_column(default=0)


class Booking(Base):
    """Запись клиента на услугу."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    starts_at: Mapped[datetime] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    reminded_24h: Mapped[bool] = mapped_column(default=False)
    reminded_2h: Mapped[bool] = mapped_column(default=False)

    service: Mapped[Service] = relationship(lazy="selectin")
    user: Mapped[User] = relationship(lazy="selectin")

    # Гонка двойной брони: два одновременных запроса на одно время не пройдут оба
    __table_args__ = (
        Index("uq_active_slot", "starts_at", unique=True, sqlite_where=text("status='active'")),
    )


class Lead(Base):
    """Заявка «задать вопрос» — обрабатывается менеджером вручную."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(128))
    question: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="new")
