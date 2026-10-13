"""SQLAlchemy persistence with atomic data/checkpoint commits."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .countries import brand_country
from .domain import AutomobileData, BrandData, url_key
from .preprocessing import preprocess_engine, years
from .spec_fields import FLOAT_COLUMNS, INTEGER_COLUMNS, RAW_COLUMNS
from .vehicle_information import (
    ENGINE_TYPE_COLUMNS,
    VEHICLE_BOOLEAN_COLUMNS,
    VEHICLE_COLUMNS,
    VEHICLE_INTEGER_COLUMNS,
    VEHICLE_TEXT_COLUMNS,
    classify_vehicle,
    title_information,
)


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Brand(Timestamps, Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(512), index=True)
    logo: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(128), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Automobile(Timestamps, Base):
    __tablename__ = "automobiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True)
    url: Mapped[str] = mapped_column(Text)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    press_release: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    year_end: Mapped[int | None] = mapped_column(Integer)
    year_source: Mapped[str | None] = mapped_column(String(16))
    for _column in VEHICLE_TEXT_COLUMNS:
        locals()[_column] = mapped_column(
            Text,
            nullable=True,
            index=_column
            in {"body_type", "model_family", "segment", "vehicle_category", "vehicle_type"},
        )
    for _column in VEHICLE_INTEGER_COLUMNS:
        locals()[_column] = mapped_column(Integer, nullable=True)
    for _column in VEHICLE_BOOLEAN_COLUMNS:
        locals()[_column] = mapped_column(Boolean, nullable=True)
    del _column


class Engine(Timestamps, Base):
    __tablename__ = "engines"
    __table_args__ = (UniqueConstraint("automobile_id", "other_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    other_id: Mapped[int] = mapped_column(Integer, index=True)
    automobile_id: Mapped[int] = mapped_column(ForeignKey("automobiles.id"), index=True)
    name: Mapped[str] = mapped_column(String(512))
    # One actual SQL column per source field; the registry also drives preprocessing.
    for _column in RAW_COLUMNS:
        locals()[_column] = mapped_column(
            Text, nullable=True, index=_column in {"fuel", "T_drive_type"}
        )
    engine_type: Mapped[str | None] = mapped_column(String(64), index=True)
    for _column in ENGINE_TYPE_COLUMNS:
        locals()[_column] = mapped_column(
            String(128), nullable=True, index=_column in {"powertrain_type", "aspiration"}
        )
    for _column in INTEGER_COLUMNS:
        locals()[_column] = mapped_column(Integer, nullable=True, index=_column == "cylinders")
    for _column in FLOAT_COLUMNS:
        locals()[_column] = mapped_column(
            Float, nullable=True, index=_column in {"power_kw", "torque_nm", "displacement_cc"}
        )
    del _column


class AutomobilePhoto(Base):
    __tablename__ = "automobile_photos"
    __table_args__ = (UniqueConstraint("automobile_id", "position"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    automobile_id: Mapped[int] = mapped_column(ForeignKey("automobiles.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)


class WorkItem(Base):
    __tablename__ = "scrape_work"
    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


RUN_COUNT_COLUMNS = (
    "brands",
    "discovered",
    "attempted",
    "succeeded",
    "skipped",
    "failed",
    "requests",
    "retried",
)


class Run(Base):
    __tablename__ = "scrape_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    for _column in RUN_COUNT_COLUMNS:
        locals()[_column] = mapped_column(Integer, default=0, nullable=True)
    del _column


class Store:
    def __init__(self, database_url: str):
        parsed = make_url(database_url)
        if parsed.get_backend_name() == "sqlite" and parsed.database not in {None, ":memory:"}:
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(database_url)
        if parsed.get_backend_name() == "sqlite":

            @event.listens_for(self.engine, "connect")
            def sqlite_options(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=30000")

    def initialize(self):
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        models = (Brand, Automobile, Engine, Run, AutomobilePhoto)
        owned = {"brands", "automobiles", "engines"}
        if tables & owned:
            if not owned.issubset(tables):
                raise ValueError("Incomplete existing schema; use a new dedicated database")
            for model in models:
                name = model.__tablename__
                if name not in tables:
                    continue
                columns = {item["name"] for item in inspector.get_columns(name)}
                expected = {column.name for column in model.__table__.columns}
                if not expected.issubset(columns):
                    raise ValueError(
                        f"Incompatible {name} schema; "
                        "run automobile-scraper preprocess for the Python JSON schema"
                    )
                constraints = unique_columns(self.engine, inspector, name)
                if model in {Run, AutomobilePhoto}:
                    continue
                required = ("automobile_id", "other_id") if model is Engine else ("url_hash",)
                if required not in constraints:
                    raise ValueError(
                        f"Incompatible {name} constraints; use a new dedicated database"
                    )
        Base.metadata.create_all(self.engine)
        self.create_vehicle_view()

    def create_vehicle_view(self):
        with self.engine.begin() as connection:
            create_vehicle_view(connection)

    def preprocess(self):
        from .migrations import preprocess_database

        return preprocess_database(self)

    def session(self) -> Session:
        return Session(self.engine)

    def close(self):
        self.engine.dispose()

    @staticmethod
    def save_brand(session: Session, data: BrandData) -> Brand:
        url = str(data.url)
        row = session.scalar(select(Brand).where(Brand.url_hash == url_key(url)))
        if row is None:
            row = Brand(url_hash=url_key(url))
            session.add(row)
        row.url, row.name, row.logo = url, data.name, data.logo
        row.country = data.country or row.country or brand_country(data.name)
        row.deleted_at = None
        session.flush()
        return row

    @staticmethod
    def save_automobile(session: Session, data: AutomobileData, brand_id: int | None = None):
        if brand_id is None:
            brands = session.scalars(select(Brand).where(Brand.deleted_at.is_(None))).all()
            matches = [
                brand for brand in brands if brand.name.casefold() == data.brand_name.casefold()
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"Expected one brand matching {data.brand_name!r}, found {len(matches)}"
                )
            brand_id = matches[0].id
        url = str(data.url)
        row = session.scalar(select(Automobile).where(Automobile.url_hash == url_key(url)))
        if row is None:
            row = Automobile(url_hash=url_key(url))
            session.add(row)
        row.url, row.brand_id, row.name = url, brand_id, data.name
        row.description, row.press_release = data.description, data.press_release
        unknown = data.information.keys() - set(VEHICLE_COLUMNS)
        if unknown:
            raise ValueError(f"Unknown vehicle information fields: {sorted(unknown)}")
        for column, value in title_information(data.name, data.brand_name).items():
            if getattr(row, column) is None:
                setattr(row, column, value)
        for column, value in data.information.items():
            if value is not None:
                setattr(row, column, value)
        for column, value in classify_vehicle(
            {key: getattr(row, key) for key in VEHICLE_COLUMNS}
        ).items():
            setattr(row, column, value)
        for column, value in years(data.name, url).items():
            setattr(row, column, value)
        session.flush()
        session.execute(delete(AutomobilePhoto).where(AutomobilePhoto.automobile_id == row.id))
        for position, photo_url in enumerate(data.photos or []):
            session.add(AutomobilePhoto(automobile_id=row.id, position=position, url=photo_url))
        existing = {
            item.other_id: item
            for item in session.scalars(select(Engine).where(Engine.automobile_id == row.id))
        }
        for item in data.engines:
            engine = existing.pop(item.other_id, None)
            if engine is None:
                engine = Engine(automobile_id=row.id, other_id=item.other_id)
                session.add(engine)
            engine.name = item.name
            for column, value in preprocess_engine(item.specs, item.name, item.columns).items():
                setattr(engine, column, value)
        for stale in existing.values():
            session.delete(stale)
        session.flush()
        return row

    def counts(self) -> dict[str, int]:
        with self.session() as session:
            return {
                model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in (Brand, Automobile, Engine)
            }

    def validate(self) -> dict[str, int]:
        with self.session() as session:
            for child, parent, column in (
                (Automobile, Brand, Automobile.brand_id),
                (Engine, Automobile, Engine.automobile_id),
                (AutomobilePhoto, Automobile, AutomobilePhoto.automobile_id),
            ):
                count = session.scalar(
                    select(func.count())
                    .select_from(child)
                    .outerjoin(parent, column == parent.id)
                    .where(parent.id.is_(None))
                )
                if count:
                    raise ValueError(f"{count} orphan rows in {child.__tablename__}")
        return self.counts()


def create_vehicle_view(connection):
    """One flat row per engine, compiled using the destination's identifier quoting."""
    from sqlalchemy import text

    columns = [
        column
        for column in Engine.__table__.columns
        if column.name not in {"id", "automobile_id", "name"}
    ]
    query = select(
        Engine.id.label("engine_id"),
        Engine.other_id.label("source_engine_id"),
        Automobile.id.label("automobile_id"),
        Automobile.name.label("automobile_name"),
        Engine.name.label("engine_name"),
        Brand.id.label("brand_id"),
        Brand.name.label("brand_name"),
        Brand.country,
        Automobile.year,
        Automobile.year_end,
        Automobile.year_source,
        Automobile.url.label("automobile_url"),
        *(Automobile.__table__.c[column] for column in VEHICLE_COLUMNS),
        *columns,
    ).select_from(
        Engine.__table__.join(Automobile, Engine.automobile_id == Automobile.id).join(
            Brand, Automobile.brand_id == Brand.id
        )
    )
    connection.execute(text("DROP VIEW IF EXISTS vehicle_specs"))
    sql = str(query.compile(dialect=connection.dialect, compile_kwargs={"literal_binds": True}))
    connection.execute(text("CREATE VIEW vehicle_specs AS " + sql))


def unique_columns(engine, inspector, table):
    constraints = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table)}
    constraints.update(
        tuple(item["column_names"]) for item in inspector.get_indexes(table) if item["unique"]
    )
    if engine.dialect.name == "sqlite":
        quote = engine.dialect.identifier_preparer.quote
        with engine.connect() as connection:
            for row in connection.exec_driver_sql(f"PRAGMA index_list({quote(table)})"):
                if row[2]:
                    constraints.add(
                        tuple(
                            item[2]
                            for item in connection.exec_driver_sql(
                                f"PRAGMA index_info({quote(row[1])})"
                            )
                        )
                    )
    return constraints
