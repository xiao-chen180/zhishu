"""Admin 题库管理 API — 公共题库（exercise_bank）的增删改查

路由前缀: /api/v1/admin/exercises
所有端点均要求管理员权限。
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func as sa_func, or_, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_admin
from app.models.exercise_bank import ExerciseBank
from app.models.student import Student

logger = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_TYPES = {"choice", "judge", "short_answer", "coding"}
MAX_BATCH_SIZE = 200


# ====================================================================
# 请求模型
# ====================================================================

class ExercisePayload(BaseModel):
    question: str
    exercise_type: str = "choice"
    options: Optional[list[str]] = None
    answer: str
    explanation: Optional[str] = None
    difficulty: int = Field(default=50, ge=0, le=100)
    knowledge_point: Optional[str] = None

    @field_validator("question", "answer")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("不能为空")
        return v

    @field_validator("exercise_type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        v = (v or "choice").strip()
        if v not in ALLOWED_TYPES:
            raise ValueError(f"题型必须是 {sorted(ALLOWED_TYPES)} 之一")
        return v

    def normalized_options(self) -> Optional[list[str]]:
        if self.exercise_type != "choice":
            return None
        opts = [o.strip() for o in (self.options or []) if o and o.strip()]
        if len(opts) < 2:
            raise HTTPException(status_code=422, detail="选择题至少需要 2 个非空选项")
        return opts


class BatchImportPayload(BaseModel):
    exercises: list[ExercisePayload]


# ====================================================================
# 工具函数
# ====================================================================

def _to_dict(e: ExerciseBank) -> dict:
    return {
        "id": str(e.id),
        "question": e.question,
        "exercise_type": e.exercise_type,
        "options": e.options,
        "answer": e.answer,
        "explanation": e.explanation,
        "difficulty": e.difficulty if e.difficulty is not None else 50,
        "knowledge_point": e.knowledge_point,
        "source": e.source or "manual",
        "is_active": bool(e.is_active),
        "created_by": str(e.created_by) if e.created_by else None,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


async def _get_or_404(db: AsyncSession, exercise_id: str) -> ExerciseBank:
    try:
        eid = uuid.UUID(exercise_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail=f"无效的题目 ID: {exercise_id}")
    result = await db.execute(select(ExerciseBank).where(ExerciseBank.id == eid))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="题目不存在")
    return item


# ====================================================================
# 端点
# ====================================================================

@router.get("")
async def list_exercises(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    exercise_type: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: Student = Depends(get_current_user),
):
    """分页列出题库题目"""
    require_admin(user)

    conditions = []
    if search:
        kw = f"%{search.strip()}%"
        conditions.append(or_(ExerciseBank.question.ilike(kw), ExerciseBank.knowledge_point.ilike(kw)))
    if exercise_type:
        conditions.append(ExerciseBank.exercise_type == exercise_type)
    if knowledge_point:
        conditions.append(ExerciseBank.knowledge_point == knowledge_point)

    count_stmt = select(sa_func.count()).select_from(ExerciseBank)
    list_stmt = select(ExerciseBank)
    for cond in conditions:
        count_stmt = count_stmt.where(cond)
        list_stmt = list_stmt.where(cond)

    total = (await db.execute(count_stmt)).scalar() or 0
    list_stmt = (
        list_stmt.order_by(ExerciseBank.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(list_stmt)).scalars().all()

    return {
        "items": [_to_dict(e) for e in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/knowledge-points")
async def list_knowledge_points(
    db: AsyncSession = Depends(get_db),
    user: Student = Depends(get_current_user),
):
    """列出题库中已有的知识点（用于筛选下拉框）"""
    require_admin(user)
    stmt = (
        select(ExerciseBank.knowledge_point)
        .where(ExerciseBank.knowledge_point.isnot(None))
        .distinct()
        .order_by(ExerciseBank.knowledge_point)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [kp for kp in rows if kp]


@router.post("")
async def create_exercise(
    payload: ExercisePayload,
    db: AsyncSession = Depends(get_db),
    user: Student = Depends(get_current_user),
):
    """新增一道题目"""
    require_admin(user)

    item = ExerciseBank(
        question=payload.question,
        exercise_type=payload.exercise_type,
        options=payload.normalized_options(),
        answer=payload.answer,
        explanation=payload.explanation,
        difficulty=payload.difficulty,
        knowledge_point=payload.knowledge_point,
        source="manual",
        is_active=True,
        created_by=user.id,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    logger.info("管理员 %s 新增题目 %s", user.student_no, item.id)
    return _to_dict(item)


@router.put("/{exercise_id}")
async def update_exercise(
    exercise_id: str,
    payload: ExercisePayload,
    db: AsyncSession = Depends(get_db),
    user: Student = Depends(get_current_user),
):
    """编辑题目"""
    require_admin(user)
    item = await _get_or_404(db, exercise_id)

    item.question = payload.question
    item.exercise_type = payload.exercise_type
    item.options = payload.normalized_options()
    item.answer = payload.answer
    item.explanation = payload.explanation
    item.difficulty = payload.difficulty
    item.knowledge_point = payload.knowledge_point

    await db.commit()
    await db.refresh(item)
    logger.info("管理员 %s 更新题目 %s", user.student_no, item.id)
    return _to_dict(item)


@router.delete("/{exercise_id}")
async def delete_exercise(
    exercise_id: str,
    db: AsyncSession = Depends(get_db),
    user: Student = Depends(get_current_user),
):
    """删除题目"""
    require_admin(user)
    item = await _get_or_404(db, exercise_id)
    await db.delete(item)
    await db.commit()
    logger.info("管理员 %s 删除题目 %s", user.student_no, exercise_id)
    return {"status": "ok", "message": "题目已删除"}


@router.post("/batch")
async def batch_import(
    payload: BatchImportPayload,
    db: AsyncSession = Depends(get_db),
    user: Student = Depends(get_current_user),
):
    """批量导入题目（JSON 数组）"""
    require_admin(user)

    if not payload.exercises:
        raise HTTPException(status_code=422, detail="导入列表为空")
    if len(payload.exercises) > MAX_BATCH_SIZE:
        raise HTTPException(status_code=422, detail=f"单次最多导入 {MAX_BATCH_SIZE} 道")

    # 先整体校验选项，避免写一半失败
    prepared = []
    for idx, ex in enumerate(payload.exercises, start=1):
        try:
            options = ex.normalized_options()
        except HTTPException as exc:
            raise HTTPException(status_code=422, detail=f"第 {idx} 条: {exc.detail}")
        prepared.append(
            ExerciseBank(
                question=ex.question,
                exercise_type=ex.exercise_type,
                options=options,
                answer=ex.answer,
                explanation=ex.explanation,
                difficulty=ex.difficulty,
                knowledge_point=ex.knowledge_point,
                source="import",
                is_active=True,
                created_by=user.id,
            )
        )

    db.add_all(prepared)
    await db.commit()
    logger.info("管理员 %s 批量导入 %d 道题目", user.student_no, len(prepared))
    return {"status": "ok", "created": len(prepared)}