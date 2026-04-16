import streamlit as st
import akshare as ak
import pandas as pd
from openai import OpenAI
import time
import urllib.request
import re

# ================= 【终极代理杀手】 =================
import os

urllib.request.getproxies = lambda: {}
os.environ['NO_PROXY'] = '*'
for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
    os.environ.pop(k, None)

# ================= 数据初始化 (加入状态记忆) =================
if 'stock_pool' not in st.session_state:
    st.session_state.stock_pool = {
        "002625": "光启技术", "300831": "派瑞股份", "688805": "健信超导",
        "688668": "鼎通科技", "002195": "岩山科技", "300870": "欧陆通",
        "688167": "炬光科技", "300373": "扬杰科技", "300408": "三环集团", "301566": "达利凯普"
    }

if 'ai_reports' not in st.session_state:
    st.session_state.ai_reports = {}


# ================= 核心功能函数 =================

def fetch_latest_news(stock_code):
    """抓取新闻"""
    try:
        news_df = ak.stock_news_em(symbol=stock_code)
        if news_df.empty: return []
        return news_df.head(5)[['发布时间', '新闻标题']].to_dict('records')
    except Exception as e:
        return [{"新闻标题": f"数据抓取失败: {e}", "发布时间": ""}]


def analyze_sentiment(news_list, stock_name, api_key, base_url):
    """调用 MiniMax 进行 AI 研判"""
    if not api_key: return "⚠️ 未检测到 API Key"
    if not news_list or "失败" in news_list[0].get("新闻标题", ""): return "暂无新闻数据。"

    news_text = "\n".join([f"时间: {item['发布时间']}, 标题: {item['新闻标题']}" for item in news_list])
    # 💡 优化了 Prompt，强制要求 AI 给出一个明确的纯数字得分
    prompt = f"你是一位A股资深分析师。目标股票：【{stock_name}】\n新闻：\n{news_text}\n请简要回答：1.核心事件 2.资金博弈(利空/利好) 3.情绪得分(必须直接给出一个1到10的纯数字，不要带其他描述说明)。"

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model="minimax-m2.7",
            messages=[
                {"role": "system", "content": "你是一个专业的金融量化分析助手，回答要求极简、直接。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 分析出错: {e}"


def parse_score(report_text):
    """从 AI 长文本中精准提取出情绪打分 (1-10)"""
    try:
        # 正则表达式寻找“得分”后面紧跟的数字
        match = re.search(r'情绪得分[^\d]*(\d+)', report_text)
        if match:
            score = int(match.group(1))
            return min(10, max(1, score))  # 确保在 1-10 范围内

        # 备用方案：寻找 "3." 后面的第一个数字
        backup_match = re.search(r'3\.[^\d]*(\d+)', report_text)
        if backup_match:
            return min(10, max(1, int(backup_match.group(1))))

        return 5  # 如果 AI 没按格式输出，默认给 5 分中性
    except:
        return 5


@st.cache_data(ttl=3600)  # 缓存历史数据1小时，加快速度
def get_5d_change(stock_code):
    """利用 Akshare 获取5日历史涨跌幅"""
    try:
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily")
        if len(df) >= 6:
            current_close = df['收盘'].iloc[-1]
            close_5d_ago = df['收盘'].iloc[-6]
            return round((current_close - close_5d_ago) / close_5d_ago * 100, 2)
        return 0.0
    except Exception:
        return 0.0


@st.cache_data(ttl=2)
def fetch_realtime_tencent(pool_codes):
    """腾讯独立接口实时抓取"""
    if not pool_codes: return pd.DataFrame()

    def get_prefix(c):
        if c.startswith(('60', '68')): return 'sh' + c
        if c.startswith(('00', '30')): return 'sz' + c
        if c.startswith(('4', '8')): return 'bj' + c
        return c

    query_str = ",".join([get_prefix(c) for c in pool_codes])
    url = f"http://qt.gtimg.cn/q={query_str}"

    try:
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request(url)
        resp = opener.open(req, timeout=5)
        text = resp.read().decode('gbk')

        results = []
        for line in text.strip().split('\n'):
            if not line: continue
            parts = line.split('=')[1].replace('"', '').split('~')
            if len(parts) > 38:
                results.append({
                    '代码': parts[2], '名称': parts[1], '最新价': float(parts[3]),
                    '今日涨跌': float(parts[32]), '换手率': float(parts[38]) if parts[38] else 0.0,
                    '成交额': f"{float(parts[37]) / 10000:.2f} 亿" if parts[37] else "0"
                })
        return pd.DataFrame(results)
    except Exception as e:
        return pd.DataFrame()


# ================= UI 界面 =================

def main():
    st.set_page_config(page_title="AI 量化交易监控中枢", page_icon="⚡", layout="wide")

    # -------- 侧边栏配置 --------
    st.sidebar.header("⚙️ 接口配置")
    user_api_key = st.sidebar.text_input("MiniMax API Key (令牌):", type="password")
    user_base_url = st.sidebar.text_input("Base URL:", value="https://api.minimax.chat/v1")

    st.sidebar.markdown("---")

    st.sidebar.header("➕ 动态添加自选股")
    new_code = st.sidebar.text_input("输入6位股票代码 (如 000001):", max_chars=6)
    new_name = st.sidebar.text_input("输入股票名称 (如 平安银行):")

    if st.sidebar.button("✅ 确认添加到核心池", use_container_width=True):
        if len(new_code) == 6 and new_name:
            st.session_state.stock_pool[new_code] = new_name
            st.sidebar.success(f"添加成功: {new_name}")
        else:
            st.sidebar.error("请输入正确的 6 位代码和名称！")

    st.sidebar.markdown(f"**当前监控池 ({len(st.session_state.stock_pool)}只)：**")
    for code, name in st.session_state.stock_pool.items():
        st.sidebar.caption(f"🔹 {code} - {name}")

    st.title("⚡ 核心科技股池 - AI 监控与实时盯盘")

    tab1, tab2 = st.tabs(["🤖 AI 情绪与研判 (每日复盘)", "📊 实时盘口与异动监控 (盘中实战)"])

    # ================= Tab 1 =================
    with tab1:
        st.markdown("⚠️ **操作提示：点击扫描后请耐心等待，系统正在提取分数并计算 5 日涨跌幅。**")

        col_btn, _ = st.columns([1, 4])
        with col_btn:
            do_scan = st.button("🚀 立即执行全盘 AI 扫描", type="primary", use_container_width=True)

        if do_scan:
            if not user_api_key:
                st.warning("⚠️ 请先在左侧输入 API Key！")
                st.stop()

            progress_bar = st.progress(0)
            status_text = st.empty()
            st.session_state.ai_reports.clear()

            pool_items = list(st.session_state.stock_pool.items())
            for idx, (code, name) in enumerate(pool_items):
                status_text.info(f"⏳ 正在分析与计算: {name}...")

                # 1. 抓取与分析
                news = fetch_latest_news(code)
                ai_report = analyze_sentiment(news, name, user_api_key, user_base_url)

                # 2. 从 AI 文本中提取分数
                score = parse_score(ai_report)

                # 3. 计算该股票 5日历史涨跌
                change_5d = get_5d_change(code)

                # 存入记忆库
                st.session_state.ai_reports[code] = {
                    "name": name, "news": news, "report": ai_report,
                    "score": score, "change_5d": change_5d
                }

                time.sleep(0.3)
                progress_bar.progress((idx + 1) / len(pool_items))

            status_text.success("✅ AI 研判扫描完毕！请查看下方战力排行榜。")

        # 🌟🌟🌟 新增：结构化数据战力排行榜 🌟🌟🌟
        if st.session_state.ai_reports:
            st.markdown("---")
            st.subheader("🏆 核心池战力总榜 (按 AI 情绪分排名)")

            summary_data = []
            # 获取最新价和今日涨跌
            target_codes = list(st.session_state.ai_reports.keys())
            realtime_df = fetch_realtime_tencent(target_codes)

            for code, data in st.session_state.ai_reports.items():
                curr_price = 0.0
                change_today = 0.0

                if not realtime_df.empty and code in realtime_df['代码'].values:
                    row = realtime_df[realtime_df['代码'] == code].iloc[0]
                    curr_price = row['最新价']
                    change_today = row['今日涨跌']

                summary_data.append({
                    "代码": code,
                    "名称": data['name'],
                    "最新价": curr_price,
                    "今日涨跌(%)": change_today,
                    "5日涨跌(%)": data['change_5d'],
                    "情绪得分": data['score']
                })

            # 转换为 DataFrame 并按分数降序排列
            df_summary = pd.DataFrame(summary_data)
            df_summary = df_summary.sort_values(by="情绪得分", ascending=False)

            # 使用 Streamlit 漂亮的进度条列来展示分数
            st.dataframe(
                df_summary,
                column_config={
                    "今日涨跌(%)": st.column_config.NumberColumn("今日涨跌(%)", format="%.2f %%"),
                    "5日涨跌(%)": st.column_config.NumberColumn("5日涨跌(%)", format="%.2f %%"),
                    "情绪得分": st.column_config.ProgressColumn(
                        "🔥 综合情绪得分",
                        help="由 MiniMax 提取新闻后给出的资金博弈打分",
                        min_value=1, max_value=10, format="%d 分"
                    ),
                },
                use_container_width=True, hide_index=True
            )

            st.markdown("---")
            st.subheader("📋 详细个股研判逻辑")
            col1, col2 = st.columns(2)
            columns = [col1, col2]

            for idx, (code, data) in enumerate(st.session_state.ai_reports.items()):
                with columns[idx % 2]:
                    with st.expander(f"📌 {data['name']} (分数: {data['score']})", expanded=True):
                        for n in data['news']:
                            st.caption(f"- {n.get('发布时间')}: {n.get('新闻标题')}")
                        st.markdown("---")
                        st.info(data['report'])

    # ================= Tab 2 =================
    with tab2:
        st.markdown("已切换为【腾讯极速独立接口】，完全绕开东方财富的反爬限制与系统代理，毫秒级抓取。")

        if st.button("🔄 刷新实时数据", use_container_width=True):
            with st.spinner("正在闪电拉取数据..."):
                target_codes = list(st.session_state.stock_pool.keys())
                pool_data = fetch_realtime_tencent(target_codes)

                if not pool_data.empty:
                    pool_data = pool_data.sort_values(by='今日涨跌', ascending=False)

                    anomalies = []
                    for _, row in pool_data.iterrows():
                        if row['今日涨跌'] > 5:
                            anomalies.append(f"🔥 【暴涨预警】 {row['名称']} 涨幅突破 {row['今日涨跌']}%！")
                        elif row['今日涨跌'] < -5:
                            anomalies.append(f"🧊 【破位预警】 {row['名称']} 跌幅达到 {row['今日涨跌']}%！")
                        if row['换手率'] > 15: anomalies.append(
                            f"🌪️ 【筹码松动】 {row['名称']} 换手率超 {row['换手率']}%！")

                    if anomalies:
                        st.error("🚨 发现盘中异动：")
                        for msg in anomalies: st.markdown(msg)
                    else:
                        st.success("🍵 当前走势平稳，未触发异动警报。")

                    pool_data['今日涨跌'] = pool_data['今日涨跌'].apply(lambda x: f"{x}%")
                    pool_data['换手率'] = pool_data['换手率'].apply(lambda x: f"{x}%")
                    st.dataframe(pool_data, use_container_width=True, hide_index=True)
                else:
                    st.warning("获取数据失败，请确保您是在交易日时间段内测试（或检查股票代码是否全对）。")


if __name__ == "__main__":
    main()