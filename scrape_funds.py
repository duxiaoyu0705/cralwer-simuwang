"""
抓取 simuwang.com "收益走势图" 折线数据（实际百分比值）
方案: 拦截 fundNavTrend API + 浏览器内 AES 解码
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from urllib.parse import urljoin

from playwright.async_api import async_playwright
import pandas as pd


# ===== 多账户配置 =====
ACCOUNTS = [
    {"phone": "16602302952", "password": "du123456"},
    {"phone": "18242336092", "password": "Master2008*"},
    # {"phone": "13900139000", "password": "password3"},
]
ROTATE_EVERY = 30  # 每抓取 N 个基金后轮换到下一个账户

LIST_URL = "https://dc.simuwang.com/smph/a0ab1ac3"
FUND_COUNT = 0  # 0=全部，>0=指定数量
OUTPUT_FILE = "fund_chart_data_full_20260807.xlsx"


# ===== 弹窗处理 =====

async def dismiss_popups(page):
    for sel in [
        "button:has-text('同意并登录'):visible",
        "button:has-text('我已知悉并申请查看'):visible",
        "button:has-text('同意'):visible",
        "button:has-text('确定'):visible",
        "button:has-text('知道了'):visible",
        "text=我已阅读并同意",
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1000):
                await btn.click(timeout=2000)
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    return False


async def wait_for_security_verify(page):
    try:
        body = await page.inner_text("body")
        if "账户安全验证" in body:
            print("\n" + "=" * 50)
            print("  ⚠️  检测到「账户安全验证」弹窗")
            print("  请手动输入验证码，完成后脚本自动继续...")
            print("=" * 50)
            while True:
                await asyncio.sleep(3)
                try:
                    if "账户安全验证" not in await page.inner_text("body"):
                        print("[INFO] 验证完成，继续抓取...\n")
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


# ===== 登录 =====

async def login(page, account):
    phone = account["phone"]
    pwd = account["password"]
    print(f"[INFO] 登录: {phone} ...")
    await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    body = await page.inner_text("body")
    if any(kw in body for kw in ["登录/注册", "密码登录"]):
        try:
            await page.locator("button:has-text('密码登录'):visible").click(timeout=3000)
            await asyncio.sleep(0.5)
        except Exception:
            pass
        try:
            await page.locator("input[placeholder*='手机号']:visible").first.fill(phone, timeout=5000)
        except Exception:
            pass
        try:
            await page.locator("input[type='password']:visible").first.fill(pwd, timeout=5000)
        except Exception:
            pass
        try:
            await page.locator("text=我已阅读并同意").click(timeout=3000)
        except Exception:
            pass
        try:
            await page.locator("button:has-text('登录'):visible").last.click(timeout=5000)
        except Exception:
            await page.keyboard.press("Enter")
        await asyncio.sleep(5)
        await dismiss_popups(page)
        print(f"[INFO] {phone} 登录完成")
    else:
        print(f"[INFO] {phone} Cookie有效，无需重新登录")


# ===== 基金列表 =====

async def get_fund_list(page):
    if LIST_URL not in page.url:
        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    all_links = []
    seen = set()
    page_num = 1

    while True:
        links = await page.evaluate("""
            () => [...document.querySelectorAll('table tbody tr a')]
                .filter(a => a.href && a.innerText.trim()
                    && !a.href.includes('company')
                    && !a.href.includes('manager'))
                .map(a => ({name: a.innerText.trim(), href: a.href}))
        """)
        for l in links:
            if l["href"] not in seen:
                seen.add(l["href"])
                all_links.append(l)

        has_next = await page.evaluate("""
            () => { const b = document.querySelector('.btn-next');
                    return b && !b.classList.contains('disabled'); }
        """)
        if not has_next:
            break
        try:
            await page.locator(".btn-next").click(timeout=5000)
            await asyncio.sleep(2)
            page_num += 1
        except Exception:
            break

    total = len(all_links)
    all_links = all_links[:FUND_COUNT] if FUND_COUNT > 0 else all_links
    print(f"[INFO] {page_num}页共{total}只基金，取{len(all_links)}只")
    return all_links


# ===== 图表数据提取 =====

async def extract(page, fund):
    name, href = fund["name"], fund["href"]
    if href.startswith("/"):
        href = urljoin(LIST_URL, href)
    print(f"\n[{datetime.now():%H:%M:%S}] {name}")

    MAX_RETRIES = 3
    RETRY_DELAY = 180

    for attempt in range(1, MAX_RETRIES + 1):
        if attempt > 1:
            print(f"  [RETRY] 第{attempt}次重试...")

        api_body = None

        async def on_response(response):
            nonlocal api_body
            if "fundNavTrend" in response.url:
                try:
                    api_body = await response.json()
                except Exception:
                    pass

        page.on("response", on_response)

        try:
            await page.goto(href, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(6)

            for _ in range(5):
                if not await dismiss_popups(page):
                    break
                await asyncio.sleep(1)

            await wait_for_security_verify(page)
            await asyncio.sleep(2)

            # 点击近半年
            for item in await page.locator(".xp-nav-item.xs-nav-block-item:has-text('近半年')").all():
                if await item.is_visible():
                    hidden = await item.evaluate(
                        "el=>{let p=el.parentElement;while(p){if(getComputedStyle(p).display==='none')return 1;p=p.parentElement;}return 0;}"
                    )
                    if not hidden:
                        await item.click(force=True)
                        await asyncio.sleep(2)
                        break

            # 点击超额收益(算术)
            for t in await page.locator("[aria-haspopup='menu']:has-text('超额收益')").all():
                if await t.is_visible():
                    hidden = await t.evaluate(
                        "el=>{let p=el.parentElement;while(p){if(getComputedStyle(p).display==='none')return 1;p=p.parentElement;}return 0;}"
                    )
                    if not hidden:
                        await t.click(force=True)
                        await asyncio.sleep(2)
                        await page.evaluate(
                            "()=>document.querySelectorAll('.el-dropdown-menu__item').forEach(i=>{if(i.textContent.includes('超额收益(算术)'))i.click()})"
                        )
                        await asyncio.sleep(3)
                        break

            if api_body is None:
                page.remove_listener("response", on_response)
                if attempt < MAX_RETRIES:
                    print(f"  [WAIT] 未获取到数据，{RETRY_DELAY}s后重试...")
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                return {"fund_name": name, "series": [], "success": False,
                        "error": "No fundNavTrend after retries"}

            d = api_body.get("data", {})
            if not isinstance(d, dict) or not d.get("key"):
                page.remove_listener("response", on_response)
                if attempt < MAX_RETRIES:
                    print(f"  [WAIT] 数据不完整，{RETRY_DELAY}s后重试...")
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                return {"fund_name": name, "series": [], "success": False,
                        "error": "Incomplete API response"}

            # AES 解密
            series = []
            result = await page.evaluate("""
                (apiData) => {
                    const d = apiData.data;
                    if (!window[d.id]) eval(d.key);
                    const s = window[d.id], code = d.encode;
                    let key;
                    if (code === 3) key = s.split('').reverse().join('');
                    else if (code === 4) key = s.slice(2);
                    else if (code === 5) key = s.slice(0, s.length - 2);
                    else if (code === 6) key = s.slice(1, s.length - 1);
                    else if (code === 7) key = s.slice(2, s.length - 1);
                    else if (code === 8) key = s.slice(1, s.length - 2);
                    else if (code === 9) key = s[0] + s.slice(2);
                    else if (code === 10) key = s.slice(0, -2) + s[s.length - 1];
                    else key = s;
                    let CS = typeof CryptoJS !== 'undefined' ? CryptoJS : null;
                    if (!CS) {
                        for (let k of Object.getOwnPropertyNames(window)) {
                            try { let v = window[k]; if (v && v.MD5 && v.AES && v.enc) { CS = v; break; } } catch(e) {}
                        }
                    }
                    if (!CS) return {error: 'CryptoJS not found'};
                    const hex = CS.MD5(key).toString();
                    const dec = CS.AES.decrypt(window.atob(d.data),
                        CS.enc.Utf8.parse(hex),
                        {iv: CS.enc.Utf8.parse(hex.slice(16, 32)),
                         mode: CS.mode.CBC, padding: CS.pad.Pkcs7});
                    const text = dec.toString(CS.enc.Utf8);
                    if (!text) return {error: 'empty', sigBytes: dec.sigBytes};
                    return {success: true, data: JSON.parse(text)};
                }
            """, api_body)

            if result.get("success"):
                data = result["data"]
                categories = data.get("categories", [])
                fund_data = data.get("data", {})
                fund_id = next((k for k in fund_data if not k.startswith("IN") and k != "compare"), None)

                if fund_id and categories:
                    fund_ret = fund_data.get(fund_id, {}).get("ret", [])
                    benchmark = fund_data.get("IN0000007M", [])
                    if len(categories) > 1:
                        try:
                            last_date = str(categories[-1])
                            cutoff = (datetime.strptime(last_date, "%Y-%m-%d") - timedelta(days=185)).strftime("%Y-%m-%d")
                            start_idx = next((i for i, d2 in enumerate(categories) if str(d2) >= cutoff), 0)

                            cats = categories[start_idx:]
                            f_ret = fund_ret[start_idx:] if fund_ret else []
                            b_ret = benchmark[start_idx:] if benchmark else []

                            if f_ret:
                                f_mult = [1 + v/100 for v in f_ret]
                                b_mult = [1 + v/100 for v in b_ret] if b_ret else []
                                base_f = f_mult[0] if f_mult[0] != 0 else 1
                                base_b = b_mult[0] if b_mult and b_mult[0] != 0 else 1
                                fp = [(v/base_f - 1)*100 for v in f_mult]
                                bp = [(v/base_b - 1)*100 for v in b_mult] if b_mult else []
                                cp = [f - b for f, b in zip(fp, bp)] if bp else []
                                if fp:
                                    series.append({"name": "基金收益(%)", "coords": list(zip(cats, fp))})
                                if bp:
                                    series.append({"name": "基准收益(%)", "coords": list(zip(cats, bp))})
                                if cp:
                                    series.append({"name": "超额收益(算术)(%)", "coords": list(zip(cats, cp))})
                        except Exception as e:
                            print(f"  [CALC-ERR] {e}")

            ok = len(series) > 0
            if ok:
                print(f"  OK: {len(series)}条线, {len(series[0]['coords'])}点")
                for s in series:
                    ys = [y for _, y in s["coords"]]
                    print(f"    {s['name']}: {min(ys):.2f}% ~ {max(ys):.2f}%")
            else:
                print(f"  FAIL")
            page.remove_listener("response", on_response)
            return {"fund_name": name, "series": series, "success": ok}

        except Exception as e:
            print(f"  [ERROR] {e}")
            page.remove_listener("response", on_response)
            if attempt < MAX_RETRIES:
                print(f"  [WAIT] {RETRY_DELAY}s后重试...")
                await asyncio.sleep(RETRY_DELAY)
                continue
            return {"fund_name": name, "series": [], "success": False, "error": str(e)}

    return {"fund_name": name, "series": [], "success": False, "error": "max retries"}


# ===== Excel 保存 =====

def load_processed_funds(path):
    if not os.path.exists(path):
        return set()
    try:
        df = pd.read_excel(path)
        return set(df[df["数据线"] != "无"]["基金名称"].unique())
    except Exception:
        return set()


def save_result(result, path):
    rows = []
    for s in result.get("series", []):
        for i, (date, val) in enumerate(s["coords"]):
            rows.append({
                "基金名称": result["fund_name"],
                "数据线": s["name"],
                "序号": i,
                "日期": str(date),
                "数值(%)": round(val, 4),
            })
    if not result.get("series"):
        rows.append({"基金名称": result["fund_name"], "数据线": result.get("error", "无"), "序号": "", "日期": "", "数值(%)": ""})
    new_df = pd.DataFrame(rows)
    if os.path.exists(path):
        old = pd.read_excel(path)
        old = old[old["基金名称"] != result["fund_name"]]
        combined = pd.concat([old, new_df], ignore_index=True)
    else:
        combined = new_df
    combined.to_excel(path, index=False)


# ===== 主流程 =====

async def main():
    print("=" * 55)
    print(f"Simuwang 基金数据抓取 - {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"账户数: {len(ACCOUNTS)}, 每{ROTATE_EVERY}条轮换")
    print("=" * 55)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()

        try:
            # 首次登录
            account_idx = 0
            await login(page, ACCOUNTS[account_idx])

            funds = await get_fund_list(page)
            if not funds:
                return

            processed = load_processed_funds(OUTPUT_FILE)
            if processed:
                print(f"[INFO] 已有{len(processed)}只基金数据，将跳过")

            success_count = 0
            account_count = 0  # 当前账户已抓取数量

            for idx, fund in enumerate(funds):
                if fund["name"] in processed:
                    continue

                # 轮换账户
                if account_count >= ROTATE_EVERY and len(ACCOUNTS) > 1:
                    account_idx = (account_idx + 1) % len(ACCOUNTS)
                    print(f"\n[ROTATE] 切换到账户: {ACCOUNTS[account_idx]['phone']}")
                    await login(page, ACCOUNTS[account_idx])
                    account_count = 0
                    # 重新获取基金列表（新登录后可能需要重新进入列表页）
                    await get_fund_list(page)
                    await asyncio.sleep(2)

                result = await extract(page, fund)
                save_result(result, OUTPUT_FILE)

                if result.get("success"):
                    success_count += 1
                    account_count += 1
                    processed.add(fund["name"])

                if (len(processed)) % 10 == 0:
                    print(f"[PROGRESS] {len(processed)}/{len(funds)}")

                # 限速
                print(f"  [SLEEP] 60s...")
                await asyncio.sleep(60)

            print(f"\n[DONE] 本次成功{success_count}，总计{len(processed)} -> {OUTPUT_FILE}")

        finally:
            await browser.close()

    print(f"结束: {datetime.now():%Y-%m-%d %H:%M:%S}")


if __name__ == "__main__":
    asyncio.run(main())
