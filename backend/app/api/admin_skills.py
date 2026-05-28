from fastapi import APIRouter
from fastapi import HTTPException

from app.admin.skills import list_admin_skills
from app.admin.skills import upsert_admin_skill
from app.api.state import settings
from app.domain.schemas import SkillOut
from app.domain.schemas import UpsertSkillRequest


router = APIRouter()


@router.get("/api/admin/skills", response_model=list[SkillOut])
async def list_skills() -> list[SkillOut]:
    return list_admin_skills(settings)


@router.put("/api/admin/skills/{name}", response_model=SkillOut)
async def upsert_skill(name: str, body: UpsertSkillRequest) -> SkillOut:
    if name != body.name:
        raise HTTPException(status_code=400, detail="Path name and body name must match")
    return upsert_admin_skill(settings, body.name, body.content)
