# Point-in-time A-share fundamentals (`%USERPROFILE%\AStockData\fundamentals`)

Research data only. Built by `research/tools/fundamentals_fetch.py` (branch
`research/fundamentals-pit`); verification numbers in
`docs/research/FUNDAMENTALS_PIT_VERIFICATION.md`. A copy of this file sits next
to the data as `README.md`.

## The one rule

Join fundamentals to prices **only through `available_date`** (or `strict_date`
for the conservative check), with the helpers in
`research/tools/fundamentals_pit.py`:

```python
pit = pd.read_parquet(r"~/AStockData/fundamentals/reports_pit.parquet")
# value of one period as known on a date (restatements published later are invisible)
fp.asof_period(pit, queries[["symbol", "period_end", "date"]])
# most recent period known on a date (a late restatement of an old year never displaces a newer quarter)
fp.asof_latest(pit, queries[["symbol", "date"]])
```

A figure dated D may drive a decision after the close of D; trade at the next
session at the earliest. Never join on `period_end`, and never use
`reports_latest.parquet` values with its `notice_date` as if they were known then.

## Why there are versions

Eastmoney serves the **latest revised** figures under the **first** notice
date. Two kinds of revision reach its tables:

- announced corrections, e.g. 康美药业 FY2017: first published on 2018-04-26
  with revenue 26.5bn and 归母净利润 4.1bn, cut to 17.6bn and 2.1bn by the 2019
  correction; Eastmoney shows the corrected figures dated 2018-04-26;
- **silent restatements**: comparatives restated in a later report (merger
  under common control, retrospective policy change) with no correction
  announcement, e.g. 北方国际 000065 Q1 2016: first published revenue 1.02bn,
  Eastmoney shows 1.92bn (restated in the Q1 2017 report) under 2016-04-28.

BaoStock keeps the first-published figures. Every listed stock-period is
therefore compared with BaoStock, fully (three queries) or with a one-query
screen:

| status | rows | meaning |
|---|---|---|
| `verified` | 1 | fully checked: BaoStock (first published) and Eastmoney (latest) agree within 0.1% on 归母净利润, 营业总收入 and 经营现金流: one version, dated at first publication |
| `first_from_baostock` | v1 | fully checked and they differ: v1 = BaoStock's first-published figures, dated at first publication |
| `revised` | v2 | Eastmoney's revised figures for the same period, dated at the **latest** evidence of the revision (`revision_basis`: cninfo correction/restatement announcement, the next report that carried the restated figures, Eastmoney's update date, or, with no such evidence, the day the data were downloaded) |
| `screened` | 1 | not fully checked, but the first-published ratio 归母净利润 / 营业总收入 (BaoStock dupont) equals Eastmoney's: Eastmoney figures at first publication. The screen misses cash-flow-only restatements and the rare one that scales profit and revenue alike (rate measured on the random sample) |
| `revised_only` | v2 only | the screen differs but no full check exists: first-published figures unknown, only the revised version is kept, dated as `revised` |
| `unverified` | 1 | no BaoStock record (e.g. periods published before listing, Beijing exchange): Eastmoney figures at first publication. If a cninfo correction or restatement announcement exists for the period, `strict_date` moves to it |

Fully checked = a random sample (500), every stock-period linked to a cninfo
correction, revised report or restatement announcement, and every stock-period
the screen flagged. Counts are in the verification report.

## Tables (parquet)

### `reports_pit.parquet` — periodic reports, versioned (use this)

One row per (symbol, period_end, version). Figures are year-to-date cumulative
as published (Q2 = H1 cumulative, etc.), CNY.

| column | meaning |
|---|---|
| symbol | `600519.SH`, `000001.SZ`, `920002.BJ` (BJ = Beijing exchange, Eastmoney codes; no BaoStock check, no price data in research_daily). Eastmoney's placeholder codes for IPO applicants (`A22001.SZ` etc.) are dropped |
| period_end, period_type | report period end; Q1 / H1 / Q3 / FY |
| version | 1 = first published; 2 = revised |
| available_date | public from this date (exchange disclosure date; the later of Eastmoney and BaoStock when both exist; for version 2 the revision date). NaT = never usable |
| strict_date | ≥ available_date; certainly-public date for unchecked rows with a known correction or restatement announcement |
| parent_netprofit | 归属于母公司股东的净利润 (Eastmoney PARENTNETPROFIT; BaoStock netProfit × dupontPnitoni) |
| deducted_netprofit | 扣除非经常性损益后的归母净利润 (Eastmoney KCFJCXSYJLR; NaN in a BaoStock v1 row when 归母 changed; never checked, BaoStock has no 扣非) |
| revenue_total | 营业总收入 (Eastmoney TOTALOPERATEREVE; BaoStock netProfit / dupontNitogr) |
| revenue_op | 营业收入 (Eastmoney OPERATE_INCOME_PK; differs from revenue_total for finance subsidiaries, banks, insurers) |
| cfo | 经营活动产生的现金流量净额 (Eastmoney NETCASH_OPERATE_PK; BaoStock CFOToNP × netProfit) |
| status, value_source | see above; `baostock` or `eastmoney` |
| differs | fields where BaoStock and Eastmoney disagree (> 0.1%); `ratio` for `revised_only` |
| screen | one-query screen verdict (`agree` / `differ` / `na`) for rows that were not fully checked |
| notice_date_em, notice_date_bs | first-publication dates from each source |
| dmsk_notice_date | date of the latest report that carried the period's figures (Eastmoney DMSK tables; listed stocks only) |
| update_date_em | Eastmoney's last update of the row |
| revision_date, revision_basis | latest evidence that the period's figures were revised after first publication, and its kind (`event`, `later_report`, `em_update`, `observed`) |
| n_revision_events, revision_kinds | cninfo events linked to the period after its first publication |
| org_type, currency, name | Eastmoney organisation type (通用/银行/证券/保险), currency, short name |
| ipo_date, out_date, pre_listing | BaoStock listing/delisting dates; `pre_listing` = period ends before listing (prospectus figures) |

BaoStock-derived figures come from six-decimal ratios: the rounding bound is
about 5e-7 × net profit (negligible), larger only for revenue when the net
margin is tiny.

### `reports_latest.parquet` — Eastmoney as served (for reference only)

Latest figures + first `notice_date` + `update_date`, the DMSK latest-report
date and figures (`dmsk_*`), the 业绩报表 table's notice date, EPS and
operating cash flow per share (`cpd_*`). **Contains look-ahead** if joined on
`notice_date`; kept to audit the PIT table.

### `previews.parquet` — 业绩预告 and 业绩快报 (separate from periodic reports)

Long format: one row per (source, symbol, period_end, notice_date, item).

| source | coverage | notes |
|---|---|---|
| `bs_forecast` | all forecasts **including revisions**, listed and delisted SH/SZ | `chg_pct_low/high` in % (as published); `value_low/high` parsed from the abstract text when it states a level (NaN when it states only a change or %); item `parent_netprofit` / `deducted_netprofit` / `net_profit` from the text, `pct_only` when no level |
| `em_forecast` | listed stocks, **latest forecast only** (Eastmoney drops earlier versions) | structured amounts per item by PREDICT_FINANCE_CODE: `parent_netprofit` (004), `deducted_netprofit` (005), `eps` (003), `revenue` (006 营业收入), `revenue_deducted` (008 扣除后营业收入), `revenue_main` (001 主营业务收入), `net_profit` (002), `non_recurring` (007), `eps_deducted` (009); `prior_value` = last year's figure as stated in the forecast |
| `em_express` | listed stocks | 归母净利润, 营业总收入 with prior-year figures, EPS, ROE |
| `bs_express` | listed and delisted | yoy as fractions (`*_yoy_frac`, -0.36 = -36%), EPS, ROE in %, total/net assets; no profit amount |

Use `notice_date` as the available date for every preview row.

### Other files

| file | content |
|---|---|
| `events_cninfo.parquet` | cninfo announcements: revised reports (更正后/更新后/修订版), correction notices, unspecified corrections, restatements (差错更正/追溯调整), with Shanghai time |
| `event_links.parquet` | events mapped to (symbol, period_end): explicit periods from the title; a restatement links to periods first published in the 3 years before it; a correction without a period to periods published in the 45 days before it |
| `baostock_checks.parquet` | BaoStock first-published figures (derived) for every fully checked stock-period, with rounding bounds and `why` (sample / candidate / screen) |
| `universe.parquet` | BaoStock SH/SZ stock list incl. delisted (ipo_date, out_date) |
| `coverage_by_period.csv`, `missing_reports.csv` | expected vs present SH/SZ reports per period |
| `build_report.json` | all verification numbers |
| `raw/` | cached raw responses (gzip JSON / JSONL; BaoStock `checks.jsonl` full checks, `screen.jsonl` screen, `previews.jsonl`); `raw/em/<table>/history/` keeps older snapshots when a period is refetched |
| `work/` | targets (`targets.csv` sample + candidates, `targets_flagged.csv` screen), sample checks, frozen copy of the build code used for the run (`code/`; `code_run1/` = first, interrupted run) |
| `status.json`, `logs/` | progress of the last run |

## Sources

- Eastmoney datacenter (`datacenter-web.eastmoney.com/api/data/v1/get`):
  `RPT_F10_FINANCE_MAINFINADATA` (main; includes delisted stocks),
  `RPT_LICO_FN_CPD` (业绩报表; listed only), `RPT_DMSK_FN_INCOME` /
  `RPT_DMSK_FN_CASHFLOW` (latest-report dates; listed only),
  `RPT_PUBLIC_OP_NEWPREDICT` (forecasts; latest only), `RPT_FCI_PERFORMANCEE`
  (express). Filtered to SECURITY_TYPE_CODE 058001001/058001008 (A share, CDR)
  and 6-digit codes.
- cninfo (`www.cninfo.com.cn/new/hisAnnouncement/query`): titles and times of
  periodic reports with 更正/更新/修订/修正/补充, the 补充更正 category, and
  差错更正/追溯调整 searches, 2015 to date; per-stock searches for the sample's
  first-publication dates.
- BaoStock: `query_stock_basic`, `query_profit_data`, `query_dupont_data`,
  `query_cash_flow_data`, `query_forecast_report`,
  `query_performance_express_report`.
- Not used: akshare `stock_yjbb_em` / `stock_lrb_em` / `stock_xjll_em` (their
  公告日期 is the latest update, not the first publication); QMT (client being
  reinstalled).

## Updating

`python -I research/tools/fundamentals_fetch.py all --refresh-recent 4`
refetches the last four periods (older snapshots are kept under
`raw/em/<table>/history/`, so revisions Eastmoney makes from now on are
recorded with the date they were observed), re-runs the cninfo and BaoStock
stages for anything new (new stock-periods are screened, flagged ones fully
checked), and rebuilds the tables. Run it detached at BelowNormal priority
(WMI `Win32_Process.Create`).
