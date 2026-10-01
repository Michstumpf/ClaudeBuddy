"""In-memory model of Claude Code sessions and pending approvals."""

import asyncio
import re
import time
import uuid
from dataclasses import asdict, dataclass, field

from .local_sessions import parse_tmux

# Commands that must never be approved by voice, only by a deliberate tap.
DANGEROUS_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f",
    r"\brm\s+-[a-z]*f[a-z]*r",
    r"\bgit\s+push\b.*(--force|-f\b)",
    r"\bgit\s+reset\s+--hard",
    r"\bgit\s+clean\s+-[a-z]*f",
    r"\bdrop\s+(table|database)\b",
    r"\b(terraform|tofu)\s+(apply|destroy)\b",
    r"\bkubectl\s+delete\b",
    r"\bdeploy\b",
    r"\bsudo\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]
_DANGEROUS_RE = re.compile("|".join(DANGEROUS_PATTERNS), re.IGNORECASE)

LAST_MESSAGE_MAX = 280


def summarize_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Bash":
        return str(tool_input.get("command", ""))[:200]
    if tool_name in ("Edit", "Write", "MultiEdit", "Read", "NotebookEdit"):
        return str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if tool_name in ("WebFetch",):
        return str(tool_input.get("url", ""))
    for value in tool_input.values():
        if isinstance(value, str) and value:
            return value[:200]
    return ""


def is_dangerous(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "Bash":
        return False
    return bool(_DANGEROUS_RE.search(str(tool_input.get("command", ""))))


@dataclass
class Session:
    id: str
    name: str
    host: str
    cwd: str
    status: str = "idle"  # idle | working | waiting | done | offline
    last_message: str = ""
    tmux_pane: str | None = None
    permission_mode: str | None = None
    updated_at: float = field(default_factory=time.time)
    # Name given with /rename, from the local session registry. Wins over the
    # tmux session / folder name.
    title: str | None = None


@dataclass
class Approval:
    id: str
    session_id: str
    session_name: str
    tool_name: str
    summary: str
    dangerous: bool
    expires_at: float
    future: asyncio.Future = field(repr=False, compare=False)

    def public(self) -> dict:
        # Not asdict(): it would try to deep-copy the asyncio.Future.
        return {f: getattr(self, f) for f in self.__dataclass_fields__ if f != "future"}


class Hub:
    def __init__(self, approval_timeout: float = 20.0):
        self.approval_timeout = approval_timeout
        self.sessions: dict[str, Session] = {}
        self.approvals: dict[str, Approval] = {}
        self.devices: set = set()  # connected websockets
        self._listeners: list = []  # async callables(snapshot, event)
        self._local_ids: set[str] = set()  # sessions seen in the local registry

    # ---- snapshot / broadcast -------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "type": "state",
            "sessions": [asdict(s) for s in sorted(self.sessions.values(), key=lambda s: -s.updated_at)],
            "pending": [a.public() for a in self.approvals.values()],
            "devices": len(self.devices),
        }

    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    async def notify(self, event: dict | None = None) -> None:
        snap = self.snapshot()
        for fn in list(self._listeners):
            await fn(snap, event)

    # ---- hook events ----------------------------------------------------------

    def _upsert(self, payload: dict) -> Session:
        sid = payload.get("session_id") or "unknown"
        extra = payload.get("buddy", {}) or {}
        cwd = payload.get("cwd", "")
        derived = extra.get("tmux_session") or cwd.replace("\\", "/").rstrip("/").split("/")[-1]
        session = self.sessions.get(sid)
        if session is None:
            session = Session(id=sid, name=derived or sid[:8], host=extra.get("host", "?"), cwd=cwd)
            self.sessions[sid] = session
        session.name = session.title or derived or session.name
        session.cwd = cwd or session.cwd
        session.host = extra.get("host", session.host)
        session.tmux_pane = extra.get("tmux_pane") or session.tmux_pane
        session.permission_mode = payload.get("permission_mode", session.permission_mode)
        session.updated_at = time.time()
        return session

    async def handle_hook(self, payload: dict) -> dict:
        """Apply a Claude Code hook event. Returns the hook's response body."""
        event = payload.get("hook_event_name", "")
        session = self._upsert(payload)
        response: dict = {}
        ui_event = None

        if event == "SessionStart":
            session.status = "idle"
        elif event == "UserPromptSubmit":
            session.status = "working"
        elif event == "PostToolUse":
            if session.status == "waiting":
                session.status = "working"
        elif event == "Notification":
            ntype = payload.get("notification_type", "")
            if ntype == "permission_prompt":
                session.status = "waiting"
                ui_event = {"kind": "attention", "session": session.name}
            elif ntype == "idle_prompt":
                session.status = "idle"
        elif event == "Stop":
            session.status = "done"
            msg = (payload.get("last_assistant_message") or "").strip()
            if msg:
                session.last_message = msg[:LAST_MESSAGE_MAX]
            ui_event = {"kind": "done", "session": session.name}
        elif event == "SessionEnd":
            session.status = "offline"
        elif event == "PermissionRequest":
            decision = await self.request_approval(session, payload)
            response = {"decision": decision}
            return response  # request_approval already notified

        await self.notify(ui_event)
        return response

    # ---- local session registry ----------------------------------------------

    def reconcile(self, entries: list[dict], host: str) -> bool:
        """Merge ~/.claude/sessions entries (see local_sessions.py). Returns True if anything changed.

        A registry status only overrides the hub when it is newer than the last
        hook event, so a hook that just arrived is never undone by a stale file.
        """
        before = self.snapshot()
        seen = set()
        for entry in entries:
            sid = entry["sessionId"]
            seen.add(sid)
            tmux_name, pane = parse_tmux(entry.get("tmux"))
            # A /rename name always wins; Claude Code's derived name (e.g. "git-d6")
            # only beats the bare folder name when there is no tmux session name.
            title = entry.get("name") if entry.get("nameSource") == "user" or not tmux_name else None
            stamp = (entry.get("statusUpdatedAt") or entry.get("updatedAt") or 0) / 1000
            session = self.sessions.get(sid)
            if session is None:
                cwd = entry.get("cwd", "")
                session = Session(
                    id=sid,
                    name=tmux_name or cwd.rstrip("/").split("/")[-1] or sid[:8],
                    host=host,
                    cwd=cwd,
                    status="working" if entry.get("status") == "busy" else "idle",
                    tmux_pane=pane,
                    updated_at=stamp,
                )
                self.sessions[sid] = session
            self._local_ids.add(sid)
            if title:
                session.title = session.name = title
            session.tmux_pane = session.tmux_pane or pane

            status = entry.get("status")
            if stamp <= session.updated_at:
                continue
            if status == "idle" and session.status in ("working", "waiting", "offline"):
                session.status, session.updated_at = "idle", stamp
            elif status == "busy" and session.status in ("idle", "done", "waiting", "offline"):
                session.status, session.updated_at = "working", stamp
            elif status == "waiting" and session.status in ("idle", "done", "working", "offline"):
                # A permission dialog is open in the terminal / Remote Control.
                session.status, session.updated_at = "waiting", stamp

        for sid in self._local_ids - seen:  # process gone without SessionEnd
            self._local_ids.discard(sid)
            if sid in self.sessions:
                self.sessions[sid].status = "offline"
        return self.snapshot() != before

    # ---- approvals ------------------------------------------------------------

    async def request_approval(self, session: Session, payload: dict) -> str | None:
        """Wait for a tap on a Buddy. None means: no decision, show the normal dialog."""
        if not self.devices:
            return None
        tool_name = payload.get("tool_name", "?")
        tool_input = payload.get("tool_input", {}) or {}
        loop = asyncio.get_running_loop()
        approval = Approval(
            id=uuid.uuid4().hex[:12],
            session_id=session.id,
            session_name=session.name,
            tool_name=tool_name,
            summary=summarize_tool(tool_name, tool_input),
            dangerous=is_dangerous(tool_name, tool_input),
            expires_at=time.time() + self.approval_timeout,
            future=loop.create_future(),
        )
        self.approvals[approval.id] = approval
        prev_status = session.status
        session.status = "waiting"
        await self.notify({"kind": "approval", "session": session.name, "id": approval.id})
        try:
            return await asyncio.wait_for(approval.future, timeout=self.approval_timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.approvals.pop(approval.id, None)
            if session.status == "waiting":
                session.status = "working" if prev_status != "waiting" else prev_status
            await self.notify(None)

    def decide(self, approval_id: str, behavior: str, via: str = "touch") -> bool:
        approval = self.approvals.get(approval_id)
        if approval is None or approval.future.done():
            return False
        if behavior not in ("allow", "deny"):
            return False
        # Dangerous commands are only approvable with a deliberate tap.
        if behavior == "allow" and approval.dangerous and via != "touch":
            return False
        approval.future.set_result(behavior)
        return True
