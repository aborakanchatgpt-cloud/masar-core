"""
Masar Core — نقاط نهاية "ops" (B1a: منفّذ أوامر عبر قائمة انتظار ملفية)

تسمح لجلسات Claude (أو أي مستدعٍ يملك CORE_ADMIN_TOKEN) بطلب تنفيذ أمر على
الخادم المضيف دون SSH ودون تركيب Docker socket داخل الحاوية: هذه النقطة
تكتب طلب التنفيذ كملف JSON داخل مجلد مشترك مع المضيف (OPS_DIR/queue عبر
bind mount ./ops:/ops)، وسكربت deploy/ops/run_queue.sh على المضيف (يعمل
عبر cron autodeploy كل دقيقتين تقريبًا، بصلاحية root) يقرأه، يتحقق منه
ويُنفّذه ضمن قائمة أوامر مسموح بها بدقّة، ثم يكتب النتيجة في
OPS_DIR/results ليقرأها هذا الملف عبر GET.

التحقق هنا سطحي فقط (الأمر ضمن القائمة، والوسائط نصوص قصيرة) — التحقق
الحقيقي من صحة كل أمر ووسائطه والتنفيذ الفعلي كلاهما على المضيف فقط
(انظر ALLOWED_COMMANDS في run_queue.sh، ويجب أن تبقى القائمتان متطابقتين).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import require_admin_token

router = APIRouter(prefix="/admin/ops", tags=["ops"], dependencies=[Depends(require_admin_token)])

# يجب أن تطابق هذه القائمة بالضبط أسماء الأوامر في deploy/ops/run_queue.sh —
# أي أمر غير موجود هنا يُرفض من هنا مباشرة قبل حتى أن يصل طلبه للمضيف.
ALLOWED_COMMANDS = {
    "ps",
    "logs",
    "restart",
    "up",
    "caddy-reload",
    "deploy-log",
    "backup-log",
    "sys",
    "env-keys",
    "migrate",
    "backup-now",
    "git",
    "script",
    "psql",
}

MAX_ARG_LEN = 2000
MAX_LIST_RESULTS = 20


def _ops_dir() -> Path:
    return Path(os.environ.get("OPS_DIR", "/ops"))


def _ensure_dirs() -> tuple[Path, Path]:
    """ينشئ مجلدي queue وresults إن لم يكونا موجودين — يتجاهل أخطاء
    الصلاحيات (مثلًا إن أنشأهما المضيف مسبقًا بمالك مختلف)."""
    base = _ops_dir()
    queue = base / "queue"
    results = base / "results"
    for d in (base, queue, results):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return queue, results


class OpsRequest(BaseModel):
    cmd: str
    args: list[str] = Field(default_factory=list)


class OpsEnqueueResponse(BaseModel):
    ok: bool
    id: str
    note: str


@router.post("", response_model=OpsEnqueueResponse)
async def enqueue_op(body: OpsRequest) -> OpsEnqueueResponse:
    if body.cmd not in ALLOWED_COMMANDS:
        raise HTTPException(status_code=400, detail=f"أمر غير مسموح به: {body.cmd}")
    if any(len(a) > MAX_ARG_LEN for a in body.args):
        raise HTTPException(
            status_code=400,
            detail=f"كل عنصر في args يجب ألا يتجاوز {MAX_ARG_LEN} حرفًا",
        )

    queue, _results = _ensure_dirs()

    job_id = uuid.uuid4().hex[:16]
    payload = {"id": job_id, "cmd": body.cmd, "args": body.args}

    target = queue / f"{job_id}.json"
    tmp = queue / f"{job_id}.json.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"تعذّرت كتابة الطلب: {exc}") from exc

    return OpsEnqueueResponse(
        ok=True,
        id=job_id,
        note="processed by host cron within ~2 min; poll GET /admin/ops/{id}",
    )


@router.get("/{job_id}")
async def get_op(job_id: str):
    queue, results = _ensure_dirs()

    result_path = results / f"{job_id}.json"
    if result_path.exists():
        try:
            with open(result_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=500, detail=f"تعذّرت قراءة النتيجة: {exc}") from exc
        data["status"] = "done"
        return data

    if (queue / f"{job_id}.running").exists():
        return {"id": job_id, "status": "running"}

    if (queue / f"{job_id}.json").exists():
        return {"id": job_id, "status": "pending"}

    return JSONResponse(status_code=404, content={"status": "unknown"})


@router.get("")
async def list_ops() -> list[dict]:
    _queue, results = _ensure_dirs()

    items: list[dict] = []
    try:
        paths = list(results.glob("*.json"))
    except OSError:
        paths = []

    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        items.append(
            {
                "id": data.get("id"),
                "cmd": data.get("cmd"),
                "exit_code": data.get("exit_code"),
                "finished_at": data.get("finished_at"),
            }
        )

    items.sort(key=lambda x: x.get("finished_at") or "", reverse=True)
    return items[:MAX_LIST_RESULTS]
