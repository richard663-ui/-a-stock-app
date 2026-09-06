from services import v4r_meta_label_audit_v2 as m

assert m.META_TRAIN_DAYS == 2
assert m.VERSION.startswith("v4r-meta-label-rolling-oof-v2")
assert m.CLOUD_SCOPE == "QMT_L1_60S_V4R_META_LABEL_V2"
assert m.m1.META_THRESHOLDS == (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
# Promotion gate must reject a numerically attractive but under-covered candidate.
b = {"directional_accuracy_pct": 46.8, "avg_net_edge_bp": -1.15}
c = {"directional_predictions": 99, "directional_accuracy_pct": 60.0,
     "directional_coverage_pct": 4.9, "avg_net_edge_bp": 2.0,
     "positive_net_edge_days": 4, "accuracy_wilson_95_lower_pct": 53.0}
g = m.m1._gate(c, b)
assert g["pass"] is False
assert g["checks"]["directional_predictions_ge_100"] is False
assert g["checks"]["coverage_ge_5pct"] is False
print("rolling OOF meta-label V2 self-test PASS")
