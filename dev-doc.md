# Simuwang 基金数据抓取 - 开发文档

## 1. 环境要求

| 依赖 | 版本 |
|------|------|
| Python | 3.10+ |
| Playwright | ≥1.40.0 |
| Pandas | ≥2.0.0 |
| OpenPyXL | ≥3.1.0 |
| Chromium | Playwright 内置 |

### 安装

```bash
pip install playwright pandas openpyxl
playwright install chromium
```

## 2. 项目结构

```
simuwang/
├── scrape_funds.py      # 主抓取脚本
├── fund_chart_data.xlsx  # 输出数据
├── requirements.txt      # Python 依赖
├── design-doc.md         # 设计文档
└── dev-doc.md            # 本文件
```

## 3. 配置参数

`scrape_funds.py` 顶部常量：

```python
ACCOUNTS = [
    {"phone": "13800138000", "password": "password1"},
    # 添加更多账户实现轮换
]
ROTATE_EVERY = 50  # 每个账户抓取N条后轮换
LIST_URL = "https://dc.simuwang.com/smph/a0ab1ac3"  # 列表页地址
FUND_COUNT = 0     # 0=全部，>0=指定数量
OUTPUT_FILE = "fund_chart_data.xlsx"                # 输出文件
```

### 速度参数

| 位置 | 延迟 | 说明 |
|------|------|------|
| `extract()` → 页面加载后 | 6s | 等待 API 响应和页面渲染 |
| `extract()` → 弹窗处理后 | 2s | 等待弹窗消失动画 |
| `extract()` → 控件点击后 | 2-3s | 等待图表重绘 |
| `main()` → 基金之间 | 60s | **限速，避免触发验证码** |
| `extract()` → 失败重试 | 180s | 等待网站恢复 |

## 4. 函数说明

### 4.1 login(page, account)

使用指定账户登录，支持多账户轮换。

参数:
- `page`: Playwright 页面对象
- `account`: `{"phone": "...", "password": "..."}` 字典

流程：
1. `page.goto(LIST_URL)` — 导航到列表页
2. 检测页面是否含"登录/注册"或"密码登录"文字
3. 若已登录（Cookie 有效），跳过
4. 点击"密码登录" Tab（默认是短信验证码 Tab）
5. 填写手机号和密码
6. 勾选 `text=我已阅读并同意`
7. 点击登录按钮
8. 等待 5 秒后调用 `dismiss_popups()`

**注意：** 所有点击使用 `force=True` 绕过固定遮罩层。

**注意：** 所有点击使用 `force=True` 绕过固定遮罩层。

### 4.2 get_fund_list(page)

翻页获取全部基金链接：

```python
while True:
    # 提取当前页 table tbody tr 中的 <a> 标签
    links = page.evaluate("document.querySelectorAll('table tbody tr a')")
    # 过滤 company/manager 链接
    # 检查 .btn-next 是否有 disabled 类
    if 没有下一页: break
    page.locator('.btn-next').click()
```

### 4.3 extract(page, fund)

单条基金数据提取，核心流程：

```
for attempt in 1..3:                    # 最多重试3次
  page.goto(href)                       # 导航到详情页
  dismiss_popups() × 5                  # 关闭弹窗
  wait_for_security_verify()            # 等待验证码

  # 拦截 API
  page.on("response", on_response)      # 监听 fundNavTrend

  # 控件操作
  点击 .xp-nav-item:has-text('近半年')     # 排除 hidden 父元素
  打开 [aria-haspopup]:has-text('超额收益') # 排除 hidden 父元素
  点击 .el-dropdown-menu__item:has-text('超额收益(算术)')

  if api_body is None → continue       # 重试
  if api_body.data.key 缺失 → continue  # 重试

  # 浏览器内解密
  result = page.evaluate("""
    eval(d.key)                         # Step 1: 获取 seed
    key = transform(seed, encode)       # Step 2: 密钥变换
    CS = 遍历window找CryptoJS           # Step 3: 找解密库
    hex = CS.MD5(key).toString()
    dec = CS.AES.decrypt(atob(data), ...) # Step 4: AES解密
    JSON.parse(dec.toString(Utf8))      # Step 5: 解析JSON
  """)

  # 数据计算
  categories → 过滤近半年(185天)
  ret → (1+v/100) 转乘数 → 区间收益率%
  benchmark → 同样处理
  超额收益(算术) = 基金区间% - 基准区间%

  return series
```

### 4.4 加密密钥变换

```python
if encode == 3:  key = seed[::-1]            # reverse
elif encode == 4:  key = seed[2:]            # 去掉前2位
elif encode == 5:  key = seed[:-2]           # 去掉后2位
elif encode == 6:  key = seed[1:-1]          # 去头去尾
elif encode == 7:  key = seed[2:-1]          # 去前2后1
elif encode == 8:  key = seed[1:-2]          # 去前1后2
elif encode == 9:  key = seed[0] + seed[2:]  # 保留首+去前2
elif encode == 10: key = seed[:-2] + seed[-1]# 去后2+加尾
```

### 4.5 dismiss_popups(page)

按优先级尝试关闭常见弹窗：

| 选择器 | 场景 |
|--------|------|
| `button:has-text('同意并登录')` | 登录后提示 |
| `button:has-text('我已知悉并申请查看')` | 净值查看申请 |
| `button:has-text('同意')` | 风险提示 |
| `button:has-text('确定')` | 通用确认 |
| `button:has-text('知道了')` | 公告 |
| `text=我已阅读并同意` | 协议勾选 |

### 4.6 wait_for_security_verify(page)

```python
if "账户安全验证" in page.inner_text("body"):
    print("请手动输入验证码...")
    while "账户安全验证" in body:
        sleep(3)
    print("验证完成，继续")
```

### 4.7 load_processed_funds(path) / save_result(result, path)

**加载：** 读取 Excel，提取"数据线"不为"无"的基金名称集合。

**保存：** 读取旧 Excel → 移除该基金的旧记录 → 追加新记录 → 写回。

## 5. 运行指南

### 5.1 启动

```bash
cd /Users/duxiaoyu/CodeStore/simuwang
python3.10 scrape_funds.py
```

### 5.2 运行特征

- 自动弹出 Chromium 浏览器窗口，**不要关闭或最小化**
- 终端实时打印当前处理的基金和结果
- 每条约 45 秒处理 + 60 秒间隔 = ~105 秒
- 配置多个账户时，每 50 条自动轮换重新登录

### 5.3 中断与恢复

- `Ctrl+C` 可随时中断
- 已处理数据已保存到 Excel
- 再次运行自动跳过已处理基金

### 5.4 验证码处理

看到以下提示时手动操作：
```
==================================================
  ⚠️  检测到「账户安全验证」弹窗
  请手动输入验证码，完成后脚本自动继续...
==================================================
```

## 6. 输出格式

`fund_chart_data.xlsx` 包含以下列：

| 列名 | 类型 | 说明 |
|------|------|------|
| 基金名称 | string | 基金产品名称 |
| 数据线 | string | `基金收益(%)` / `基准收益(%)` / `超额收益(算术)(%)` |
| 序号 | int | 0-27，28个周度数据点 |
| 日期 | string | `YYYY-MM-DD` 格式 |
| 数值(%) | float | 区间收益率百分比 |

示例：
```
基金名称           | 数据线           | 序号 | 日期       | 数值(%)
鹿秀长颈鹿1号      | 基金收益(%)      | 0    | 2026-01-09 | 0.0000
鹿秀长颈鹿1号      | 基金收益(%)      | 1    | 2026-01-16 | 2.8790
鹿秀长颈鹿1号      | 基准收益(%)      | 0    | 2026-01-09 | 0.0000
鹿秀长颈鹿1号      | 超额收益(算术)(%) | 1    | 2026-01-16 | 0.6910
```

## 7. 常见问题

### Q: 浏览器窗口关闭了怎么办？
重新运行脚本，已处理数据不会丢失。

### Q: 频繁出现验证码？
- 提高基金间延迟（`await asyncio.sleep(60)` 改为更大值）
- 添加更多账户到 `ACCOUNTS` 列表，利用轮换机制降低单账户频率
- 减小 `ROTATE_EVERY` 值（如改为 30），更频繁地切换账户

### Q: 部分基金无数据？
可能该基金页面未加载 `fundNavTrend` API（如经理页面、公司页面等非基金详情页）。脚本会标记"无"并继续。

### Q: API 加密方式变了怎么办？
加密逻辑在 `extract()` 的 `page.evaluate` 块中。如果后端修改了加密算法（如新增 `encode` 值），需要在密钥变换处追加对应的 `elif` 分支。

## 8. 踩坑记录

1. **`window.atob` vs `CryptoJS.enc.Base64.parse`** — 页面代码使用原生 `window.atob()` 而非 CryptoJS 的 Base64 解析，两者在 `AES.decrypt` 内部处理路径不同
2. **CryptoJS 模块作用域** — Nuxt 3 的 CryptoJS 在 ES 模块中 import，不是全局变量。复用阿里云验证码暴露的副本
3. **累计收益率 vs 净值乘数** — `ret` 是百分比值（如 370.6），需 `1 + v/100` 转乘数
4. **控件双重匹配** — 页面有两组相同的控件（可见 + `display:none`），需检查父元素可见性
5. **Canvas 离屏渲染** — 图表 Canvas 不在 DOM 中，`querySelectorAll` 找不到
6. **控件不触发 API** — `fundNavTrend` 只在页面加载时调用一次，"近半年"等控件纯客户端筛选
