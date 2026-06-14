import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect(
        "postgresql://chat_nltz_user:dmsA2fWZ2oZJEaIT7F8RJ5MYpB5HAjfY@dpg-d8kbqfbbc2fs73cdc1hg-a/chat_nltz"
    )
    await conn.execute("DROP TABLE IF EXISTS users CASCADE")
    print("Таблица users удалена")
    await conn.close()

asyncio.run(main())