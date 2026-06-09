import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, Integer, String, ForeignKey, Text ,DateTime ,  select
from datetime import datetime
DATABASE_URL = "postgresql+asyncpg://admin:123@localhost:5432/testdb"
engine = create_async_engine(DATABASE_URL , echo=True)
async_session = async_sessionmaker(engine , expire_on_commit = False)
Base = declarative_base()
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    password = Column(String, nullable=False)
class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String , nullable = False)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
async def init():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
async def main():
    await init()
if __name__ == "__main__":
    asyncio.run(main())
