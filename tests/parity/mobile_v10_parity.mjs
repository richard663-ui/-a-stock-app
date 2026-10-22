// Reads tick-stream fixtures (JSON) and prints stableModel scores from the
// TypeScript phone model, for comparison with modules/mobile_model_v10.py.
import {readFileSync} from 'node:fs';
import {stableModel} from '../../supabase/functions/astock-mobile/market.ts';

const cases = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const out = cases.map(c => c.cuts.map(cut => {
  const m = stableModel(c.ticks.slice(0, cut));
  return {score60: m.score60, score120: m.score120, ready60: m.ready60, ready120: m.ready120,
          core60: m.score60_raw_current, core120: m.score120_raw_current};
}));
process.stdout.write(JSON.stringify(out));
