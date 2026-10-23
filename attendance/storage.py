"""SQLite storage for roster snapshots and immutable confirmed sessions."""
import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

STATUSES = {"present", "absent", "late", "excused"}


class ConflictError(ValueError):
    pass


def text(value, field, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field}不能为空且不能超过{limit}个字符")
    if any(ord(c) < 32 for c in value):
        raise ValueError(f"{field}包含控制字符")
    return value.strip()


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_roster(value):
    if not isinstance(value, str) or len(value) > 200_000:
        raise ValueError("名单CSV内容无效或过大")
    reader = csv.DictReader(io.StringIO(value.lstrip("\ufeff")))
    if reader.fieldnames != ["student_id", "name"]:
        raise ValueError("CSV表头必须为 student_id,name")
    roster, seen = [], set()
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError("CSV列数不一致")
        sid, name = text(row["student_id"], "学号", 80), text(row["name"], "姓名", 80)
        if sid in seen:
            raise ValueError("名单中存在重复学号")
        seen.add(sid)
        roster.append({"student_id": sid, "name": name})
        if len(roster) > 1000:
            raise ValueError("单节课名单不能超过1000人")
    if not roster:
        raise ValueError("名单不能为空")
    return roster


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, class_name TEXT NOT NULL,
                    teacher TEXT NOT NULL, scheduled_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'draft',
                    confirmed_at TEXT, observed_count INTEGER, observation_at TEXT,
                    observation_source TEXT, note TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS records (
                    session_id TEXT NOT NULL REFERENCES sessions(id), student_id TEXT NOT NULL,
                    name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    PRIMARY KEY(session_id, student_id)
                );
                CREATE INDEX IF NOT EXISTS sessions_created ON sessions(created_at DESC);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("请求必须为JSON对象")
        fields = {k: text(payload.get(k), k) for k in ("title", "class_name", "teacher", "scheduled_at")}
        try:
            when = datetime.fromisoformat(fields["scheduled_at"])
        except ValueError as exc:
            raise ValueError("课程时间必须为ISO日期时间") from exc
        if when.tzinfo is None:
            raise ValueError("课程时间必须包含时区")
        roster = parse_roster(payload.get("roster_csv"))
        sid = uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO sessions(id,title,class_name,teacher,scheduled_at,created_at) VALUES(?,?,?,?,?,?)",
                         (sid, fields["title"], fields["class_name"], fields["teacher"], when.isoformat(), timestamp()))
            conn.executemany("INSERT INTO records(session_id,student_id,name) VALUES(?,?,?)",
                             [(sid, r["student_id"], r["name"]) for r in roster])
        return self.get(sid)

    def get(self, sid):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            if row is None:
                raise KeyError("课程记录不存在")
            result = dict(row)
            result["records"] = [dict(r) for r in conn.execute(
                "SELECT student_id,name,status FROM records WHERE session_id=? ORDER BY student_id", (sid,))]
        return result

    def list(self, query=""):
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("查询文本过长")
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("""
                SELECT s.*, COUNT(r.student_id) AS roster_size FROM sessions s
                LEFT JOIN records r ON r.session_id=s.id
                WHERE instr(s.title,?)>0 OR instr(s.class_name,?)>0
                GROUP BY s.id ORDER BY s.created_at DESC, s.id LIMIT 200
            """, (query, query))]

    def confirm(self, sid, payload, observation):
        if not isinstance(payload, dict):
            raise ValueError("请求必须为JSON对象")
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise ValueError("缺少有效记录版本号")
        statuses = payload.get("statuses")
        if not isinstance(statuses, dict) or any(not isinstance(v, str) or v not in STATUSES for v in statuses.values()):
            raise ValueError("考勤状态无效")
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError("备注不能超过1000个字符")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = conn.execute("SELECT revision,state FROM sessions WHERE id=?", (sid,)).fetchone()
            if session is None:
                raise KeyError("课程记录不存在")
            if session["state"] != "draft" or session["revision"] != revision:
                raise ConflictError("记录已确认或版本已变化，请重新读取")
            ids = {r[0] for r in conn.execute("SELECT student_id FROM records WHERE session_id=?", (sid,))}
            if set(statuses) != ids:
                raise ValueError("必须逐一确认完整名单，不得增加或遗漏学号")
            conn.executemany("UPDATE records SET status=? WHERE session_id=? AND student_id=?",
                             [(status, sid, student) for student, status in statuses.items()])
            conn.execute("""UPDATE sessions SET state='confirmed',revision=revision+1,confirmed_at=?,
                         observed_count=?,observation_at=?,observation_source=?,note=? WHERE id=?""",
                         (timestamp(), observation.get("count"), observation.get("observed_at"),
                          observation.get("source"), note.strip(), sid))
        return self.get(sid)

    def export(self, sid):
        session = self.get(sid)
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["session_id", "class_name", "title", "scheduled_at", "student_id", "name", "status", "confirmed_at"])
        def cell(value):
            value = str(value or "")
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
        for row in session["records"]:
            writer.writerow([cell(v) for v in (sid, session["class_name"], session["title"], session["scheduled_at"],
                                               row["student_id"], row["name"], row["status"], session["confirmed_at"])])
        return "\ufeff" + output.getvalue()
