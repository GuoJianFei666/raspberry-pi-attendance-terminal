"use strict";
let current = null;
let liveBusy = false;
const labels = {pending: "待确认", present: "到课", absent: "缺勤", late: "迟到", excused: "请假"};
const $ = id => document.getElementById(id);

function message(value, error = false) {
  $("message").textContent = value;
  $("message").classList.toggle("error", error);
  $("message").hidden = false;
}

async function api(path, payload) {
  const options = payload === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)};
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}

async function listSessions() {
  const rows = await api("/api/sessions?q=" + encodeURIComponent($("search").value));
  $("sessions").replaceChildren();
  for (const row of rows) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.textContent = `${row.title} · ${row.class_name}`;
    const small = document.createElement("small");
    small.textContent = `${new Date(row.scheduled_at).toLocaleString()} · ${row.state === "confirmed" ? "已确认" : "待确认"}`;
    button.append(small);
    button.addEventListener("click", () => openSession(row.id).catch(error => message(error.message, true)));
    item.append(button);
    $("sessions").append(item);
  }
  if (!rows.length) $("sessions").textContent = "暂无课程记录";
}

async function openSession(id) {
  current = await api("/api/sessions/" + id);
  $("empty").hidden = true;
  $("detail").hidden = false;
  $("session-info").textContent = `${current.title} / ${current.class_name} / ${current.teacher} / ${new Date(current.scheduled_at).toLocaleString()}`;
  const observed = current.observed_count === null ? "未记录有效视觉人数" : `确认时可见人数 ${current.observed_count}`;
  $("record-count").textContent = `名单 ${current.records.length} 人 · ${current.state === "confirmed" ? observed : "待逐项确认"}`;
  $("records").replaceChildren();
  for (const record of current.records) {
    const row = document.createElement("tr");
    for (const value of [record.student_id, record.name]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    const cell = document.createElement("td");
    const select = document.createElement("select");
    select.dataset.studentId = record.student_id;
    select.setAttribute("aria-label", `${record.name}的考勤状态`);
    for (const [value, label] of Object.entries(labels)) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
    select.value = record.status;
    select.disabled = current.state === "confirmed";
    cell.append(select);
    row.append(cell);
    $("records").append(row);
  }
  $("note").value = current.note;
  $("note").disabled = current.state === "confirmed";
  $("confirm").disabled = current.state === "confirmed";
  $("confirm").textContent = current.state === "confirmed" ? "本节课考勤已保存" : "确认并保存本节课考勤";
  $("export").href = `/api/sessions/${current.id}/export.csv`;
  $("export").hidden = false;
}

async function live() {
  if (liveBusy) return;
  liveBusy = true;
  try {
    const state = await api("/api/live");
    $("count").textContent = state.count === null ? "—" : String(state.count);
    const names = {unavailable: "摄像头未启用，可继续确认名单", running: "视觉计数正在更新", stale: "画面已过期，人数暂不可用", error: "视觉采集已停止"};
    $("live-state").textContent = state.error ? `${names[state.state]}：${state.error}` : names[state.state];
    $("capture-time").textContent = state.observed_at ? `最近采集 ${new Date(state.observed_at).toLocaleTimeString()} · 窗口 ${state.sample_count} 帧` : "人数仅辅助核对，个人状态由教师确认。";
    $("preview").hidden = state.state !== "running";
    $("preview-note").hidden = state.state === "running";
    if (state.state === "running") $("preview").src = "/api/frame.jpg?t=" + Date.now();
  } catch (error) {
    $("count").textContent = "—";
    $("live-state").textContent = "服务连接失败：" + error.message;
    $("preview").hidden = true;
    $("preview-note").hidden = false;
  } finally {
    liveBusy = false;
  }
}

$("create-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const fields = new FormData(event.target);
    const file = $("roster-file").files[0];
    if (!file || file.size > 200000) throw new Error("请选择不超过200KB的CSV名单");
    const payload = Object.fromEntries(["title", "class_name", "teacher"].map(key => [key, fields.get(key)]));
    payload.scheduled_at = new Date(fields.get("scheduled_at")).toISOString();
    payload.roster_csv = await file.text();
    const session = await api("/api/sessions", payload);
    await listSessions();
    await openSession(session.id);
    message("记录已创建，请逐一确认学生考勤状态。");
  } catch (error) { message(error.message, true); }
  finally { button.disabled = false; }
});

$("confirm").addEventListener("click", async () => {
  $("confirm").disabled = true;
  try {
    const statuses = Object.fromEntries([...$("records").querySelectorAll("select")].map(select => [select.dataset.studentId, select.value]));
    if (Object.values(statuses).includes("pending")) throw new Error("请完成名单中每位学生的状态确认");
    await api(`/api/sessions/${current.id}/confirm`, {revision: current.revision, statuses, note: $("note").value});
    await openSession(current.id);
    await listSessions();
    message("本节课考勤已保存。");
  } catch (error) {
    message(error.message, true);
    $("confirm").disabled = current && current.state === "confirmed";
  }
});
$("refresh").addEventListener("click", () => listSessions().catch(error => message(error.message, true)));
$("search").addEventListener("keydown", event => {
  if (event.key === "Enter") { event.preventDefault(); listSessions().catch(error => message(error.message, true)); }
});
$("preview").addEventListener("error", () => { $("preview").hidden = true; $("preview-note").hidden = false; });
const now = new Date();
now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
document.querySelector('[name="scheduled_at"]').value = now.toISOString().slice(0, 16);
listSessions().catch(error => message(error.message, true));
live();
setInterval(live, 1500);
