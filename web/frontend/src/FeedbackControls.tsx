import { GitCompare, LoaderCircle, ThumbsDown, ThumbsUp, Undo2 } from "lucide-react";
import { useState } from "react";
import type { BuildSolution } from "./types";

const REASONS = [
  ["balance", "Баланс характеристик"],
  ["price", "Цена / результат"],
  ["composition", "Состав артефактов"],
  ["availability", "Сложно купить"],
  ["upgrade", "Путь улучшения"],
  ["data_error", "Ошибка характеристик или цены"],
];

export function FeedbackHistory() {
  const [events, setEvents] = useState<Array<{ id: string; rating: number; reason: string; armor: string; container: string; durability: number; speed: number; comparison: boolean }>>([]);
  const [message, setMessage] = useState("");
  async function load() {
    try {
      const response = await fetch("/api/feedback");
      if (!response.ok) throw new Error("Не удалось загрузить оценки");
      const result = await response.json();
      setEvents(result.events);
      setMessage(result.count ? `${result.count} оценок` : "Оценок пока нет");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Ошибка загрузки"); }
  }
  async function undo(id: string) {
    try {
      const response = await fetch(`/api/feedback/${id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("Не удалось отменить оценку");
      await load();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Ошибка отмены"); }
  }
  return <details className="feedback-history" onToggle={(event) => { if (event.currentTarget.open) void load(); }}>
    <summary>Мои оценки</summary>
    <div role="status">{message}</div>
    {events.map((event) => <div className="feedback-history-row" key={event.id}>
      <div><strong>{event.armor}</strong><span>{event.container}</span>
        <span>Приведа {event.durability.toFixed(1)} · скорость {event.speed.toFixed(1)}%</span>
        <small>{event.comparison ? "Лучше другой" : event.rating > 0 ? "Хорошая" : "Плохая"} · {REASONS.find(([key]) => key === event.reason)?.[1]}</small>
      </div>
      <button type="button" title="Отменить оценку" aria-label="Отменить оценку" onClick={() => undo(event.id)}><Undo2 size={16} /></button>
    </div>)}
  </details>;
}

export function FeedbackControls({ searchId, buildId, builds }: {
  searchId: string; buildId: string; builds: BuildSolution[];
}) {
  const [reason, setReason] = useState("balance");
  const [otherId, setOtherId] = useState("");
  const [eventId, setEventId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  async function record(rating: number, compare = false) {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ search_id: searchId, build_id: buildId, rating, reason,
          other_id: compare ? otherId : null }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Не удалось сохранить оценку");
      setEventId(result.id);
      setMessage(result.learning ? "Оценка сохранена" : "Ошибка зарегистрирована, не влияет на обучение");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Ошибка сохранения");
    } finally { setBusy(false); }
  }

  async function undo() {
    setBusy(true);
    try {
      const response = await fetch(`/api/feedback/${eventId}`, { method: "DELETE" });
      if (!response.ok) throw new Error("Не удалось отменить оценку");
      setEventId("");
      setMessage("Оценка отменена");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Ошибка отмены"); }
    finally { setBusy(false); }
  }

  return <div className="feedback-controls">
    <div className="feedback-row">
      <span>Оценка</span>
      <select aria-label="Причина оценки" value={reason} onChange={(event) => setReason(event.target.value)}>
        {REASONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      <button type="button" title="Хорошая сборка" aria-label="Хорошая сборка" disabled={busy || reason === "data_error"} onClick={() => record(1)}><ThumbsUp size={16} /></button>
      <button type="button" title="Плохая сборка" aria-label="Плохая сборка" disabled={busy} onClick={() => record(-1)}><ThumbsDown size={16} /></button>
      {eventId && <button type="button" title="Отменить последнюю оценку" aria-label="Отменить последнюю оценку" disabled={busy} onClick={undo}><Undo2 size={16} /></button>}
      {busy && <LoaderCircle size={16} className="spin-icon" />}
    </div>
    {builds.length > 1 && <div className="feedback-row">
      <select aria-label="Сравнить с другой сборкой" value={otherId} onChange={(event) => setOtherId(event.target.value)}>
        <option value="">Эта сборка лучше, чем...</option>
        {builds.map((build, index) => build.build_id !== buildId && <option key={build.build_id} value={build.build_id}>#{index + 1} · {build.armor.name} · {build.container.name}</option>)}
      </select>
      <button type="button" title="Сохранить сравнение" aria-label="Сохранить сравнение" disabled={busy || !otherId || reason === "data_error"} onClick={() => record(1, true)}><GitCompare size={16} /></button>
    </div>}
    <div className="feedback-message" role="status">{message}</div>
  </div>;
}
