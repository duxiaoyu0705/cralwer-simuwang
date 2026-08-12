# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

### 尽量使用中文进行解释和沟通

## 项目概述

从 [私募排排网](https://dc.simuwang.com) 自动抓取中证500指数增强策略基金的收益走势图数据，输出为 Excel 文件。核心挑战：网站 API 返回的数据经过双层加密（动态密钥混淆 + AES-256-CBC），需要在浏览器 JS 环境中完成解密。

## 命令

```bash
# 激活虚拟环境（Python 3.10）
source .venv/bin/activate

# 安装依赖
pip install playwright pandas openpyxl
playwright install chromium

# 运行抓取脚本
python scrape_funds.py
```

## 架构

`scrape_funds.py` 是唯一的代码文件，约 440 行。整体流程：

1. **login(page)** — 自动填写手机号/密码，"密码登录"Tab，勾选协议 → 5s 等待 → 处理弹窗 → 安全验证等人工
2. **get_fund_list(page)** — 翻页提取 `table tbody tr a`，过滤 company/manager 链接 → 返回 `[{name, href}]`
3. **extract(page, fund)** — 详情页拦截 `fundNavTrend` API 响应，浏览器内执行 `eval(混淆seed) → AES-256-CBC解密 → JSON.parse` 获得 `{categories, data: {fundId: {ret}, benchmark, compare}}`
4. 数据计算：累计% → 乘数 `1+v/100` → 区间收益率% → 超额收益(算术) = 基金区间% - 基准区间%
5. **save_result()** — 增量追加到 Excel，按基金名称去重

失败重试最多 3 次（间隔 180s），基金间间隔 27s，支持断点续传。

## 关键细节

- **控件重复**: 页面有两组相同控件（可见 + `display:none`），遍历时需用 `getComputedStyle` 检查父元素链可见性
- **超额收益下拉**: 默认平滑曲线复利，脚本切为 `[aria-haspopup]:has-text('超额收益')` → 选"超额收益(算术)"
- **CryptoJS 来源**: 页面 Nuxt3 ES 模块中的 CryptoJS 非全局，需遍历 `window` 属性找到阿里云验证码暴露的副本
- **`ret` 语义**: 是累计收益率百分比（如 370.6），不是净值乘数，公式 `1 + v/100`
- **API 只调一次**: `fundNavTrend` 在页面加载时调用，"近半年"控件不触发新请求，过滤在本地完成

详细设计决策见 `design-doc.md`，开发踩坑见 `dev-doc.md`。
