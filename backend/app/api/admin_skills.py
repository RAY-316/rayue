from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

from app.admin.skills import list_admin_skills
from app.admin.skills import upsert_admin_skill
from app.auth import get_current_user
from app.api.state import settings
from app.domain.models import User
from app.domain.schemas import SkillOut
from app.domain.schemas import UpsertSkillRequest


router = APIRouter()


@router.get("/api/admin/skills", response_model=list[SkillOut])
async def list_skills(_user: User = Depends(get_current_user)) -> list[SkillOut]:
    return list_admin_skills(settings)


@router.put("/api/admin/skills/{name}", response_model=SkillOut)
async def upsert_skill(
    name: str,
    body: UpsertSkillRequest,
    _user: User = Depends(get_current_user),
) -> SkillOut:
    if name != body.name:
        raise HTTPException(status_code=400, detail="Path name and body name must match")
    return upsert_admin_skill(settings, body.name, body.content)
