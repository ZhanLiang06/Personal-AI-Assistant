/**
 * The finance API sends money as integer minor units plus a preformatted
 * display string, deliberately never as a JSON number. Rendering always uses
 * `display`; `minor` is only ever used for ratios and comparisons, which is
 * the one thing a float cannot get wrong.
 */

export const minor = (money) => money?.minor ?? 0;

/** "MYR 1,284.50". The API sends the digits and the currency separately. */
export function display(money) {
  if (!money) return "—";
  return `${money.currency} ${money.display}`;
}

/** Digits only, for tables and axes where the currency is already stated. */
export const bare = (money) => money?.display ?? "—";

/** Format a total this client computed, in the same shape the API would. */
export function displayMinor(minorUnits, currency = "MYR") {
  const value = (minorUnits / 100).toLocaleString("en-MY", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${currency} ${value}`;
}

/** Only for bar widths and percentages, never for a figure shown to a reader. */
export const ratio = (part, whole) => (whole > 0 ? part / whole : 0);

export function percent(value, digits = 0) {
  return `${(value * 100).toFixed(digits)}%`;
}

/** YYYY-MM for the month the given date falls in. */
export function monthKey(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

export function shiftMonth(key, delta) {
  const [year, month] = key.split("-").map(Number);
  const moved = new Date(year, month - 1 + delta, 1);
  return monthKey(moved);
}

export function monthLabel(key) {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString("en-GB", {
    month: "long",
    year: "numeric",
  });
}

export function daysInMonth(key) {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month, 0).getDate();
}

/** How far through the month we are, 0–1. Past months count as complete. */
export function monthProgress(key) {
  const now = new Date();
  if (monthKey(now) !== key) return 1;
  return now.getDate() / daysInMonth(key);
}

export function shortDate(iso) {
  return new Date(iso).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

export function dayNumber(iso) {
  return Number(iso.slice(8, 10));
}

/** The heading over a day's transactions: "Today", or "Fri 15 Aug". */
export function longDay(iso) {
  const date = iso.slice(0, 10);
  const now = new Date();

  // Compared through localDateTimeValue rather than toISOString, which would
  // shift the boundary by the UTC offset and call last night "yesterday".
  if (date === localDateTimeValue(now).slice(0, 10)) return "today";
  if (date === localDateTimeValue(new Date(now.getTime() - 86_400_000)).slice(0, 10)) {
    return "yesterday";
  }

  return new Date(`${date}T00:00:00`).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "2-digit",
    month: "short",
  });
}

/** "21:02". Transactions recorded without a time land at midnight, and saying
    so is more useful than printing 00:00 as though it were meant. */
export function timeOfDay(iso) {
  const time = iso.slice(11, 16);
  return time === "00:00" || time === "" ? "—" : time;
}

export function relativeDay(iso) {
  const then = new Date(iso);
  const today = new Date();
  const days = Math.floor((today - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return then.toLocaleDateString("en-GB", { weekday: "short" });
  return then.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

export function greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return "good morning";
  if (hour < 18) return "good afternoon";
  return "good evening";
}

export function longDate() {
  return new Date().toLocaleDateString("en-GB", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

/** Local datetime string the finance API accepts, e.g. 2026-08-15T21:02. */
export function localDateTimeValue(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}
