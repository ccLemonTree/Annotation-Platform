"""Administrator-only storage inspection and explicitly refused unsafe execution."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_superuser
from database import get_db_session
from services.storage_cleanup import execute_cleanup, preview_cleanup, valid_key

router = APIRouter(dependencies=[Depends(get_current_superuser)])


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paths: list[str] = Field(min_length=1, max_length=100)
    confirmation: Literal['QUARANTINE']

    @field_validator('paths')
    @classmethod
    def known_paths(cls, paths):
        if any(not valid_key(path) for path in paths):
            raise ValueError('仅允许已知images哈希路径')
        return paths


@router.get('/preview')
async def preview(limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db_session)):
    return await preview_cleanup(db, limit)


@router.post('/execute')
async def execute(request: CleanupRequest, db: AsyncSession = Depends(get_db_session)):
    return await execute_cleanup(db, request.paths)
