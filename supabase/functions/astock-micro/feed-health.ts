export const MAX_UPLOAD_AGE_SECONDS = 5;
export const MAX_TICK_AGE_SECONDS = 12;

export function tickEpoch(tick) {
  const timestamp = Number(tick?.time);
  if (Number.isFinite(timestamp) && timestamp > 1e12) return timestamp;
  if (Number.isFinite(timestamp) && timestamp > 1e9) return timestamp * 1000;
  return NaN;
}

export function feedHealth(live, now = Date.now()) {
  const ticks = Array.isArray(live?.ticks) ? live.ticks : [];
  const uploadAge = (now - Date.parse(live?.updated_at || '')) / 1000;
  const exchange = tickEpoch(ticks[ticks.length - 1]);
  const tickAge = (now - exchange) / 1000;
  const fresh = Number.isFinite(uploadAge) && Number.isFinite(tickAge)
    && uploadAge >= -2 && uploadAge < MAX_UPLOAD_AGE_SECONDS
    && tickAge >= -2 && tickAge <= MAX_TICK_AGE_SECONDS
    && live?.status === 'online';
  return { fresh,
    stale_seconds: Number.isFinite(uploadAge) ? Math.max(0, uploadAge) : null,
    tick_age_seconds: Number.isFinite(tickAge) ? Math.max(0, tickAge) : null,
    data_time: Number.isFinite(exchange) ? new Date(exchange).toISOString() : null,
    freshness_source: 'exchange_tick_and_upload_time' };
}
