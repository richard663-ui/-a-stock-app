export const MAX_GAP_SECONDS = 12;

export function exchangeTime(tick) {
  const raw = Number(tick?.time);
  if (Number.isFinite(raw) && raw > 1e12) return raw;
  if (Number.isFinite(raw) && raw > 1e9) return raw * 1000;
  const tag = String(tick?.timetag || '');
  return /(?:Z|[+-]\d\d:\d\d)$/.test(tag) ? Date.parse(tag) || 0 : 0;
}

export function sessionKey(timestamp) {
  const date = new Date(timestamp + 8 * 3600000);
  const minute = date.getUTCHours() * 60 + date.getUTCMinutes();
  if ([0, 6].includes(date.getUTCDay())) return null;
  const session = minute >= 570 && minute < 690 ? 'AM'
    : minute >= 780 && minute < 900 ? 'PM' : null;
  return session ? date.toISOString().slice(0, 10) + '/' + session : null;
}

export function validQuote(tick) {
  const bid = Number(tick?.bidPrice?.[0] ?? tick?.bidPrice1);
  const ask = Number(tick?.askPrice?.[0] ?? tick?.askPrice1);
  const price = Number(tick?.lastPrice);
  return [price,bid,ask].every(Number.isFinite) && price > 0 && bid > 0 && ask >= bid;
}

// Never fill missing seconds with row counts, prior sessions, or capture timestamps.
export function prepareTicks(input) {
  const ticks = [];
  let invalid = 0, duplicates = 0, resets = 0, lastTime = 0;
  for (const tick of Array.isArray(input) ? input : []) {
    const timestamp = exchangeTime(tick), session = sessionKey(timestamp);
    if (!timestamp || !session || !validQuote(tick) || timestamp < lastTime) {
      invalid++;
      continue;
    }
    if (ticks.length && timestamp === lastTime) {
      ticks[ticks.length - 1] = tick;
      duplicates++;
      continue;
    }
    const previous = ticks[ticks.length - 1];
    if (previous && (sessionKey(lastTime) !== session
        || timestamp - lastTime > MAX_GAP_SECONDS * 1000
        || Number(tick.volume) < Number(previous.volume)
        || Number(tick.amount) < Number(previous.amount))) {
      ticks.length = 0;
      resets++;
    }
    ticks.push(tick);
    lastTime = timestamp;
  }
  return {ticks, invalid_rows: invalid, duplicate_timestamps: duplicates, window_resets: resets,
    latest_valid_exchange_time:lastTime};
}

export function timeWindow(ticks, seconds) {
  if (!ticks?.length) return [];
  const end = exchangeTime(ticks[ticks.length - 1]);
  return ticks.filter(tick => {
    const timestamp=exchangeTime(tick);
    return timestamp>0 && timestamp<=end && timestamp>=end-seconds*1000;
  });
}

export function historicalCutoff(ticks, secondsAgo) {
  if (!ticks?.length) return [];
  const cutoff = exchangeTime(ticks[ticks.length - 1]) - secondsAgo * 1000;
  return ticks.filter(tick => exchangeTime(tick) <= cutoff);
}

export function completeWindow(ticks, seconds) {
  if (!ticks?.length) return false;
  const span = (exchangeTime(ticks[ticks.length - 1]) - exchangeTime(ticks[0])) / 1000;
  const recent = timeWindow(ticks, seconds);
  return span >= seconds && recent.length >= (seconds === 120 ? 40 : 20);
}
