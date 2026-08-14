import { useEffect, useState } from "react";
import { getOverview, getToday, listTransactions } from "./api.js";
import { displayMinor, monthKey } from "./format.js";

/**
 * The landing tiles and the retracted header rail read the same two sources,
 * so they are fetched once here and shared. Fetching them per-component meant
 * every mount hit the calendar again.
 */
export function useContextData() {
  const [today, setToday] = useState(null);
  const [todayError, setTodayError] = useState(false);
  const [overview, setOverview] = useState(null);
  const [overviewError, setOverviewError] = useState(false);
  const [weekSpend, setWeekSpend] = useState(null);

  useEffect(() => {
    let live = true;

    getToday()
      .then((data) => live && setToday(data))
      .catch(() => live && setTodayError(true));

    getOverview(monthKey())
      .then((data) => live && setOverview(data))
      .catch(() => live && setOverviewError(true));

    // The last seven days needs its own window: a week straddles months, so it
    // cannot be sliced out of the month's daily totals.
    const end = new Date();
    const start = new Date(end);
    start.setDate(start.getDate() - 6);
    const iso = (date) =>
      `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
        date.getDate(),
      ).padStart(2, "0")}`;

    listTransactions({ start: iso(start), end: iso(end), limit: 500 })
      .then((page) => {
        if (!live) return;
        const expenses = page.transactions.filter((item) => item.direction === "expense");
        const total = expenses.reduce((sum, item) => sum + item.base_amount.minor, 0);
        setWeekSpend(displayMinor(total, expenses[0]?.base_amount.currency ?? "MYR"));
      })
      .catch(() => live && setWeekSpend(null));

    return () => {
      live = false;
    };
  }, []);

  return { today, todayError, overview, overviewError, weekSpend };
}
