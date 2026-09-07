"""
Masar Core — جسر MCP ("Masar MCP bridge"، B1b)

خادم MCP بسيط (Streamable HTTP، JSON-RPC 2.0، المواصفتان 2025-03-26
و2025-06-18) مبني يدويًا هنا (بدون حزمة mcp الخارجية) — الهدف تحرير جلسات
Claude من الاعتماد على n8n Cloud (الذي أوشك رصيد التنفيذ التجريبي فيه على
النفاد) عبر موصّل مخصّص (Custom Connector) يتحدث مباشرة مع Core عبر HTTPS.

نقطة الوصول: POST /mcp/{token} — التوكن هو MCP_BRIDGE_TOKEN من البيئة
(مقارنة بزمن ثابت). غياب التوكن بالبيئة = تعطيل كامل (503)؛ توكن خاطئ = 404
(لا نُسرّب حتى وجود النقطة لمن لا يملك التوكن الصحيح). لا حاجة لبث SSE هنا —
GET تُرفض بـ 405، وDELETE تُقبل بلا أثر (بعض عملاء MCP يرسلها لإنهاء الجلسة).

الأدوات المتاحة تعمل على مستودع الكود (تركيب read-only ./:/repo:ro) وعلى
قائمة انتظار "ops" الموجودة أصلًا من B1a (OPS_DIR/queue، ينفّذها
deploy/ops/run_queue.sh على المضيف) — الكتابة في المستودع تمر عبر
"staging" (OPS_DIR/stage/<id>/) ثم أمر "commit" الذي يضيف الملفات فعليًا
لشجرة git على المضيف ويعمل commit + push، إذ لا SSH ولا صلاحية كتابة على
المستودع من داخل حاوية core نفسها إطلاقًا.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import hmac
import inspect
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.ops import ALLOWED_COMMANDS, MAX_ARG_LEN

logger = logging.getLogger("masar.mcp_bridge")

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSIONS = {"2025-03-26", "2025-06-18"}
DEFAULT_PROTOCOL_VERSION = "2025-03-26"


def _repo_dir() -> Path:
    return Path(os.environ.get("REPO_DIR", "/repo"))


def _ops_dir() -> Path:
    return Path(os.environ.get("OPS_DIR", "/ops"))


def _bridge_token() -> str | None:
    tok = os.environ.get("MCP_BRIDGE_TOKEN", "")
    return tok or None


def _constant_time_eq(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


def _safe_rel_path(path: str) -> Path | None:
    """يرفض المسارات الخطرة (تصعيد للأعلى بـ.. ، .git/، ops/، .env*) —
    يُرجع مسارًا نسبيًا صالحًا أو None. يُستخدم لكل من القراءة والسرد والكتابة."""
    if not path:
        return None
    p = path.replace("\\", "/").lstrip("/")
    if not p or p == ".":
        return Path(".")
    parts = p.split("/")
    if any(part in ("..", "") for part in parts[:-1]) or parts[-1] == "..":
        return None
    if p == ".git" or p.startswith(".git/"):
        return None
    if p == "ops" or p.startswith("ops/"):
        return None
    if Path(p).name.startswith(".env") or p == ".env" or p.startswith(".env"):
        return None
    return Path(p)


def _resolve_in_repo(rel: Path) -> Path:
    base = _repo_dir().resolve()
    full = (_repo_dir() / rel).resolve()
    full.relative_to(base)  # يرفع ValueError إن خرج المسار عن الجذر
    return full


def _enqueue(cmd: str, args: list[str]) -> str:
    queue = _ops_dir() / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:16]
    payload = {"id": job_id, "cmd": cmd, "args": args}
    target = queue / f"{job_id}.json"
    tmp = queue / f"{job_id}.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, target)
    return job_id


def _job_result(job_id: str) -> dict:
    results = _ops_dir() / "results"
    queue = _ops_dir() / "queue"
    result_path = results / f"{job_id}.json"
    if result_path.exists():
        try:
            with open(result_path, encoding="utf-8") as f:
                data = json.load(f)
            data["status"] = "done"
            return data
        except (OSError, json.JSONDecodeError) as exc:
            return {"id": job_id, "status": "error", "error": str(exc)}
    if (queue / f"{job_id}.running").exists():
        return {"id": job_id, "status": "running"}
    if (queue / f"{job_id}.json").exists():
        return {"id": job_id, "status": "pending"}
    return {"id": job_id, "status": "unknown"}


# ---------------------------------------------------------------------------
# تنفيذ الأدوات — كل دالة تُرجع نصًا أو dict (يُحوّل لـ JSON) داخل
# content=[{type:"text"}]، أو ترفع ValueError برسالة تُعرض isError:true.
# ---------------------------------------------------------------------------


def _tool_repo_read(args: dict) -> str:
    path = str(args.get("path", ""))
    rel = _safe_rel_path(path)
    if rel is None:
        raise ValueError(f"مسار غير مسموح به: {path}")
    try:
        full = _resolve_in_repo(rel)
    except ValueError as exc:
        raise ValueError(f"مسار غير صالح: {path}") from exc
    if not full.is_file():
        raise ValueError(f"ملف غير موجود: {path}")

    data = full.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    text = data.decode("utf-8", errors="replace")
    truncated = len(text) > 300_000
    if truncated:
        text = text[:300_000]
    header = f"# {path} (chars={len(text)} sha256={sha}" + (" truncated=true)" if truncated else ")") + "\n"
    return header + text


def _tool_repo_list(args: dict) -> str:
    sub = str(args.get("dir", "") or "")
    pattern = str(args.get("pattern", "") or "")
    rel = _safe_rel_path(sub) if sub else Path(".")
    if rel is None:
        raise ValueError(f"مسار غير مسموح به: {sub}")
    try:
        start = _resolve_in_repo(rel)
    except ValueError as exc:
        raise ValueError(f"مسار غير صالح: {sub}") from exc
    if not start.is_dir():
        raise ValueError(f"مجلد غير موجود: {sub}")

    base = _repo_dir().resolve()
    skip_dirs = {".git", "ops", "__pycache__", "node_modules"}
    lines: list[str] = []
    for root, dirs, files in os.walk(start):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            fp = Path(root) / name
            relp = fp.relative_to(base).as_posix()
            if _safe_rel_path(relp) is None:
                continue
            if pattern and not fnmatch.fnmatch(relp, pattern):
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                size = -1
            lines.append(f"{relp}\t{size}")
    lines.sort()
    return "\n".join(lines) if lines else "(empty)"


def _tool_repo_write(args: dict) -> dict:
    files = args.get("files")
    message = str(args.get("message", "") or "").strip()
    if not isinstance(files, list) or not files:
        raise ValueError("files يجب أن تكون قائمة غير فارغة")
    if len(files) > 60:
        raise ValueError("الحد الأقصى 60 ملفًا لكل طلب")
    if not message:
        raise ValueError("message مطلوبة")

    total_chars = 0
    checked: list[tuple[Path, str]] = []
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("كل عنصر في files يجب أن يكون كائنًا {path, content}")
        path = str(item.get("path", ""))
        content = item.get("content")
        if content is None:
            raise ValueError(f"content مفقود للملف: {path}")
        content = str(content)
        rel = _safe_rel_path(path)
        if rel is None or str(rel) in (".", ""):
            raise ValueError(f"مسار غير مسموح به: {path}")
        total_chars += len(content)
        checked.append((rel, content))

    if total_chars > 2_000_000:
        raise ValueError("الحد الأقصى 2,000,000 حرف إجماليًا لكل طلب")

    stage_id = uuid.uuid4().hex[:16]
    stage_dir = _ops_dir() / "stage" / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)

    for rel, content in checked:
        dest = stage_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    job_id = _enqueue("commit", [stage_id, message])

    return {
        "job_id": job_id,
        "stage_id": stage_id,
        "files": len(checked),
        "note": "host commits+pushes within ~2 min; poll job(job_id); server redeploys automatically on success",
    }


def _tool_ops(args: dict) -> dict:
    cmd = str(args.get("cmd", ""))
    op_args = args.get("args", [])
    if not isinstance(op_args, list):
        raise ValueError("args يجب أن تكون قائمة")
    op_args = [str(a) for a in op_args]
    if cmd not in ALLOWED_COMMANDS:
        raise ValueError(f"أمر غير مسموح به: {cmd}")
    if any(len(a) > MAX_ARG_LEN for a in op_args):
        raise ValueError(f"كل عنصر في args يجب ألا يتجاوز {MAX_ARG_LEN} حرفًا")
    job_id = _enqueue(cmd, op_args)
    return {"job_id": job_id}


def _tool_job(args: dict) -> dict:
    job_id = str(args.get("id", ""))
    if not job_id:
        raise ValueError("id مطلوب")
    return _job_result(job_id)


async def _tool_wait(args: dict) -> dict:
    try:
        seconds = int(args.get("seconds", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("seconds يجب أن يكون عددًا صحيحًا") from exc
    if seconds < 1 or seconds > 300:
        raise ValueError("seconds يجب أن يكون بين 1 و300")
    await asyncio.sleep(seconds)
    return {"slept": seconds}


async def _tool_core_call(args: dict) -> dict:
    path = str(args.get("path", ""))
    method = str(args.get("method", "GET") or "GET").upper()
    body = args.get("body", {}) or {}
    if not path.startswith("/"):
        path = "/" + path
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError(f"method غير مدعومة: {method}")

    admin_token = os.environ.get("CORE_ADMIN_TOKEN", "")
    headers = {}
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"

    url = f"http://127.0.0.1:8000{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=headers, json=body if method != "GET" else None)

    text = resp.text
    truncated = len(text) > 100_000
    if truncated:
        text = text[:100_000]
    return {"status": resp.status_code, "body": text, "truncated": truncated}


def _tool_server_time(_args: dict) -> dict:
    return {"utc": datetime.now(timezone.utc).isoformat()}


ToolFn = Callable[[dict], Any]

TOOLS: dict[str, dict[str, Any]] = {
    "repo_read": {
        "description": "قراءة ملف نصي من المستودع (مسار نسبي إلى الجذر).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "fn": _tool_repo_read,
    },
    "repo_list": {
        "description": "سرد الملفات داخل مجلد بالمستودع (بحث تكراري)، مع فلترة اختيارية بنمط fnmatch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dir": {"type": "string", "default": ""},
                "pattern": {"type": "string", "default": ""},
            },
        },
        "fn": _tool_repo_list,
    },
    "repo_write": {
        "description": (
            "كتابة/تعديل ملفات في المستودع عبر staging + قائمة انتظار الأوامر على "
            "المضيف، الذي ينسخ الملفات ويعمل commit ويدفع (push) تلقائيًا خلال "
            "دقيقتين تقريبًا. استخدم job(id) لمتابعة الحالة."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
                "message": {"type": "string"},
            },
            "required": ["files", "message"],
        },
        "fn": _tool_repo_write,
    },
    "ops": {
        "description": "تنفيذ أمر مسموح به على المضيف عبر قائمة انتظار ops (نفس قائمة /admin/ops).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["cmd"],
        },
        "fn": _tool_ops,
    },
    "job": {
        "description": "الاستعلام عن نتيجة مهمة سابقة (ops أو repo_write) بمعرّفها.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
        "fn": _tool_job,
    },
    "wait": {
        "description": "انتظار عدد ثوانِ محدد (1-300) — مفيد للاستطلاع بعد أوامر بطيئة.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "integer", "minimum": 1, "maximum": 300}},
            "required": ["seconds"],
        },
        "fn": _tool_wait,
    },
    "core_call": {
        "description": "استدعاء داخلي لخدمة Core نفسها (http://127.0.0.1:8000) بتوكن الإدارة.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "method": {"type": "string", "default": "GET"},
                "body": {"type": "object", "default": {}},
            },
            "required": ["path"],
        },
        "fn": _tool_core_call,
    },
    "server_time": {
        "description": "الوقت الحالي بتوقيت UTC.",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": _tool_server_time,
    },
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 dispatch
# ---------------------------------------------------------------------------


def _jsonrpc_result(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _jsonrpc_error(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


async def _dispatch(msg: Any) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        bad_id = msg.get("id") if isinstance(msg, dict) else None
        return _jsonrpc_error(bad_id, -32600, "Invalid Request")

    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg
    params = msg.get("params") or {}

    if isinstance(method, str) and method.startswith("notifications/"):
        return None

    if method == "initialize":
        client_version = params.get("protocolVersion") if isinstance(params, dict) else None
        version = client_version if client_version in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "masar-mcp-bridge", "version": "0.1.0"},
        }
        return None if is_notification else _jsonrpc_result(msg_id, result)

    if method == "ping":
        return None if is_notification else _jsonrpc_result(msg_id, {})

    if method == "tools/list":
        tools = [
            {"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]}
            for name, spec in TOOLS.items()
        ]
        return None if is_notification else _jsonrpc_result(msg_id, {"tools": tools})

    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        arguments = params.get("arguments") if isinstance(params, dict) else None
        arguments = arguments if isinstance(arguments, dict) else {}
        logger.info("mcp tools/call: %s", name)
        spec = TOOLS.get(name) if isinstance(name, str) else None
        if spec is None:
            payload = {"content": [{"type": "text", "text": f"أداة غير معروفة: {name}"}], "isError": True}
        else:
            try:
                fn: ToolFn = spec["fn"]
                result = fn(arguments)
                if inspect.isawaitable(result):
                    result = await result
                text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                payload = {"content": [{"type": "text", "text": text}], "isError": False}
            except ValueError as exc:
                payload = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            except Exception as exc:  # noqa: BLE001 — خطأ أداة لا يجب أن يُسقط الاتصال
                logger.exception("mcp tool error: %s", name)
                payload = {"content": [{"type": "text", "text": f"خطأ داخلي: {exc}"}], "isError": True}
        return None if is_notification else _jsonrpc_result(msg_id, payload)

    if is_notification:
        return None
    return _jsonrpc_error(msg_id, -32601, f"Method not found: {method}")


async def _handle_body(body: Any) -> Any:
    """يُرجع dict (رد واحد)، أو list (batch)، أو None إن كانت كل الرسائل
    إشعارات (notifications) بلا رد متوقع — عندها يجب أن يرسل المستدعي 202."""
    if isinstance(body, list):
        if not body:
            return _jsonrpc_error(None, -32600, "Invalid Request")
        responses = [r for r in (await asyncio.gather(*(_dispatch(item) for item in body))) if r is not None]
        return responses if responses else None
    return await _dispatch(body)


@router.post("/mcp/{token}")
async def mcp_post(token: str, request: Request):
    expected = _bridge_token()
    if not expected:
        return JSONResponse(status_code=503, content={"error": "bridge disabled"})
    if not _constant_time_eq(token, expected):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — JSON تالف
        return JSONResponse(status_code=200, content=_jsonrpc_error(None, -32700, "Parse error"))

    result = await _handle_body(body)
    if result is None:
        return Response(status_code=202, content=b"")
    return JSONResponse(status_code=200, content=result)


@router.get("/mcp/{token}")
async def mcp_get(token: str):
    expected = _bridge_token()
    if not expected:
        return JSONResponse(status_code=503, content={"error": "bridge disabled"})
    if not _constant_time_eq(token, expected):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return JSONResponse(status_code=405, content={"detail": "Method Not Allowed — use POST"})


@router.delete("/mcp/{token}")
async def mcp_delete(token: str):
    expected = _bridge_token()
    if not expected:
        return JSONResponse(status_code=503, content={"error": "bridge disabled"})
    if not _constant_time_eq(token, expected):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return Response(status_code=200)
