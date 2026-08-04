# V17 QMT实验版

目标：保留原四板块，增加60/120秒高置信方向预警，并减少页面冗余和网络请求。

## 明天开盘前

1. 打开并登录国盛QMT。
2. 在本地仓库根目录打开PowerShell。
3. 启动只读采集器：

```powershell
py .\services\qmt_experiment_collector.py --symbol 000400.SZ --interval 1
```

保持这个终端运行。

4. 另开一个PowerShell，启动实验页面：

```powershell
streamlit run app_v17.py
```

如果`streamlit`命令不可用：

```powershell
py -m streamlit run app_v17.py
```

## 页面结构

1. 今日动作与短线预警
2. 大资金与盘口
3. 趋势与指标
4. 事件与基本面背景

## 重要说明

- `预警强度`目前是规则引擎的信号强度，不是已经验证的真实胜率。
- 只有信号高度一致时才输出偏涨/偏跌，大多数时间保持中性。
- 不导入`xttrader`，不读取账户，不下单。
- 明天先采集真实数据和记录预警，之后再计算60秒/120秒命中率、覆盖率和误报率。
- Streamlit页面只负责展示；实时采集在独立进程中每秒运行，因此页面加载慢不会丢失中间数据。
