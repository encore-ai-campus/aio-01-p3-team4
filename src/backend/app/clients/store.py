from asyncio import sleep
from functools import wraps
from psycopg import AsyncConnection, OperationalError
from psycopg.rows import dict_row
from app.core.config import DATABASE_URL

def retry_db(operation):
    @wraps(operation)
    async def run(*args, **kwargs):
        for delay in (1, 2, 3, 4, None):
            try:
                return await operation(*args, **kwargs)
            except OperationalError:
                if delay is None:
                    raise
                await sleep(delay)
    return run


@retry_db
async def query(sql, params=(), fetch=False):
    async with await AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as db:
        cursor = await db.execute(sql, params)
        return await cursor.fetchall() if fetch else None


@retry_db
async def claim_and_store_answers(job_id: str, answers: dict[str, str]):
    """needs_user 작업을 선점하고 답변 전체를 한 트랜잭션으로 저장한다.

    이미 다른 요청이 작업을 선점했다면 None을 반환한다. INSERT 중 하나라도
    실패하면 연결 컨텍스트가 트랜잭션 전체를 롤백하므로 작업이 running에
    고정되거나 일부 답변만 저장되지 않는다.
    """
    async with await AsyncConnection.connect(DATABASE_URL, row_factory=dict_row) as db:
        cursor = await db.execute(
            "UPDATE jobs SET status='running' "
            "WHERE id=%s AND status='needs_user' RETURNING id",
            (job_id,),
        )
        if await cursor.fetchone() is None:
            return None

        for question_id, answer in answers.items():
            await db.execute(
                "INSERT INTO user_inputs(job_id,question_id,answer) VALUES (%s,%s,%s)",
                (job_id, question_id, answer),
            )

        cursor = await db.execute(
            "SELECT question_id,answer FROM user_inputs WHERE job_id=%s ORDER BY id",
            (job_id,),
        )
        return await cursor.fetchall()
