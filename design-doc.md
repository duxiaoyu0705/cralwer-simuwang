# Simuwang 基金数据抓取 - 设计文档

## 1. 项目背景

### 1.1 需求
从 [私募排排网数据中心](https://dc.simuwang.com/smph/a0ab1ac3) 自动抓取中证500指数增强策略基金的"收益走势图"折线数据，包括：
- 基金收益率（近半年区间）
- 基准收益率（中证500）
- 超额收益（算术）

### 1.2 约束
- 网站需要登录认证
- 登录方式为密码登录（非短信验证码）
- 数据通过加密 API 返回，前端 JavaScript 解密后渲染
- 频繁访问会触发"账户安全验证"弹窗
- 图表控件（近半年、超额收益算术）需手动切换
- 单账户持续抓取容易触发频率限制，需要多账户轮换机制

### 1.3 目标输出
- Excel 文件，每条基金 3 条折线（各 28 个周度数据点）
- 实际百分比数值，非像素坐标
- 支持断点续传和增量保存

## 2. 方案选型与演进

### 2.1 方案A：Canvas 绑制拦截（已放弃）

**思路：** 通过 Playwright 注入 Canvas 2D 钩子，拦截 `moveTo`/`lineTo`/`bezierCurveTo`/`stroke` 等绑制操作，从绑制路径中提取折线坐标。

**优点：**
- 不依赖 API 解密，直接获取渲染结果
- 不受加密算法变更影响

**缺点与放弃原因：**
- 数据为像素坐标，无法转换为实际百分比值
- 图表 Canvas 为离屏渲染，不在 DOM 中，无法从 HTML 获取 Y 轴刻度标签
- Y 轴标签不通过 Canvas `fillText` 渲染（图表库使用其他方式）
- **结论：无法满足"实际数值"的核心需求**

### 2.2 方案B：API 拦截 + AES 解密（当前方案）

**思路：** 拦截 `fundNavTrend` API 响应，解析其自定义加密格式，在浏览器 JavaScript 环境中完成 AES 解密。

**核心挑战：** API 响应数据经过双层保护

#### 第一层：动态密钥
```json
{
    "data": {
        "encode": 8,
        "data": "<Base64 编码的 AES 密文>",
        "key": "var _0xXXXX=[...]; function _0xYYYY(d,e,f){...} eval(...)",
        "id": "p178419427115021232794"
    }
}
```

- `key` 字段是一段混淆的 JavaScript 代码，执行后设置 `window.pXXXXXXXX = seed_value`
- `encode` 字段决定 seed 的变换方式（3-10 共 8 种）
- `data` 字段是 Base64 编码的 AES-256-CBC 密文

#### 第二层：AES-256-CBC 加密

解密流程：
```
seed = eval(key)  →  window[pXXXX]  
key = transform(seed, encode)        // 字符串变换
md5_hex = MD5(key).toString()        // 32字符hex
aes_key = UTF8.encode(md5_hex)       // 32 bytes (AES-256)
aes_iv  = UTF8.encode(md5_hex[16:32]) // 16 bytes
plaintext = AES_CBC_Decrypt(atob(data), aes_key, aes_iv)
json_data = JSON.parse(plaintext)
```

### 2.3 解密方案对比

| 方案 | 结果 |
|------|------|
| Python + PyCryptodome | **失败** — `Padding is incorrect`，CryptoJS 内部字符串→WordArray 转换与 Python 字节处理不一致 |
| 注入 CryptoJS CDN | **失败** — 页面有同名 CryptoJS 但版本不同，冲突导致 `Malformed UTF-8` |
| 遍历 window 找到页面已有的 CryptoJS | **成功** — 复用阿里云验证码(AliyunCaptcha)暴露的 CryptoJS |

最终采用第三种：在浏览器 JS 环境中执行完整的解密链。

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                        scrape_funds.py                       │
├─────────────────────────────────────────────────────────────┤
│  main()                                                      │
│  ├── login()           ← 自动填写表单 + 勾选协议              │
│  ├── get_fund_list()   ← 翻页获取全部基金链接                 │
│  └── for each fund:                                          │
│       ├── extract()                                          │
│       │   ├── 导航到详情页 + 弹窗处理                          │
│       │   ├── 拦截 fundNavTrend API 响应                      │
│       │   ├── click_controls()  ← 近半年 + 超额收益(算术)      │
│       │   ├── 浏览器内 eval(key) + AES 解密                  │
│       │   ├── 数据计算：累计% → 区间% → 超额收益              │
│       │   └── 失败时自动重试（3次，间隔180s）                  │
│       ├── save_result()  ← 追加写入 Excel                    │
│       └── sleep(60)      ← 限速                              │
│                                                                │
│  辅助函数：                                                    │
│  ├── dismiss_popups()           ← 关闭常规弹窗                │
│  ├── wait_for_security_verify() ← 等待手动验证码              │
│  ├── load_processed_funds()     ← 读取已处理基金              │
│  └── save_result()              ← 增量保存到 Excel            │
└─────────────────────────────────────────────────────────────┘
```

## 4. 关键技术决策

### 4.1 为什么用 fundNavTrend 而非 performanceRangeV2

| API | 数据内容 | 是否使用 |
|-----|---------|---------|
| `fundNavTrend` | 全量历史净值数据（categories + ret + benchmark） | **使用** |
| `performanceRangeV2` | 区间绩效汇总 | 未使用（仅汇总值，无折线数据） |

`fundNavTrend` 返回从基金成立至今的完整时间序列数据，"近半年"筛选在前端完成。

### 4.2 "近半年"为何不触发新请求

控件点击只改变前端图表渲染参数，不会发起新的 API 请求。因此只需拦截页面加载时的一次 `fundNavTrend` 调用，然后在本地进行日期筛选。

### 4.3 累计收益率转区间收益率

**关键发现：** `ret` 字段存储的是累计收益率**百分比**（如 370.6 表示 +370.6%），不是净值乘数。

```python
# 错误：直接除百分比
fund_pct = (370.6 / 357.43 - 1) * 100 = 3.68%  ❌

# 正确：先转乘数再除
m1 = 1 + 370.6/100  # = 4.706
m2 = 1 + 357.43/100 # = 4.5743
fund_pct = (m1 / m2 - 1) * 100 = 2.88%  ✓
```

### 4.4 控件定位策略

页面存在两组相同的时间选择器和下拉框：
- 一组在可见的"收益走势图"区域
- 一组在 `display:none` 的隐藏区域中

解决方案：遍历所有匹配项，通过 `getComputedStyle` 检查父元素链确认可见性后再点击。

## 5. 容错与可靠性设计

### 5.1 断点续传
- 每条基金抓取后立即写入 Excel
- 启动时读取已有 Excel，跳过已处理的基金
- 重跑无需重新抓取已完成数据

### 5.2 失败重试
- API 无响应或数据不完整时，等待 180 秒后重试
- 最多重试 3 次

### 5.3 账户安全验证
- 检测页面是否出现"账户安全验证"弹窗
- 暂停抓取，提示用户手动输入验证码
- 每 3 秒轮询，验证通过后自动继续

### 5.4 限速
- 每条基金之间有 60 秒固定间隔
- 避免触发网站的反爬机制

### 5.5 多账户轮换
- 支持配置多个登录账户
- 每抓取 N 条基金后（`ROTATE_EVERY`，默认 50），自动切换下一个账户重新登录
- 降低单账户触发频率限制和验证码的风险
- 配置示例：
  ```python
  ACCOUNTS = [
      {"phone": "13800138000", "password": "password1"},
      {"phone": "13900139000", "password": "password2"},
  ]
  ROTATE_EVERY = 50
  ```

## 6. 数据流

```
列表页翻页
  ↓ 获取所有基金链接
基金详情页加载
  ↓ fundNavTrend API 响应
拦截加密数据
  ↓ { encode, data, key, id }
eval(key) → 设置 window[id] = seed
  ↓ 
transform(seed, encode) → key
  ↓
MD5(key) → hex (32 chars)
  ↓
UTF8(hex) → 32-byte AES key
  ↓
AES-CBC-Decrypt( atob(data), key, iv=UTF8(hex[16:32]) )
  ↓ 
JSON.parse → { categories, data: { fundId: {ret}, benchmark, compare } }
  ↓
过滤近半年（last_date - 185天）
  ↓
累计% → 区间% = (multiplier_now / multiplier_start - 1) × 100
  ↓
超额收益(算术) = 基金区间% - 基准区间%
  ↓
Excel: 基金名称 | 数据线 | 序号 | 日期 | 数值(%)
```
