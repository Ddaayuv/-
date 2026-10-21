import re
import time
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event, inspect, select, text, update)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from .config import settings
from .utils import key, normalize

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_active: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name_ar: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100), default="")
    emoji: Mapped[str] = mapped_column(String(8), default="📘")
    aliases: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    has_practical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
class Section(Base):
    __tablename__ = "sections"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    name_ar: Mapped[str] = mapped_column(String(100))
    emoji: Mapped[str] = mapped_column(String(8), default="📄")
    aliases: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    week_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[str] = mapped_column(String(300), default="")
    search_text: Mapped[str] = mapped_column(Text, default="")
    is_important: Mapped[bool] = mapped_column(Boolean, default=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    study_mode: Mapped[str] = mapped_column(String(20), default="general", nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    subject: Mapped[Subject] = relationship(lazy="selectin")
    section: Mapped[Section] = relationship(lazy="selectin")
    attachments: Mapped[list["Attachment"]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by="Attachment.id")
class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tg_file_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="admin")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(50))
    detail: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
class BotSetting(Base):
    __tablename__ = "bot_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

class MenuButton(Base):
    __tablename__ = "menu_buttons"
    id: Mapped[int] = mapped_column(primary_key=True)
    menu_key: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Quiz(Base):
    __tablename__ = "quizzes"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String(200), default="اختبار من النص")
    original_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    questions: Mapped[list["QuizQuestion"]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by="QuizQuestion.number")


class QuizQuestion(Base):
    __tablename__ = "quiz_questions"
    __table_args__ = (UniqueConstraint("quiz_id", "number", name="uq_quiz_question_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    options_json: Mapped[str] = mapped_column(Text)
    correct_option_index: Mapped[int] = mapped_column(Integer)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), index=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    total_questions: Mapped[int] = mapped_column(Integer)
    answered_count: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class QuizPoll(Base):
    __tablename__ = "quiz_polls"
    __table_args__ = (UniqueConstraint("attempt_id", "question_number", name="uq_attempt_question_poll"),)
    poll_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("quiz_attempts.id", ondelete="CASCADE"), index=True)
    question_number: Mapped[int] = mapped_column(Integer)
    correct_option_index: Mapped[int] = mapped_column(Integer)


class QuizResponse(Base):
    __tablename__ = "quiz_responses"
    __table_args__ = (UniqueConstraint("attempt_id", "question_number", name="uq_attempt_question_response"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("quiz_attempts.id", ondelete="CASCADE"), index=True)
    question_number: Mapped[int] = mapped_column(Integer)
    selected_option_index: Mapped[int] = mapped_column(Integer)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


connect_args = {"timeout": 30} if settings.db_url.startswith("sqlite") else {}
engine = create_async_engine(settings.db_url, connect_args=connect_args, pool_pre_ping=True)
if settings.db_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor(); cur.execute("PRAGMA journal_mode=WAL"); cur.execute("PRAGMA foreign_keys=ON"); cur.close()
Session = async_sessionmaker(engine, expire_on_commit=False)
SECTIONS = [("lectures","المحاضرات","🎥","محاضرات,محاضره,فيديو,lectures,lecture,video"),("notes","الملاحظات","📝","ملاحظات,ملخصات,ملخص,notes,summary"),("practical","العملي","🧪","عملي,lab,labs,practical"),("files","الملفات","📄","ملفات,files,file,pdf"),("questions","الأسئلة","❓","اسئله,اسئلة,مراجعه,questions,quiz"),("key_points","أهم النقاط","⭐","مهم,اهم النقاط,important"),("resources","المصادر","📚","مصادر,مراجع,resources")]
SUBJECTS = [("linux","أساسيات Linux","Linux Basics","🐧","لينكس,لينوكس,linux",True),("cyber-basics","أساسيات الأمن السيبراني","Cybersecurity Basics","🛡","الامن السيبراني,امن سيبراني,cybersecurity,security",True),("crypto","أساسيات التشفير","Cryptography Basics","🔐","التشفير,اساسيات التشفير,cryptography,crypto",False),("web-dev","برمجة المواقع","Web Development","🌐","ويب,web,برمجه مواقع",True),("statistics","الإحصاء","Statistics","📊","الاحصاء,احتمالية,احتمالات,الاحصاء الاحتمالي,statistics,stats",False),("pentest","اختبار الاختراق","Penetration Testing","🎯","pentest,penetration testing,اختراق",True),("arabic","اللغة العربية","Arabic","📖","عربي,العربيه,arabic",False),("networks","أساسيات الشبكات","Networking Basics","📡","الشبكات,شبكات,networking,network",True),("english","اللغة الإنكليزية","English","🔤","الانجليزيه,انجليزي,انكليزي,english",False),("ethics","الأخلاقيات","Ethics","⚖️","اخلاقيات,اخلاقيات مهنية,المهنة,ethics",False),("python","Python","Python","🐍","بايثون,بايثن,البايثون للامن السيبراني,python",True),("human-rights","حقوق الإنسان","Human Rights","🕊","حقوق الانسان,human rights",False)]
DEFAULT_SETTINGS={"channel_id":str(settings.channel_id or ""),"channel_link":settings.channel_link,"force_sub":str(settings.require_membership).lower(),"new_user_alerts":"true","announce":str(settings.announce).lower()}
def _sync_migrations(conn):
    inspector=inspect(conn); subject_columns={c["name"] for c in inspector.get_columns("subjects")} if inspector.has_table("subjects") else set(); item_columns={c["name"] for c in inspector.get_columns("items")} if inspector.has_table("items") else set(); add_practical="has_practical" not in subject_columns; add_mode="study_mode" not in item_columns
    if add_practical: conn.execute(text(f"ALTER TABLE subjects ADD COLUMN has_practical BOOLEAN NOT NULL DEFAULT {'0' if conn.dialect.name=='sqlite' else 'FALSE'}"))
    if add_mode: conn.execute(text("ALTER TABLE items ADD COLUMN study_mode VARCHAR(20) NOT NULL DEFAULT 'general'"))
    return add_practical,add_mode
def snapshot_sqlite() -> str | None:
    """Copy the SQLite file next to itself before migrations so nothing can be lost silently."""
    if not settings.db_url.startswith("sqlite"): return None
    import os, shutil, glob
    path=settings.db_path
    if not path or path==":memory:" or not os.path.exists(path) or os.path.getsize(path)==0: return None
    folder=os.path.join(os.path.dirname(path) or ".","snapshots"); os.makedirs(folder,exist_ok=True)
    dest=os.path.join(folder,f"bot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
    shutil.copy2(path,dest)
    for old in sorted(glob.glob(os.path.join(folder,"bot-*.db")))[:-5]: os.remove(old)
    return dest
async def db_counts(session) -> dict:
    from sqlalchemy import func
    return {"users":await session.scalar(select(func.count(User.id))) or 0,"items":await session.scalar(select(func.count(Item.id))) or 0,"attachments":await session.scalar(select(func.count(Attachment.id))) or 0}
async def init_db():
    snapshot_sqlite()
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all); added_practical,added_mode=await conn.run_sync(_sync_migrations)
    async with Session() as s:
        if not await s.scalar(select(Section.id).limit(1)):
            for i,(slug,ar,em,al) in enumerate(SECTIONS): s.add(Section(slug=slug,name_ar=ar,emoji=em,aliases=al,sort_order=i))
        if not await s.scalar(select(Subject.id).limit(1)):
            for i,(slug,ar,en,em,al,practical) in enumerate(SUBJECTS): s.add(Subject(slug=slug,name_ar=ar,name_en=en,emoji=em,aliases=al,sort_order=i,has_practical=practical))
        await s.flush()
        if added_practical:
            for slug,*_rest,practical in SUBJECTS: await s.execute(update(Subject).where(Subject.slug==slug).values(has_practical=practical))
        # Repair legacy content classifications on every startup. This only updates fields;
        # it never deletes users, subjects, items, or attachments.
        await s.execute(text("UPDATE items SET study_mode = 'practical' WHERE section_id IN (SELECT id FROM sections WHERE slug = 'practical')"))
        await s.execute(text("UPDATE items SET study_mode = 'theory' WHERE study_mode = 'general' AND subject_id IN (SELECT id FROM subjects WHERE has_practical = TRUE)"))
        practical_section_id=await s.scalar(select(Section.id).where(Section.slug=="practical")); lecture_section_id=await s.scalar(select(Section.id).where(Section.slug=="lectures"))
        if practical_section_id and lecture_section_id: await s.execute(update(Item).where(Item.section_id==practical_section_id).values(section_id=lecture_section_id))
        for k,v in DEFAULT_SETTINGS.items():
            if await s.get(BotSetting,k) is None: s.add(BotSetting(key=k,value=v))

        await s.commit()
async def touch_user(session,tg_user):
    u=await session.scalar(select(User).where(User.telegram_id==tg_user.id)); now=utcnow()
    if u is None: session.add(User(telegram_id=tg_user.id,name=tg_user.full_name,username=tg_user.username,joined_at=now,last_active=now)); await session.flush(); return True
    u.name,u.username,u.last_active=tg_user.full_name,tg_user.username,now; return False
async def get_setting(session,name,default=""):
    row=await session.get(BotSetting,name); return row.value if row is not None else default
async def set_setting(session,name,value):
    row=await session.get(BotSetting,name)
    if row is None: session.add(BotSetting(key=name,value=str(value)))
    else: row.value=str(value); row.updated_at=utcnow()
async def setting_enabled(session,name,default=False): return (await get_setting(session,name,str(default).lower())).strip().lower() in {"1","true","yes","on"}
async def is_admin(session,uid):
    if uid in settings.owner_ids: return True
    return await session.scalar(select(Admin.id).where(Admin.telegram_id==uid)) is not None
async def audit(session,admin_id,action,detail=""): session.add(AuditLog(admin_id=admin_id,action=action,detail=detail))
async def get_item(session,item_id): return await session.scalar(select(Item).where(Item.id==item_id))
def _match(rows,name,extra_fields):
    k=key(name)
    if not k:return None
    for r in rows:
        cands=[getattr(r,f) for f in extra_fields]+(r.aliases or "").split(",")
        if k in {key(c) for c in cands if c}:return r
    if len(k)>=3:
        hits=[r for r in rows if any(k in key(c) for c in [getattr(r,f) for f in extra_fields] if c)]
        if len(hits)==1:return hits[0]
    return None
async def find_subject(session,name): return _match((await session.scalars(select(Subject))).all(),name,["slug","name_ar","name_en"])
async def find_section(session,name): return _match((await session.scalars(select(Section))).all(),name,["slug","name_ar"])
def build_search_text(item,subject,section):
    return normalize(" ".join([item.title or "",item.description or "",item.tags or "",subject.name_ar,subject.name_en,subject.aliases.replace(","," "),section.name_ar,section.aliases.replace(","," "),{"practical":"عملي مختبر تطبيقي","theory":"نظري نظريه","general":"عام"}.get(item.study_mode,"")]))
async def create_item(session,d,admin_id):
    subject=await session.get(Subject,d["subject_id"]); section=await session.get(Section,d["section_id"])
    if subject is None or section is None: raise ValueError("المادة أو القسم غير موجود في قاعدة البيانات")
    item=Item(subject_id=subject.id,section_id=section.id,title=d["title"],description=d.get("desc",""),week_no=d.get("week"),tags=d.get("tags",""),is_important=d.get("important",False),study_mode=d.get("study_mode","general"),created_by=admin_id); item.attachments=[Attachment(**a) for a in d["attachments"]]; item.search_text=build_search_text(item,subject,section); session.add(item); await session.flush(); return item
def slugify(text_value): return re.sub(r"[^a-z0-9]+","-",text_value.lower()).strip("-") or f"s{int(time.time())}"
