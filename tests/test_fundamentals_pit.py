import importlib.util
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fundamentals_pit", ROOT / "research" / "tools" / "fundamentals_pit.py")
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)

T = pd.Timestamp


def em_row(code="600518.SH", period="2017-12-31", notice="2018-04-26 00:00:00", update="2019-04-30 00:00:00",
           parent=2149836281.41, deducted=1343641222.62, revenue=17578618640.06, cfo=-4840052606.46, sec_type="058001001"):
    return {"SECUCODE": code, "SECURITY_NAME_ABBR": "x", "SECURITY_TYPE_CODE": sec_type, "REPORT_DATE": period + " 00:00:00",
            "REPORT_TYPE": "年报", "ORG_TYPE": "通用", "CURRENCY": "CNY", "NOTICE_DATE": notice, "UPDATE_DATE": update,
            "PARENTNETPROFIT": parent, "KCFJCXSYJLR": deducted, "TOTALOPERATEREVE": revenue, "OPERATE_INCOME_PK": revenue,
            "NETCASH_OPERATE_PK": cfo}


def pit_frame(rows):
    df = pd.DataFrame(rows)
    for c in ("period_end", "available_date", "strict_date"):
        if c in df:
            df[c] = pd.to_datetime(df[c])
    for f in fp.FIELDS:
        if f not in df:
            df[f] = np.nan
    return df


class PeriodAndCodeTests(unittest.TestCase):
    def test_periods_and_deadlines(self):
        ps = fp.report_periods("2014-03-31", "2015-01-01")
        self.assertEqual([p.strftime("%m-%d") for p in ps], ["03-31", "06-30", "09-30", "12-31"])
        self.assertEqual(fp.report_periods("2026-03-31", "2026-11-23")[-1], T("2026-09-30"))
        self.assertEqual(fp.filing_deadline("2017-12-31"), T("2018-04-30"))
        self.assertEqual(fp.filing_deadline("2018-06-30"), T("2018-08-31"))
        self.assertEqual(fp.period_type("2018-09-30"), "Q3")

    def test_symbols(self):
        self.assertEqual(fp.symbol_from_code("600519"), "600519.SH")
        self.assertEqual(fp.symbol_from_code("300104"), "300104.SZ")
        self.assertEqual(fp.symbol_from_code("920002"), "920002.BJ")
        self.assertIsNone(fp.symbol_from_code("900901"))  # B share
        self.assertEqual(fp.bs_code("000001.SZ"), "sz.000001")
        self.assertIsNone(fp.bs_code("920002.BJ"))
        self.assertEqual(fp.symbol_from_bs("sh.600519"), "600519.SH")


class EastmoneyParsingTests(unittest.TestCase):
    def test_main_keeps_a_shares_and_maps_fields(self):
        rows = [em_row(), em_row(code="830799.BJ", sec_type="058001005"), em_row(code="900901.SH", sec_type="058001002"),
                em_row(code="A22001.SZ")]  # IPO applicant placeholder code, same security type as listed shares
        df = fp.parse_em_main(rows)
        self.assertEqual(list(df["symbol"]), ["600518.SH"])
        r = df.iloc[0]
        self.assertEqual(r["notice_date"], T("2018-04-26"))
        self.assertEqual(r["period_type"], "FY")
        self.assertAlmostEqual(r["parent_netprofit"], 2149836281.41)
        self.assertAlmostEqual(r["cfo"], -4840052606.46)

    def test_missing_values_stay_missing(self):
        df = fp.parse_em_main([em_row(cfo=None)])
        self.assertTrue(np.isnan(df["cfo"].iloc[0]))

    def test_forecast_items(self):
        self.assertEqual(fp.forecast_item("归属于上市公司股东的净利润"), "parent_netprofit")
        self.assertEqual(fp.forecast_item("扣除非经常性损益后的净利润"), "deducted_netprofit")
        self.assertEqual(fp.forecast_item("营业收入"), "revenue")
        # each name maps to its own item, so one forecast never yields two rows with the same item
        names = ["主营业务收入", "净利润", "每股收益", "归属于上市公司股东的净利润", "扣除非经常性损益后的净利润",
                 "营业收入", "非经常性损益", "扣除后营业收入", "扣非后每股收益"]
        by_name = [fp.forecast_item(n) for n in names]
        self.assertEqual(len(set(by_name)), len(names))
        self.assertEqual(by_name, [fp.FORECAST_CODES[f"{i:03d}"] for i in range(1, 10)])
        self.assertEqual(fp.forecast_item("whatever", "008"), "revenue_deducted")  # the code wins

    def test_forecast_rows_keep_items_apart(self):
        base = {"SECUCODE": "000004.SZ", "SECURITY_TYPE_CODE": "058001001", "REPORT_DATE": "2021-12-31 00:00:00",
                "NOTICE_DATE": "2022-04-28 00:00:00", "PREDICT_AMT_LOWER": 2e8, "PREDICT_AMT_UPPER": 3e8}
        rows = [{**base, "PREDICT_FINANCE_CODE": "006", "PREDICT_FINANCE": "营业收入"},
                {**base, "PREDICT_FINANCE_CODE": "008", "PREDICT_FINANCE": "扣除后营业收入"},
                {**base, "SECUCODE": "A21001.SZ", "PREDICT_FINANCE_CODE": "004", "PREDICT_FINANCE": "归属于上市公司股东的净利润"}]
        df = fp.parse_em_forecast(rows)
        self.assertEqual(sorted(df["item"]), ["revenue", "revenue_deducted"])
        self.assertFalse(df.duplicated(["source", "symbol", "period_end", "notice_date", "item"]).any())


class ForecastTextTests(unittest.TestCase):
    def test_loss_range_in_wan(self):
        low, high, item = fp.parse_forecast_amounts("预计2019年1-12月归属于上市公司股东的净利润亏损:135,000万元至165,000万元。")
        self.assertEqual((low, high, item), (-1.65e9, -1.35e9, "parent_netprofit"))

    def test_single_amount_and_change_clause_ignored(self):
        low, high, _ = fp.parse_forecast_amounts("预计2019年1-12月归属于上市公司股东的净利润亏损:约461500万元。")
        self.assertEqual((low, high), (-4.615e9, -4.615e9))
        low, high, _ = fp.parse_forecast_amounts(
            "预计2020年1-12月归属于上市公司股东的净利润亏损:1,485,000万元至1,782,000万元，同比上年降低1,018,932.5万元至1,315,932.5万元。")
        self.assertEqual((low, high), (-1.782e10, -1.485e10))

    def test_profit_unit_once_and_change_only(self):
        low, high, _ = fp.parse_forecast_amounts("预计2024年1-6月归属于上市公司股东的净利润盈利:1,400至2,100万元。")
        self.assertEqual((low, high), (1.4e7, 2.1e7))
        low, high, item = fp.parse_forecast_amounts("预计2019年度归属于上市公司股东的净利润与上年同期相比增加5,000万元到6,000万元")
        self.assertTrue(np.isnan(low) and np.isnan(high))
        self.assertIsNone(fp.parse_forecast_amounts("预计净利润同比增长50%-80%")[2])


class CninfoTitleTests(unittest.TestCase):
    def test_periods_in_titles(self):
        self.assertEqual(fp.report_periods_in_title("2018年年度报告（<em>更新</em>后）"), [(T("2018-12-31"), "FY")])
        self.assertEqual(fp.report_periods_in_title("2019年半年度报告摘要"), [(T("2019-06-30"), "H1")])
        self.assertEqual(fp.report_periods_in_title("关于2018年年度报告及2019年第一季度报告的更正公告"),
                         [(T("2018-12-31"), "FY"), (T("2019-03-31"), "Q1")])
        self.assertEqual(fp.report_periods_in_title("关于对2019年第一季度报告中2019年半年度业绩预测的补充说明"),
                         [(T("2019-03-31"), "Q1")])
        self.assertEqual(fp.report_periods_in_title("关于2018年度股东大会通知的更正公告"), [])
        self.assertEqual(fp.report_periods_in_title("2018年度报告"), [(T("2018-12-31"), "FY")])

    def test_classification(self):
        self.assertEqual(fp.classify_announcement("2018年年度报告（更新后）", "revised"), "revised_report")
        self.assertEqual(fp.classify_announcement("2018年年度报告摘要（已取消）", "revised"), "cancelled")
        self.assertEqual(fp.classify_announcement("关于2018年年度报告的更正公告", "corrections"), "correction_notice")
        self.assertEqual(fp.classify_announcement("补充更正公告", "corrections"), "correction_unspecified")
        self.assertEqual(fp.classify_announcement("关于2018年度股东大会通知的更正公告", "corrections"), "other")
        self.assertEqual(fp.classify_announcement("关于前期会计差错更正的公告", "restatement"), "restatement")

    def test_parse_cninfo_times_are_shanghai(self):
        anns = [{"announcementId": "1", "secCode": "600518", "announcementTitle": "2017年年度报告",
                 "announcementTime": 1524672000000, "adjunctUrl": "x.PDF"},
                {"announcementId": "2", "secCode": "200001", "announcementTitle": "2017年年度报告",
                 "announcementTime": 1524672000000, "adjunctUrl": "y.PDF"}]
        ev = fp.parse_cninfo(anns, "revised")
        self.assertEqual(len(ev), 1)  # B share dropped
        self.assertEqual(ev["ann_date"].iloc[0], T("2018-04-26"))
        self.assertEqual(ev["periods"].iloc[0], "2017-12-31")


class BaoStockTests(unittest.TestCase):
    def test_derived_first_published_values(self):
        d = fp.bs_derive({"pubDate": "2018-04-26", "netProfit": "4094646237.18", "npMargin": "0.154649"},
                         {"dupontPnitoni": "1.001500", "dupontNitogr": "0.154649"}, {"CFOToNP": "0.450050"})
        self.assertEqual(d["bs_pub_date"], T("2018-04-26"))
        self.assertAlmostEqual(d["parent_netprofit"], 4094646237.18 * 1.0015, delta=1)
        self.assertAlmostEqual(d["revenue_total"] / 1e8, 264.77, delta=0.01)
        self.assertAlmostEqual(d["cfo"] / 1e8, 18.43, delta=0.01)
        self.assertAlmostEqual(d["parent_netprofit_err"], 4094646237.18 * 5e-7)

    def test_missing_ratios_give_nan_not_zero(self):
        d = fp.bs_derive({"netProfit": "100"}, {}, {})
        self.assertTrue(np.isnan(d["parent_netprofit"]) and np.isnan(d["revenue_total"]) and np.isnan(d["cfo"]))

    def test_screen_ratio(self):
        ratio, err = fp.bs_ratio({"dupontPnitoni": "1.001500", "dupontNitogr": "0.154649"})
        self.assertAlmostEqual(ratio, 1.0015 * 0.154649)
        # Kangmei FY2017 as first published (41.0 / 264.8) agrees; the corrected figures (21.5 / 175.8) do not
        v = fp.screen_verdict([4.1e9, 2.15e9, 1.0, 5.0], [26.48e9, 17.58e9, 0.0, np.nan], [ratio] * 4, [err] * 4)
        self.assertEqual(list(v), ["agree", "differ", "na", "na"])
        self.assertTrue(np.isnan(fp.bs_ratio({})[0]))

    def test_agreement(self):
        out = fp.agreement([100e6, 100e6, np.nan, 100e6], [100.05e6, 102e6, 1.0, 100e6 * 1e4])
        self.assertEqual(list(out), ["agree", "differ", "na", "differ"])
        self.assertTrue(fp.scale_mismatch([1e9], [1e5])[0])
        self.assertFalse(fp.scale_mismatch([1e9], [1.1e9])[0])


class VersioningTests(unittest.TestCase):
    def setUp(self):
        self.em = fp.parse_em_main([
            em_row(),  # Kangmei FY2017: corrected values under the first notice date
            em_row(code="600519.SH", notice="2018-03-28 00:00:00", update="2019-03-29 00:00:00",
                   parent=27079360255.05, deducted=27223280155.52, revenue=61062757593.94, cfo=22153036665.9),
            em_row(code="000002.SZ", notice="2018-03-27 00:00:00", update="2018-03-27 00:00:00",
                   parent=1e9, deducted=9e8, revenue=1e10, cfo=5e8),
        ])
        self.dmsk = pd.DataFrame({"symbol": ["600518.SH"], "period_end": [T("2017-12-31")],
                                  "dmsk_parent_netprofit": [2149836281.41], "dmsk_revenue_total": [17578618640.06],
                                  "dmsk_deducted_netprofit": [1343641222.62], "dmsk_cfo": [-4840052606.46],
                                  "dmsk_notice_date": [T("2019-04-30")]})
        bs = []
        for sym, p in (("600518.SH", {"pubDate": "2018-04-26", "netProfit": "4094646237.18"}),
                       ("600519.SH", {"pubDate": "2018-03-28", "netProfit": "29006364182.45"})):
            if sym == "600518.SH":
                d = fp.bs_derive(p, {"dupontPnitoni": "1.001500", "dupontNitogr": "0.154649"}, {"CFOToNP": "0.450050"})
            else:
                d = fp.bs_derive(p, {"dupontPnitoni": f"{27079360255.05 / 29006364182.45:.6f}",
                                     "dupontNitogr": f"{29006364182.45 / 61062757593.94:.6f}"},
                                 {"CFOToNP": f"{22153036665.9 / 29006364182.45:.6f}"})
            bs.append({"symbol": sym, "period_end": T("2017-12-31"), **d})
        self.bs = pd.DataFrame(bs)
        self.links = pd.DataFrame({"symbol": ["600518.SH", "000002.SZ"], "period_end": [T("2017-12-31")] * 2,
                                   "event_date": [T("2019-04-30"), T("2018-05-10")], "kind": ["restatement", "correction_notice"],
                                   "ann_id": ["a", "b"]})
        self.pit = fp.build_pit(self.em, self.dmsk, self.bs, self.links)

    def rows(self, symbol):
        return self.pit[self.pit["symbol"] == symbol].reset_index(drop=True)

    def test_verified_single_version(self):
        r = self.rows("600519.SH")
        self.assertEqual(len(r), 1)
        self.assertEqual(r.loc[0, "status"], "verified")
        self.assertEqual(r.loc[0, "available_date"], T("2018-03-28"))
        self.assertAlmostEqual(r.loc[0, "parent_netprofit"], 27079360255.05)

    def test_restated_report_keeps_first_and_revised_versions(self):
        r = self.rows("600518.SH")
        self.assertEqual(list(r["version"]), [1, 2])
        v1, v2 = r.iloc[0], r.iloc[1]
        self.assertEqual(v1["status"], "first_from_baostock")
        self.assertEqual(v1["available_date"], T("2018-04-26"))
        self.assertAlmostEqual(v1["revenue_total"] / 1e8, 264.77, delta=0.01)  # what was published in 2018
        self.assertTrue(np.isnan(v1["deducted_netprofit"]))  # unknown first value, not guessed
        self.assertTrue(np.isnan(v1["revenue_op"]))
        self.assertEqual(v2["status"], "revised")
        self.assertEqual(v2["available_date"], T("2019-04-30"))
        self.assertEqual(v2["revision_basis"], "event")
        self.assertAlmostEqual(v2["revenue_total"], 17578618640.06)
        self.assertIn("parent_netprofit", v1["differs"])

    def test_unverified_with_correction_moves_strict_date(self):
        r = self.rows("000002.SZ")
        self.assertEqual(len(r), 1)
        self.assertEqual(r.loc[0, "status"], "unverified")
        self.assertEqual(r.loc[0, "available_date"], T("2018-03-27"))
        self.assertEqual(r.loc[0, "strict_date"], T("2018-05-10"))

    def test_undated_revision_waits_for_observation(self):
        em = self.em.copy()
        em.loc[em["symbol"] == "600518.SH", "update_date"] = T("2018-04-26")  # no later Eastmoney update
        dmsk = self.dmsk.assign(dmsk_notice_date=T("2018-04-26"))  # no later report
        links = self.links[self.links["symbol"] != "600518.SH"]  # no announcement
        out = fp.build_pit(em.assign(observed_date=T("2026-11-24")), dmsk, self.bs, links)
        v2 = out[(out["symbol"] == "600518.SH") & (out["version"] == 2)].iloc[0]
        self.assertEqual(v2["available_date"], T("2026-11-24"))
        self.assertEqual(v2["revision_basis"], "observed")
        out = fp.build_pit(em, dmsk, self.bs, links)
        v2 = out[(out["symbol"] == "600518.SH") & (out["version"] == 2)].iloc[0]
        self.assertTrue(pd.isna(v2["available_date"]))  # never joinable rather than dated too early

    def test_screen_statuses(self):
        em = pd.concat([self.em, fp.parse_em_main([em_row(code="000003.SZ", notice="2018-04-20 00:00:00", update="2019-04-25 00:00:00",
                                                          parent=5e8, deducted=4e8, revenue=5e9, cfo=1e8)])], ignore_index=True)
        screen = pd.DataFrame({"symbol": ["000002.SZ", "000003.SZ", "600519.SH"], "period_end": [T("2017-12-31")] * 3,
                               "screen": ["agree", "differ", "differ"],
                               "screen_pub_date": [T("2018-03-28"), T("2018-04-20"), T("2018-03-28")]})
        out = fp.build_pit(em, self.dmsk, self.bs, self.links, screen=screen)
        r = out[out["symbol"] == "000002.SZ"].reset_index(drop=True)
        self.assertEqual(list(r["status"]), ["screened"])
        self.assertEqual(r.loc[0, "available_date"], T("2018-03-28"))  # the later of the two first-publication dates
        self.assertEqual(r.loc[0, "strict_date"], T("2018-05-10"))  # a correction announcement still moves strict_date
        r = out[out["symbol"] == "000003.SZ"].reset_index(drop=True)
        self.assertEqual(list(r["version"]), [2])  # first-published figures unknown: no version 1
        self.assertEqual(r.loc[0, "status"], "revised_only")
        self.assertEqual(r.loc[0, "available_date"], T("2019-04-25"))
        self.assertEqual(r.loc[0, "revision_basis"], "em_update")
        self.assertEqual(list(out.loc[out["symbol"] == "600519.SH", "status"]), ["verified"])  # a full check wins

    def test_later_baostock_date_wins(self):
        bs = self.bs.copy()
        bs.loc[bs["symbol"] == "600519.SH", "bs_pub_date"] = T("2018-03-29")
        out = fp.build_pit(self.em, self.dmsk, bs, self.links)
        self.assertEqual(out.loc[out["symbol"] == "600519.SH", "available_date"].iloc[0], T("2018-03-29"))

    def test_asof_period_never_sees_the_future(self):
        q = pd.DataFrame({"symbol": ["600518.SH"] * 4, "period_end": [T("2017-12-31")] * 4,
                          "date": ["2018-04-25", "2018-04-26", "2019-04-29", "2019-04-30"]})
        out = fp.asof_period(self.pit, q)
        self.assertTrue(np.isnan(out.loc[0, "revenue_total"]))
        self.assertAlmostEqual(out.loc[1, "revenue_total"] / 1e8, 264.77, delta=0.01)
        self.assertAlmostEqual(out.loc[2, "revenue_total"] / 1e8, 264.77, delta=0.01)
        self.assertAlmostEqual(out.loc[3, "revenue_total"], 17578618640.06)
        self.assertEqual(list(out["version"].fillna(0).astype(int)), [0, 1, 1, 2])

    def test_strict_mode_uses_strict_date(self):
        q = pd.DataFrame({"symbol": ["000002.SZ"] * 2, "period_end": [T("2017-12-31")] * 2, "date": ["2018-04-01", "2018-05-10"]})
        out = fp.asof_period(self.pit, q, date_col="strict_date")
        self.assertTrue(np.isnan(out.loc[0, "parent_netprofit"]))
        self.assertEqual(out.loc[1, "parent_netprofit"], 1e9)


class AsofLatestTests(unittest.TestCase):
    def test_old_period_revision_does_not_replace_newer_period(self):
        pit = pit_frame([
            {"symbol": "A", "period_end": "2017-12-31", "version": 1, "available_date": "2018-04-20", "strict_date": "2018-04-20",
             "parent_netprofit": 10.0, "status": "first_from_baostock"},
            {"symbol": "A", "period_end": "2018-03-31", "version": 1, "available_date": "2018-04-28", "strict_date": "2018-04-28",
             "parent_netprofit": 3.0, "status": "verified"},
            {"symbol": "A", "period_end": "2017-12-31", "version": 2, "available_date": "2018-06-01", "strict_date": "2018-06-01",
             "parent_netprofit": 5.0, "status": "revised"},
            {"symbol": "B", "period_end": "2017-12-31", "version": 1, "available_date": "2018-03-01", "strict_date": "2018-03-01",
             "parent_netprofit": 7.0, "status": "verified"},
        ])
        q = pd.DataFrame({"symbol": ["A", "A", "A", "A", "B"],
                          "date": ["2018-04-19", "2018-04-21", "2018-04-30", "2018-06-02", "2018-03-02"]})
        out = fp.asof_latest(pit, q)
        self.assertTrue(pd.isna(out.loc[0, "period_end"]))
        self.assertEqual(out.loc[1, "parent_netprofit"], 10.0)
        self.assertEqual(out.loc[2, "period_end"], T("2018-03-31"))
        self.assertEqual(out.loc[3, "period_end"], T("2018-03-31"))  # FY2017 revision does not displace Q1 2018
        self.assertEqual(out.loc[3, "parent_netprofit"], 3.0)
        self.assertEqual(out.loc[4, "parent_netprofit"], 7.0)
        prior = fp.asof_period(pit, pd.DataFrame({"symbol": ["A"], "period_end": [T("2017-12-31")], "date": ["2018-06-02"]}))
        self.assertEqual(prior.loc[0, "parent_netprofit"], 5.0)


class LinkAndCoverageTests(unittest.TestCase):
    def test_link_events(self):
        events = pd.DataFrame({"ann_id": ["1", "2", "3"], "symbol": ["A.SH", "A.SH", "B.SZ"],
                               "ann_date": [T("2019-04-29"), T("2018-05-03"), T("2018-05-02")],
                               "kind": ["restatement", "correction_notice", "correction_unspecified"],
                               "periods": ["", "2017-12-31", ""]})
        reports = pd.DataFrame({"symbol": ["A.SH", "A.SH", "A.SH", "A.SH", "B.SZ", "B.SZ"],
                                "period_end": [T("2015-12-31"), T("2016-12-31"), T("2017-12-31"), T("2019-03-31"), T("2017-12-31"),
                                               T("2016-12-31")],
                                "notice_date": [T("2016-03-30"), T("2017-04-20"), T("2018-04-26"), T("2019-04-30"), T("2018-04-20"),
                                                T("2017-04-10")]})
        links = fp.link_events(events, reports)
        got = set(zip(links["symbol"], links["period_end"].dt.strftime("%Y-%m-%d"), links["kind"]))
        self.assertIn(("A.SH", "2017-12-31", "correction_notice"), got)
        self.assertIn(("A.SH", "2017-12-31", "restatement"), got)
        self.assertIn(("A.SH", "2016-12-31", "restatement"), got)  # within ~3 years before the event
        self.assertNotIn(("A.SH", "2015-12-31", "restatement"), got)
        self.assertNotIn(("A.SH", "2019-03-31", "restatement"), got)  # published after the event
        self.assertIn(("B.SZ", "2017-12-31", "correction_unspecified"), got)
        self.assertNotIn(("B.SZ", "2016-12-31", "correction_unspecified"), got)

    def test_coverage_respects_listing_and_delisting(self):
        uni = pd.DataFrame({"symbol": ["A", "B", "C"], "ipo_date": [T("2010-01-01"), T("2018-01-05"), T("2010-01-01")],
                            "out_date": [pd.NaT, pd.NaT, T("2018-03-01")]})
        reports = pd.DataFrame({"symbol": ["A"], "period_end": [T("2017-12-31")]})
        stats, missing = fp.coverage_by_period(uni, reports, [T("2017-12-31")])
        self.assertEqual(int(stats.loc[0, "expected"]), 1)  # B not yet listed, C delisted before the deadline
        self.assertEqual(int(stats.loc[0, "missing"]), 0)
        stats, missing = fp.coverage_by_period(uni, reports, [T("2018-03-31")])
        self.assertEqual(sorted(missing["symbol"]), ["A", "B"])

    def test_unit_flags(self):
        df = pd.DataFrame({"symbol": ["A", "B"], "period_end": [T("2017-12-31")] * 2, "currency": ["CNY", "USD"],
                           "parent_netprofit": [1.0, 6e12], "deducted_netprofit": [1.0, 1.0], "revenue_total": [-5.0, 1.0],
                           "revenue_op": [1.0, 1.0], "cfo": [1.0, 1.0]})
        flags = set(zip(fp.unit_flags(df)["symbol"], fp.unit_flags(df)["flag"]))
        self.assertEqual(flags, {("B", "non_cny"), ("B", "huge_parent_netprofit"), ("A", "negative_revenue_total")})


if __name__ == "__main__":
    unittest.main()
