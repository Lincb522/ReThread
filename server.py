from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
API_VERSION = 5
SESSION_DIR_NAMES = ("sessions", "archived_sessions")
SESSION_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
MAX_BATCH_SIZE = 1000
WRITE_TIMEOUT_SECONDS = 30
LEGACY_MOJIBAKE_MARKERS = frozenset("¿ÆÑÄÂ¼´×¤µ±¨²ÁÏÔÐÐ")
INTERNAL_USER_PREFIXES = (
    "<environment_context>",
    "# AGENTS.md instructions for ",
    "<permissions instructions>",
    "<app-context>",
    "<collaboration_mode>",
    "<apps_instructions>",
    "<skills_instructions>",
    "<plugins_instructions>",
    "<recommended_plugins>",
    "<personality_spec>",
    "The following is the Codex agent history",
    "The following is Codex agent history",
)
ASSESSMENT_PREFIXES = (
    "The following is the Codex agent history",
    "The following is Codex agent history",
)


def repair_legacy_text(value: str) -> str:
    """Repair old GBK text that was accidentally decoded as Latin-1."""
    if not value or not any(char in LEGACY_MOJIBAKE_MARKERS for char in value):
        return value
    try:
        repaired = value.encode("latin-1").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if repaired else value


def read_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    """Yield valid JSON objects, tolerating partially-written/corrupt lines."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        yield value
                except (json.JSONDecodeError, UnicodeError):
                    continue
    except OSError:
        return


def load_titles(codex_home: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in read_json_lines(codex_home / "session_index.jsonl"):
        session_id = str(item.get("id", ""))
        if session_id:
            result[session_id] = {
                "title": str(item.get("thread_name") or item.get("title") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
    return result


def extract_text(content: Any, exclude_internal: bool = False) -> str:
    if isinstance(content, str):
        if exclude_internal and is_internal_user_message(content):
            return ""
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") in {"input_text", "output_text", "text"}:
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                if exclude_internal and is_internal_user_message(text):
                    continue
                parts.append(text.strip())
    return "\n\n".join(parts)


def is_internal_user_message(text: str) -> bool:
    """Identify Codex-injected user-role records that are not user messages."""
    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in INTERNAL_USER_PREFIXES)


def is_assessment_payload(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and any(text.lstrip().startswith(prefix) for prefix in ASSESSMENT_PREFIXES):
            return True
    return False


def is_temporary_workspace(cwd: str, home: Path | None = None) -> bool:
    """Recognize workspaces Codex creates when the user did not choose a project."""
    if not cwd:
        return True

    def normalize(value: Path | str) -> str:
        text = str(value).strip().replace("\\", "/").rstrip("/")
        return text.casefold()

    user_home = normalize(home or Path.home())
    normalized = normalize(cwd)
    managed_root = f"{user_home}/documents/codex"
    return normalized == user_home or normalized == managed_root or normalized.startswith(managed_root + "/")


def parse_session(path: Path, include_messages: bool = True) -> dict[str, Any]:
    session_id = ""
    cwd = ""
    thread_source = ""
    session_source: Any = ""
    created_at = ""
    updated_at = ""
    messages: list[dict[str, str]] = []

    for event in read_json_lines(path):
        timestamp = str(event.get("timestamp") or "")
        created_at = created_at or timestamp
        updated_at = timestamp or updated_at
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "session_meta":
            session_id = str(payload.get("id") or session_id)
            cwd = repair_legacy_text(str(payload.get("cwd") or cwd))
            thread_source = str(payload.get("thread_source") or thread_source)
            session_source = payload.get("source") or session_source
        if not include_messages or event.get("type") != "response_item":
            continue
        if payload.get("type") != "message" or payload.get("role") not in {"user", "assistant"}:
            continue
        role = str(payload["role"])
        content = payload.get("content")
        if role == "user" and is_assessment_payload(content):
            continue
        text = extract_text(content, exclude_internal=role == "user")
        if not text:
            continue
        phase = str(payload.get("phase") or "")
        if role == "assistant" and phase == "commentary":
            kind = "progress"
        elif role == "assistant" and phase == "final_answer":
            kind = "final"
        elif role == "assistant":
            kind = "legacy"
        else:
            kind = "user"
        message = {
            "role": role,
            "text": text,
            "timestamp": timestamp,
            "kind": kind,
            "phase": phase,
        }
        # Some Codex versions record the same message twice in adjacent events.
        if not messages or (messages[-1]["role"], messages[-1]["text"]) != (message["role"], message["text"]):
            messages.append(message)

    if not session_id:
        match = SESSION_ID_RE.search(path.stem)
        session_id = match.group(0) if match else path.stem
    return {
        "id": session_id,
        "cwd": cwd,
        "thread_source": thread_source,
        "session_source": session_source,
        "is_internal_thread": thread_source == "subagent",
        "created_at": created_at,
        "updated_at": updated_at,
        "message_count": len(messages),
        "messages": messages if include_messages else [],
        "file": str(path),
    }


class SessionStore:
    def __init__(self, codex_home: Path):
        self.codex_home = codex_home.expanduser().resolve()

    def roots(self) -> list[Path]:
        return [(self.codex_home / name).resolve() for name in SESSION_DIR_NAMES]

    def files(self) -> list[Path]:
        result: list[Path] = []
        for root in self.roots():
            if root.is_dir():
                result.extend(root.rglob("*.jsonl"))
        return result

    def is_archived_path(self, path: Path) -> bool:
        archived_root = (self.codex_home / "archived_sessions").resolve()
        try:
            path.resolve().relative_to(archived_root)
            return True
        except ValueError:
            return False

    def find_path(self, session_id: str) -> Path | None:
        if not SESSION_ID_RE.fullmatch(session_id):
            return None
        allowed_roots = self.roots()
        for path in self.files():
            if session_id not in path.name:
                continue
            resolved = path.resolve()
            if not any(self._is_below(resolved, root) for root in allowed_roots):
                return None
            return resolved
        return None

    @staticmethod
    def _is_below(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def list(self, query: str = "", limit: int = 200) -> list[dict[str, Any]]:
        titles = load_titles(self.codex_home)
        rows: list[dict[str, Any]] = []
        needle = query.casefold().strip()
        for path in self.files():
            row = parse_session(path, include_messages=True)
            if row["is_internal_thread"]:
                continue
            indexed = titles.get(row["id"], {})
            first_user = next((m["text"] for m in row["messages"] if m["role"] == "user"), "")
            # Background approval/audit threads have no genuine user message
            # after injected context records are removed.
            if not first_user:
                continue
            row["title"] = repair_legacy_text(indexed.get("title") or first_user.splitlines()[0][:100]) or "未命名会话"
            row["updated_at"] = indexed.get("updated_at") or row["updated_at"]
            row["is_temporary"] = is_temporary_workspace(row["cwd"])
            row["is_archived"] = self.is_archived_path(path)
            if needle and needle not in (row["title"] + " " + row["cwd"] + " " + first_user).casefold():
                continue
            row.pop("messages", None)
            row["local_path"] = row.pop("file", "")
            row.pop("session_source", None)
            row["local_path_exists"] = bool(row["local_path"] and Path(row["local_path"]).is_file())
            rows.append(row)
        rows.sort(key=lambda x: x["updated_at"], reverse=True)
        return rows[: max(1, min(limit, 1000))]

    def get(self, session_id: str) -> dict[str, Any] | None:
        titles = load_titles(self.codex_home)
        path = self.find_path(session_id)
        if path is None:
            return None
        result = parse_session(path)
        indexed = titles.get(session_id, {})
        result["title"] = repair_legacy_text(indexed.get("title") or "") or "未命名会话"
        result["updated_at"] = indexed.get("updated_at") or result["updated_at"]
        result["is_temporary"] = is_temporary_workspace(result["cwd"])
        result["is_archived"] = self.is_archived_path(path)
        result["local_path"] = result.pop("file", str(path))
        result["local_path_exists"] = path.is_file()
        return result

class OperationError(RuntimeError):
    """A safe, user-facing failure from an official Codex operation."""


class OperationBusy(OperationError):
    pass


def resolve_codex_command(explicit: str | None = None) -> str | None:
    requested = explicit or os.environ.get("CODEX_BINARY")
    if requested:
        path = Path(requested).expanduser()
        return str(path.resolve()) if path.is_file() else shutil.which(requested)
    # Native executables preserve app-server's JSONL stdin reliably on Windows.
    # Batch/PowerShell shims remain a fallback for CLI-only installations.
    names = ("codex.exe", "codex.cmd", "codex") if os.name == "nt" else ("codex",)
    return next((found for name in names if (found := shutil.which(name))), None)


class CodexController:
    """Serialize writes and delegate all state changes to the installed Codex."""

    def __init__(self, codex_home: Path, command: str | None = None,
                 cli_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
                 rpc_runner: Callable[[str, dict[str, Any]], Any] | None = None,
                 event_logger: Callable[[str], None] | None = None):
        self.codex_home = codex_home.expanduser().resolve()
        self.command = resolve_codex_command(command) if command else resolve_codex_command()
        self._cli_runner = cli_runner or subprocess.run
        self._rpc_runner = rpc_runner
        self._event_logger = event_logger or self._console_event_logger
        self._write_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.command)

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        return env

    @contextmanager
    def exclusive(self) -> Iterable[None]:
        if not self._write_lock.acquire(blocking=False):
            raise OperationBusy("另一个 Codex 会话管理操作正在执行，请稍后重试")
        try:
            yield
        finally:
            self._write_lock.release()

    def _require_command(self) -> str:
        if not self.command:
            raise OperationError("未找到 Codex CLI；请安装 Codex 或通过 --codex-bin 指定可执行文件")
        return self.command

    @staticmethod
    def _safe_process_error(result: subprocess.CompletedProcess[str]) -> str:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        message = detail[-1][:500] if detail else f"退出码 {result.returncode}"
        return f"Codex 拒绝了该操作：{message}"

    def _emit(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._event_logger(f"[{timestamp}] [Codex History Manager] {message}")

    @staticmethod
    def _format_console_message(message: str, color: bool) -> str:
        if not color:
            return message
        match = re.match(r"^(\[[^]]+\]) (\[Codex History Manager\]) (EXEC|OK|FAIL)(.*)$", message)
        if not match:
            return message
        timestamp, product, status, detail = match.groups()
        status_color = {"EXEC": "\033[1;36m", "OK": "\033[1;32m", "FAIL": "\033[1;31m"}[status]
        return (
            f"\033[2m{timestamp}\033[0m "
            f"\033[1;35m{product}\033[0m "
            f"{status_color}{status}\033[0m{detail}"
        )

    @classmethod
    def _console_event_logger(cls, message: str) -> None:
        print(cls._format_console_message(message, cls.console_colors_enabled()), flush=True)

    @staticmethod
    def console_colors_enabled() -> bool:
        return (
            "NO_COLOR" not in os.environ
            and os.environ.get("TERM", "").lower() != "dumb"
            and (bool(os.environ.get("FORCE_COLOR")) or bool(getattr(sys.stdout, "isatty", lambda: False)()))
        )

    @staticmethod
    def _display_command(args: tuple[str, ...]) -> str:
        values = ["codex", *args]
        return subprocess.list2cmdline(values) if os.name == "nt" else shlex.join(values)

    def _run_cli_unlocked(self, *args: str) -> None:
        command = self._require_command()
        display = self._display_command(args)
        started = time.perf_counter()
        self._emit(f"EXEC official-cli command={display}")
        try:
            result = self._cli_runner(
                [command, *args], env=self._environment(), capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=WRITE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._emit(f"FAIL official-cli command={display} reason=timeout")
            raise OperationError(f"Codex 操作超过 {WRITE_TIMEOUT_SECONDS} 秒，已终止等待") from exc
        except OSError as exc:
            self._emit(f"FAIL official-cli command={display} reason=launch-error")
            raise OperationError(f"无法启动 Codex CLI：{exc}") from exc
        if result.returncode != 0:
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self._emit(f"FAIL official-cli command={display} exit_code={result.returncode} elapsed_ms={elapsed_ms}")
            raise OperationError(self._safe_process_error(result))
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        self._emit(f"OK official-cli command={display} elapsed_ms={elapsed_ms}")

    def _rpc_unlocked(self, method: str, params: dict[str, Any]) -> Any:
        started = time.perf_counter()
        thread_id = str(params.get("threadId") or "")
        privacy = f" title_length={len(str(params.get('name') or ''))}" if "name" in params else ""
        self._emit(f"EXEC official-app-server method={method} thread_id={thread_id}{privacy}")
        if self._rpc_runner:
            try:
                result = self._rpc_runner(method, params)
            except Exception:
                self._emit(f"FAIL official-app-server method={method} thread_id={thread_id} reason=runner-error")
                raise
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self._emit(f"OK official-app-server method={method} thread_id={thread_id} elapsed_ms={elapsed_ms}")
            return result
        command = self._require_command()
        messages = [
            {"method": "initialize", "id": 1, "params": {"clientInfo": {
                "name": "codex_history_manager", "title": "Codex History Manager", "version": "1.0.0"}}},
            {"method": "initialized", "params": {}},
            {"method": method, "id": 2, "params": params},
        ]
        payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in messages)
        try:
            result = self._cli_runner(
                [command, "app-server", "--listen", "stdio://"], input=payload,
                env=self._environment(), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=WRITE_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._emit(f"FAIL official-app-server method={method} thread_id={thread_id} reason=timeout")
            raise OperationError(f"Codex app-server 超过 {WRITE_TIMEOUT_SECONDS} 秒未响应") from exc
        except OSError as exc:
            self._emit(f"FAIL official-app-server method={method} thread_id={thread_id} reason=launch-error")
            raise OperationError(f"无法启动 Codex app-server：{exc}") from exc
        response = None
        for line in (result.stdout or "").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("id") == 2:
                response = item
                break
        if response is None:
            self._emit(f"FAIL official-app-server method={method} thread_id={thread_id} reason=no-response")
            raise OperationError(self._safe_process_error(result))
        if response.get("error"):
            error = response["error"]
            message = str(error.get("message") if isinstance(error, dict) else error)[:500]
            self._emit(f"FAIL official-app-server method={method} thread_id={thread_id} reason=codex-error")
            raise OperationError(f"Codex 拒绝了该操作：{message}")
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        self._emit(f"OK official-app-server method={method} thread_id={thread_id} elapsed_ms={elapsed_ms}")
        return response.get("result")

    @staticmethod
    def validate_id(session_id: str) -> None:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise OperationError("会话 ID 格式无效")

    def rename(self, session_id: str, title: str) -> None:
        self.validate_id(session_id)
        title = title.strip()
        if not title:
            raise OperationError("标题不能为空")
        if len(title) > 200:
            raise OperationError("标题最多 200 个字符")
        with self.exclusive():
            self._rpc_unlocked("thread/name/set", {"threadId": session_id, "name": title})

    def set_archived(self, session_id: str, archived: bool) -> None:
        self.validate_id(session_id)
        with self.exclusive():
            self._run_cli_unlocked("archive" if archived else "unarchive", session_id)

    def delete(self, session_id: str) -> None:
        self.validate_id(session_id)
        with self.exclusive():
            self._run_cli_unlocked("delete", "--force", session_id)

    def delete_many(self, session_ids: list[str]) -> dict[str, Any]:
        for session_id in session_ids:
            self.validate_id(session_id)
        deleted: list[str] = []
        failed: list[dict[str, str]] = []
        with self.exclusive():
            for session_id in session_ids:
                try:
                    self._run_cli_unlocked("delete", "--force", session_id)
                    deleted.append(session_id)
                except OperationError as exc:
                    failed.append({"id": session_id, "error": str(exc)})
        return {"deleted": not failed, "deleted_ids": deleted, "failed": failed,
                "sessions_deleted": len(deleted), "sessions_failed": len(failed)}


class ConfirmationStore:
    """Short-lived, one-time plans bind confirmations to exact session IDs."""

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 256):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._plans: dict[str, tuple[float, tuple[str, ...], str]] = {}
        self._lock = threading.Lock()

    def issue(self, session_ids: list[str], phrase: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._plans = {key: value for key, value in self._plans.items() if value[0] > now}
            while len(self._plans) >= self.max_entries:
                self._plans.pop(next(iter(self._plans)))
            self._plans[token] = (now + self.ttl_seconds, tuple(session_ids), phrase)
        return token

    def consume(self, token: str, session_ids: list[str], phrase: str) -> None:
        with self._lock:
            plan = self._plans.pop(token, None)
        if not plan or plan[0] <= time.monotonic():
            raise OperationError("删除计划已过期或已使用，请重新打开确认对话框")
        if plan[1] != tuple(session_ids) or not secrets.compare_digest(plan[2], phrase):
            raise OperationError("删除确认与原计划不匹配，已拒绝操作")


def is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.rstrip(".").casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_request_target(host_header: str, origin_header: str | None, expected_port: int) -> None:
    try:
        target = urlparse(f"//{host_header}")
        target_port = target.port
    except ValueError as exc:
        raise OperationError("请求 Host 无效") from exc
    if not is_loopback_host(target.hostname) or target_port != expected_port:
        raise OperationError("拒绝非本机或端口不匹配的请求")
    if not origin_header:
        return
    try:
        origin = urlparse(origin_header)
        origin_port = origin.port
    except ValueError as exc:
        raise OperationError("请求 Origin 无效") from exc
    if origin.scheme != "http" or not is_loopback_host(origin.hostname) or origin_port != expected_port:
        raise OperationError("拒绝非同源的浏览器请求")


class Handler(SimpleHTTPRequestHandler):
    store: SessionStore
    controller: CodexController
    confirmations: ConfirmationStore
    csrf_token: str

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def _validate_transport(self) -> bool:
        try:
            validate_request_target(self.headers.get("Host", ""), self.headers.get("Origin"), self.server.server_port)
            return True
        except OperationError as exc:
            self.send_json({"error": str(exc)}, 403)
            return False

    def _require_csrf(self) -> bool:
        supplied = self.headers.get("X-Codex-CSRF", "")
        if supplied and secrets.compare_digest(supplied, self.csrf_token):
            return True
        self.send_json({"error": "CSRF 安全令牌缺失或无效，请刷新页面"}, 403)
        return False

    def _read_json(self, max_bytes: int) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type != "application/json":
            raise ValueError("仅接受 application/json 请求")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效") from exc
        if length < 2 or length > max_bytes:
            raise ValueError("请求内容大小无效")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求根节点必须是 JSON 对象")
        return value

    def _session(self, session_id: str) -> dict[str, Any]:
        self.controller.validate_id(session_id)
        session = self.store.get(session_id)
        if session is None:
            raise FileNotFoundError("会话不存在或已被移动")
        return session

    def _delete_plan(self, session_ids: list[str], group: bool) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(session_ids))
        if not unique_ids:
            raise ValueError("没有可删除的会话")
        if len(unique_ids) > MAX_BATCH_SIZE:
            raise ValueError(f"单次批量删除最多支持 {MAX_BATCH_SIZE} 个会话")
        sessions = [self._session(session_id) for session_id in unique_ids]
        phrase = f"delete-{len(unique_ids)}-{secrets.token_hex(2)}" if group else unique_ids[0][:8]
        token = self.confirmations.issue(unique_ids, phrase)
        return {
            "plan_token": token,
            "confirmation": phrase,
            "short_id": phrase,
            "sessions": len(sessions),
            "files_total": sum(1 for session in sessions if session.get("local_path_exists")),
            "official_codex": True,
            "title": sessions[0]["title"] if len(sessions) == 1 else "",
            "targets": [{"id": item["id"], "title": item["title"]} for item in sessions],
        }

    def _send_operation_error(self, exc: Exception) -> None:
        if isinstance(exc, FileNotFoundError):
            self.send_json({"error": str(exc)}, 404)
        elif isinstance(exc, OperationBusy):
            self.send_json({"error": str(exc)}, 423)
        elif isinstance(exc, OperationError):
            self.send_json({"error": str(exc)}, 409)
        elif isinstance(exc, (ValueError, json.JSONDecodeError, UnicodeError)):
            self.send_json({"error": str(exc)}, 400)
        else:
            self.send_json({"error": "操作失败，请查看服务端日志"}, 500)

    def do_GET(self) -> None:  # noqa: N802
        if not self._validate_transport():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/sessions":
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["200"])[0])
            except ValueError:
                limit = 200
            self.send_json({"sessions": self.store.list(params.get("q", [""])[0], limit)})
            return
        plan_match = re.fullmatch(r"/api/sessions/([^/]+)/delete-plan", parsed.path)
        if plan_match:
            if not self._require_csrf():
                return
            try:
                self.send_json(self._delete_plan([plan_match.group(1)], False))
            except Exception as exc:
                self._send_operation_error(exc)
            return
        if parsed.path.startswith("/api/sessions/"):
            session_id = parsed.path.rsplit("/", 1)[-1]
            session = self.store.get(session_id)
            self.send_json(session or {"error": "会话不存在"}, 200 if session else 404)
            return
        if parsed.path == "/api/status":
            self.send_json({"api_version": API_VERSION, "codex_home": str(self.store.codex_home),
                            "available": (self.store.codex_home / "sessions").is_dir(),
                            "codex_cli_available": self.controller.available, "csrf_token": self.csrf_token})
            return
        super().do_GET()

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._validate_transport():
            return
        self.send_json({"error": "直接 DELETE 已禁用，请先获取删除计划并提交短 ID 确认"}, 405)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._validate_transport():
            return
        self.send_json({"error": "不允许跨源请求"}, 405)

    def do_POST(self) -> None:  # noqa: N802
        if not self._validate_transport() or not self._require_csrf():
            return
        parsed = urlparse(self.path)
        action_match = re.fullmatch(r"/api/sessions/([^/]+)/(rename|archive)", parsed.path)
        if action_match:
            session_id, action = action_match.groups()
            try:
                body = self._read_json(4096)
                self._session(session_id)
                if action == "rename":
                    self.controller.rename(session_id, str(body.get("title") or ""))
                else:
                    archived = body.get("archived")
                    if not isinstance(archived, bool):
                        raise ValueError("archived 必须是布尔值")
                    self.controller.set_archived(session_id, archived)
                self.send_json({"ok": True, "managed_by": "official-codex"})
            except Exception as exc:
                self._send_operation_error(exc)
            return
        if parsed.path in {"/api/session-groups/delete-plan", "/api/session-groups/delete"}:
            try:
                body = self._read_json(256_000)
                session_ids = body.get("session_ids") if isinstance(body, dict) else None
                if not isinstance(session_ids, list) or not all(isinstance(item, str) for item in session_ids):
                    raise ValueError("批量删除的会话 ID 列表无效")
                session_ids = list(dict.fromkeys(session_ids))
                if parsed.path.endswith("delete-plan"):
                    self.send_json(self._delete_plan(session_ids, True))
                else:
                    confirmation = str(body.get("confirmation") or "")
                    self.confirmations.consume(str(body.get("plan_token") or ""), session_ids, confirmation)
                    report = self.controller.delete_many(session_ids)
                    self.send_json(report, 200 if report["deleted"] else 207)
            except Exception as exc:
                self._send_operation_error(exc)
            return
        match = re.fullmatch(r"/api/sessions/([^/]+)/delete", parsed.path)
        if not match:
            self.send_json({"error": "接口不存在"}, 404)
            return
        session_id = match.group(1)
        try:
            body = self._read_json(4096)
            self._session(session_id)
            confirmation = str(body.get("confirmation") or "")
            self.confirmations.consume(str(body.get("plan_token") or ""), [session_id], confirmation)
            self.controller.delete(session_id)
        except Exception as exc:
            self._send_operation_error(exc)
            return
        self.send_json({"deleted": True, "managed_by": "official-codex", "id": session_id})


def _terminal_text_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in value)


def build_startup_banner(url: str, codex_home: Path, command: str | None) -> list[str]:
    rows = [
        "CODEX HISTORY MANAGER",
        "",
        f"Web UI    : {url}",
        f"Reading   : {codex_home}",
        f"Codex CLI : {command or 'not found (read-only mode)'}",
        f"Writes    : {'official Codex CLI / app-server' if command else 'disabled'}",
        "Audit log : EXEC / OK / FAIL with command and elapsed time",
        "Security  : loopback only; keep this terminal open",
        "",
        "by xinleung",
    ]
    content_width = max(_terminal_text_width(row) for row in rows)
    border = f"+{'-' * (content_width + 2)}+"
    return [
        border,
        *(f"| {row}{' ' * (content_width - _terminal_text_width(row))} |" for row in rows),
        border,
    ]


def print_startup_banner(url: str, codex_home: Path, command: str | None) -> None:
    lines = build_startup_banner(url, codex_home, command)
    color = CodexController.console_colors_enabled()
    for index, line in enumerate(lines):
        if color and (index == 0 or index == len(lines) - 1):
            line = f"\033[1;35m{line}\033[0m"
        elif color and "CODEX HISTORY MANAGER" in line:
            line = f"\033[1;36m{line}\033[0m"
        elif color and "by xinleung" in line:
            line = f"\033[2;35m{line}\033[0m"
        print(line, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="在浏览器中查看 Codex 本地历史会话")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--codex-bin", help="Codex CLI 可执行文件路径（也可设置 CODEX_BINARY）")
    args = parser.parse_args()
    if not is_loopback_host(args.host):
        parser.error("出于会话隐私和写操作安全考虑，--host 只允许 localhost 或 loopback IP")
    Handler.store = SessionStore(args.codex_home)
    Handler.controller = CodexController(args.codex_home, args.codex_bin)
    Handler.confirmations = ConfirmationStore()
    Handler.csrf_token = secrets.token_urlsafe(32)
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        # Windows may reserve seemingly unused ports (WinError 10013), while
        # WinError 10048 means another process already owns the port. Let the
        # OS select a free ephemeral port in either case.
        if args.port == 0 or getattr(exc, "winerror", None) not in {10013, 10048} and exc.errno not in {errno.EACCES, errno.EADDRINUSE}:
            raise
        print(f"端口 {args.port} 不可用，正在自动选择可用端口……")
        server = ThreadingHTTPServer((args.host, 0), Handler)
    actual_host, actual_port = server.server_address[:2]
    display_host = "127.0.0.1" if actual_host in {"0.0.0.0", "::"} else actual_host
    print_startup_banner(
        f"http://{display_host}:{actual_port}",
        Handler.store.codex_home,
        Handler.controller.command,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
