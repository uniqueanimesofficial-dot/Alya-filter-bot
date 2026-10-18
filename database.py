from motor.motor_asyncio import AsyncIOMotorClient

from config import DB_NAME, MONGO_URI

_client = AsyncIOMotorClient(MONGO_URI)
_db = _client[DB_NAME]
filters_col = _db["filters"]
conn_col = _db["connections"]


async def init_db():
    await filters_col.create_index([("group_id", 1), ("keyword", 1)], unique=True)


# ---------------------------------------------------------------- filters
async def save_filter(group_id: int, keyword: str, text, file_type, file_id, buttons):
    await filters_col.update_one(
        {"group_id": group_id, "keyword": keyword},
        {"$set": {"text": text, "file_type": file_type, "file_id": file_id, "buttons": buttons}},
        upsert=True,
    )


async def get_filter(group_id: int, keyword: str):
    return await filters_col.find_one({"group_id": group_id, "keyword": keyword})


async def get_keywords(group_id: int):
    cursor = filters_col.find({"group_id": group_id}, {"keyword": 1})
    kws = [d["keyword"] async for d in cursor]
    # longest first so "good morning" wins over "good"
    return sorted(kws, key=len, reverse=True)


async def delete_filter(group_id: int, keyword: str) -> bool:
    res = await filters_col.delete_one({"group_id": group_id, "keyword": keyword})
    return res.deleted_count > 0


async def delete_all_filters(group_id: int) -> int:
    res = await filters_col.delete_many({"group_id": group_id})
    return res.deleted_count


# ------------------------------------------------------------ connections
async def add_connection(user_id: int, group_id: int):
    await conn_col.update_one(
        {"_id": user_id},
        {"$addToSet": {"groups": group_id}, "$set": {"active": group_id}},
        upsert=True,
    )


async def remove_connection(user_id: int, group_id: int):
    doc = await conn_col.find_one({"_id": user_id})
    if not doc:
        return
    groups = [g for g in doc.get("groups", []) if g != group_id]
    active = doc.get("active")
    if active == group_id:
        active = groups[-1] if groups else None
    await conn_col.update_one({"_id": user_id}, {"$set": {"groups": groups, "active": active}})


async def get_connections(user_id: int):
    doc = await conn_col.find_one({"_id": user_id})
    return (doc or {}).get("groups", []), (doc or {}).get("active")


async def set_active(user_id: int, group_id: int):
    await conn_col.update_one({"_id": user_id}, {"$set": {"active": group_id}})


async def get_active(user_id: int):
    _, active = await get_connections(user_id)
    return active
