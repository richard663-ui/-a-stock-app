# V17 云端部署步骤

## 1. 创建专用 Supabase 项目

在 Supabase 新建一个只用于本项目的空项目，然后打开 SQL Editor，执行仓库根目录的 `supabase_schema.sql`。

从项目设置里复制：

- Project URL
- service_role key

不要把 service_role key 写进 GitHub。

## 2. 配置 Streamlit Community Cloud Secrets

在 Streamlit 应用设置中打开 **Secrets**，填入：

```toml
APP_PASSWORD = "YOUR_PRIVATE_PASSWORD"
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_KEY = "YOUR_SERVICE_ROLE_KEY"
BRIDGE_ID = "family-qmt-01"
```

应用分支选择 `qmt-v17-experiment`，入口文件使用 `app_1.py`。

## 3. 配置 ROG 本地桥梁

把 `.streamlit/secrets.toml.example` 复制为：

```text
.streamlit/secrets.toml
```

填入与 Streamlit Cloud 完全相同的 Supabase URL、service role key 和 BRIDGE_ID。APP_PASSWORD 只用于本地页面，可与云端一致。

首次测试双击：

```text
start_cloud_bridge.bat
```

确认能持续看到股票代码、价格和 samples 后，双击：

```text
install_cloud_bridge_startup.bat
```

它会把桥梁安装为 Windows 登录后自动启动任务。桥梁启动早于 QMT 时会自动重试；国盛 QMT 仍需保持登录。

## 工作流程

```text
爸爸手机 Streamlit 搜索股票
        ↓
Supabase 写入当前股票请求
        ↓
ROG 本地桥梁读取请求并让 QMT 自动切股
        ↓
本地桥梁回填当日分笔并持续上传
        ↓
Streamlit 加载完整样本后显示四大板块
```

## 安全说明

- 访问密码和 Supabase service role key 只放在 Streamlit Secrets / 本地 secrets.toml。
- `.gitignore` 已排除真实 secrets.toml 和 runtime 行情文件。
- 当前代码只读取行情，不导入 xttrader，不读取账户，不下单。
- 建议把 GitHub 仓库改为 private；页面密码只能限制 Streamlit 访问，不能阻止别人阅读 public GitHub 源码。
