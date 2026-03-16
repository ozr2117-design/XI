import streamlit as st
import pandas as pd
import akshare as ak
import plotly.graph_objects as go
from datetime import datetime
from plotly.subplots import make_subplots
import requests

# 全局风控配置
NEXT_REPORT_DATE = "2026-03-30"

st.set_page_config(page_title="锡产业链量化监控与风控看板", layout="wide", initial_sidebar_state="collapsed")

@st.cache_data(ttl=1800)
def fetch_000960_data():
    try:
        # 使用腾讯稳定接口获取前复权日K线数据，绕过akshare针对Streamlit Cloud的请求封锁
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz000960,day,,,150,qfq"
        res = requests.get(url, timeout=10)
        data = res.json()
        kline_data = data['data']['sz000960']['qfqday']
        
        # 腾讯接口返回格式：[日期, 开盘, 收盘, 最高, 最低, 成交量]
        df = pd.DataFrame(kline_data, columns=['Date', 'Open', 'Close', 'High', 'Low', 'Volume'])
        df = df[['Date', 'Close']].rename(columns={'Close': 'Close_000960'})
        df['Date'] = pd.to_datetime(df['Date'])
        df['Close_000960'] = df['Close_000960'].astype(float)
        
        return df.tail(150)
    except Exception as e:
        st.error(f"获取锡业股份历史行情失败: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def fetch_SN0_data():
    try:
        # 沪锡主力合约历史与最新行情：提取收盘价和日期
        df = ak.futures_main_sina(symbol="SN0")
        df = df[['日期', '收盘价']].rename(columns={'日期': 'Date', '收盘价': 'Close_SN0'})
        df['Date'] = pd.to_datetime(df['Date'])
        return df.tail(150)
    except Exception as e:
        st.error(f"获取沪锡主力合约历史行情失败: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def fetch_SND_data():
    try:
        # LME伦锡行情：作为参考，提取最新收盘价和日内涨跌幅
        df = ak.futures_foreign_hist(symbol="SND")
        if not df.empty:
            if 'close' in df.columns:
                close_col = 'close'
            elif '收盘价' in df.columns:
                close_col = '收盘价'
            else:
                return None, None
            
            if len(df) >= 2:
                latest = float(df.iloc[-1][close_col])
                prev = float(df.iloc[-2][close_col])
                pct = ((latest - prev) / prev) * 100
                return latest, pct
            elif len(df) == 1:
                return float(df.iloc[-1][close_col]), 0.0
        return None, None
    except Exception as e:
        st.error(f"获取LME伦锡行情失败: {e}")
        return None, None

@st.cache_data(ttl=1800)
def fetch_PB_data():
    try:
        # 获取市净率数据
        df = ak.stock_zh_valuation_baidu(symbol="000960", indicator="市净率", period="近十年")
        df['date'] = pd.to_datetime(df['date'])
        # 截取过去5年的数据
        five_years_ago = datetime.now() - pd.DateOffset(years=5)
        df = df[df['date'] >= five_years_ago].copy()
        return df
    except Exception as e:
        st.error(f"获取锡业股份历史市净率失败: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=1800)
def fetch_inventory_data():
    try:
        # 获取沪锡库存数据，取近180天数据 (这里直接取最后180行，或者通过日期过滤皆可)
        df = ak.futures_inventory_em(symbol="锡")
        if not df.empty and '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期'])
            return df.tail(180)
        return pd.DataFrame()
    except Exception:
        # 优雅降级，静默失败由外部判断
        return pd.DataFrame()

def main():
    st.title("🛡️ 锡产业链量化监控与风控看板")
    
    # 获取数据
    df_000960 = fetch_000960_data()
    df_SN0 = fetch_SN0_data()
    snd_latest, snd_pct = fetch_SND_data()
    
    if df_000960.empty or df_SN0.empty:
        st.error("数据拉取失败或尚未准备好。")
        st.stop()
        
    # 核心算法模块
    try:
        # 数据对齐：将锡业股份和沪锡历史数据转换DataFrame，设定日期为 Index，内连接合并确保交易日期对齐
        df_merged = pd.merge(df_000960, df_SN0, on='Date', how='inner').dropna()
        df_merged.set_index('Date', inplace=True)
        
        # 趋势引擎（MA60）：计算锡业股份收盘价的 60 日移动平均线
        df_merged['MA60'] = df_merged['Close_000960'].rolling(window=60).mean()
        
        # 估值引擎（Z-Score）：
        # Ratio = 锡业股份收盘价 / (沪锡收盘价 / 10000)
        df_merged['Ratio'] = df_merged['Close_000960'] / (df_merged['Close_SN0'] / 10000.0)
        
        # 滚动均值/标准差：Rolling_Mean 和 Rolling_Std (60天窗口)
        df_merged['Rolling_Mean'] = df_merged['Ratio'].rolling(window=60).mean()
        df_merged['Rolling_Std'] = df_merged['Ratio'].rolling(window=60).std()
        
        # 每日 Z-Score 计算
        df_merged['Z_Score'] = (df_merged['Ratio'] - df_merged['Rolling_Mean']) / df_merged['Rolling_Std']
        
        # 删除由于滚动计算产生的 NaN 缺失值
        df_merged.dropna(inplace=True)
    except Exception as e:
        st.error(f"数据处理引擎异常: {e}")
        st.stop()
        
    if df_merged.empty:
        st.warning("对齐并计算后有效历史数据不足，请稍后刷新重试。")
        st.stop()
        
    # 最新数据状态
    latest_data = df_merged.iloc[-1]
    prev_data = df_merged.iloc[-2] if len(df_merged) > 1 else latest_data
    
    latest_000960 = latest_data['Close_000960']
    latest_MA60 = latest_data['MA60']
    latest_SN0 = latest_data['Close_SN0']
    latest_zscore = latest_data['Z_Score']
    
    # 趋势判定：若最新收盘价 > 最新 MA60 则为 Uptrend
    trend = "Uptrend" if latest_000960 > latest_MA60 else "Downtrend"
    
    # 计算日内涨跌幅 (基于对齐的上一个交易日)
    pct_000960 = ((latest_000960 - prev_data['Close_000960']) / prev_data['Close_000960']) * 100
    pct_SN0 = ((latest_SN0 - prev_data['Close_SN0']) / prev_data['Close_SN0']) * 100
    
    # ---------------- 前端 UI 模块 ----------------
    
    st.markdown("### 📊 核心快照")
    
    # 页面最顶部展示锡业股份、沪锡、伦锡的最新价格及日内涨跌幅
    col1, col2, col3 = st.columns(3)
    col1.metric("锡业股份 (000960)", f"¥{latest_000960:.2f}", f"{pct_000960:.2f}%")
    col2.metric("沪锡主力 (SN0)", f"¥{latest_SN0:.0f}", f"{pct_SN0:.2f}%")
    if snd_latest is not None and snd_pct is not None:
        col3.metric("LME伦锡 (SND)", f"${snd_latest:.2f}", f"{snd_pct:.2f}%") 
    elif snd_latest is not None:
        col3.metric("LME伦锡 (SND)", f"${snd_latest:.2f}", "-")
    else:
        col3.metric("LME伦锡 (SND)", "N/A", "-")

    # 财报倒计时模块：获取当前系统日期计算还有多少个自然日
    today = datetime.now().date()
    report_date = datetime.strptime(NEXT_REPORT_DATE, "%Y-%m-%d").date()
    days_to_report = (report_date - today).days
    
    st.markdown("---")
    
    # 新增：核心参数观测指南折叠面板
    with st.expander("💡 核心参数观测指南 (点击展开使用说明)", expanded=False):
        st.markdown("""
### 1. 趋势过滤：定基调 (看 MA60 季线)
* 📈 **多头 (股价 > 季线)**：处于上升通道。策略是“持有”或“逢回踩买入”。此时可忽略 Z-Score 的超买预警。
* 📉 **空头 (股价 < 季线)**：处于下降通道。策略是“防守”和“左侧潜伏”。此时耐心死等 Z-Score 跌透。

### 2. 量化雷达：找买点 (看 Z-Score 股锡比)
* 🚨 **Z ≤ -2.0 (极度错杀)**：如果基本面未爆雷，这就是左侧最佳击球区（发生概率<3%）。
* 🟧 **-2.0 < Z ≤ -1.5 (观察区)**：资产开始被低估，准备好资金。
* ⚠️ **Z ≥ 1.5 (情绪过热)**：在空头趋势中代表反弹到顶，需减仓；在多头趋势中代表主升浪，可继续持有。

### 3. 安全边际：看底线 (看 PB 市净率)
* 周期股看市净率最准。历史上锡业股份的史诗级大底 PB 通常在 **1.2 ~ 1.5 倍** 之间。
* **绝佳共振**：如果 Z-Score 亮红灯，且 PB 刚好跌入此区间，属于百年难遇的绝对底部。

### 4. 基本面照妖镜：看现货库存
* 📉 **去库 (柱子下降)**：现货紧缺，价格上涨有真实产业支撑。
* 📈 **累库 (柱子上升)**：需求疲软，警惕锡价虚高回落风险。
        """)

    st.markdown("---")
    
    # 财报盲盒熔断预警
    if 0 <= days_to_report <= 7:
        st.error(f"☢️ **一级风控预警：财报盲盒期！**\n\n距离财报披露仅剩 **{days_to_report}** 天。鉴于管理层套保历史劣迹，业绩具有极高不确定性。\n\n**系统建议：无论当前指标多好，必须主动缩减仓位，规避黑天鹅！**")
    elif 7 < days_to_report <= 15:
        st.warning(f"⚠️ 距离财报披露还有 **{days_to_report}** 天，请停止左侧加仓，准备进入防守状态。")
        
    st.markdown("### 🚦 量化信号灯 (动态情境预警机)")
    
    # 量化信号逻辑
    signal_text = ""
    if trend == "Uptrend":
        if latest_zscore >= 1.5:
            signal_text = "🚀 **动量主升浪 (持有)**：牛市不言顶，忽略超买警报。"
        elif -1.0 <= latest_zscore <= 0.5:
            signal_text = "📈 **顺势回踩 (右侧买点)**：多头趋势中的估值回落，关注上车机会。"
        else:
            signal_text = "🟢 **多头震荡**：健康区间。"
    elif trend == "Downtrend":
        if latest_zscore <= -2.0:
            if 0 <= days_to_report <= 7:
                signal_text = "🚨 **极度错杀 (左侧击球区)**：跌穿-2倍标准差！极低概率，左侧价值凸显（注：若财报熔断已触发，则此信号失效）！"
            else:
                signal_text = "🚨 **极度错杀 (左侧击球区)**：跌穿-2倍标准差！极低概率，左侧价值凸显！"
        elif -2.0 < latest_zscore <= -1.5:
            signal_text = "🟧 **左侧观察区**：脱离宏观锚点，进入备战区间。"
        elif latest_zscore >= 1.5:
            signal_text = "⚠️ **弱势反弹**：警惕均值回归下跌风险。"
        else:
            signal_text = "⚪ **弱势观望**：耐心等待。"

    st.info(f"**当前趋势:** {'⬆️ 多头 (Uptrend)' if trend == 'Uptrend' else '⬇️ 空头 (Downtrend)'} | **最新 Z-Score:** {latest_zscore:.2f} \n\n {signal_text}")

    st.markdown("---")
    st.markdown("### 📈 可视化图表")
    
    # 可视化图表 (使用 plotly.graph_objects 构建上下两子图)
    fig = make_subplots(
        rows=2, cols=1, 
        shared_xaxes=True, 
        vertical_spacing=0.08, 
        row_heights=[0.6, 0.4],
        specs=[[{"secondary_y": True}], [{"secondary_y": False}]]
    )

    # 上子图：左轴锡业股份收盘价（蓝色线）和 MA60（紫虚线）；右轴沪锡收盘价（橙色线）
    fig.add_trace(go.Scatter(x=df_merged.index, y=df_merged['Close_000960'], 
                             line=dict(color='#1E90FF', width=2), name='锡业股份', mode='lines'),
                  row=1, col=1, secondary_y=False)
                  
    fig.add_trace(go.Scatter(x=df_merged.index, y=df_merged['MA60'], 
                             line=dict(color='#8A2BE2', width=2, dash='dash'), name='MA60', mode='lines'),
                  row=1, col=1, secondary_y=False)
                  
    fig.add_trace(go.Scatter(x=df_merged.index, y=df_merged['Close_SN0'], 
                             line=dict(color='#FF8C00', width=2), name='沪锡主力', mode='lines'),
                  row=1, col=1, secondary_y=True)

    # 下子图：Z-Score 折线（绿色）。添加水平基准线 (+2.0, 0, -1.5, -2.0)
    fig.add_trace(go.Scatter(x=df_merged.index, y=df_merged['Z_Score'], 
                             line=dict(color='#32CD32', width=2), name='Z-Score', mode='lines'),
                  row=2, col=1)

    # 下子图：基准水平线
    fig.add_hline(y=2.0, line_dash="dash", line_color="red", row=2, col=1, annotation_text="+2.0")
    fig.add_hline(y=0, line_dash="dash", line_color="gray", row=2, col=1)
    fig.add_hline(y=-1.5, line_dash="dash", line_color="orange", row=2, col=1, annotation_text="-1.5")
    fig.add_hline(y=-2.0, line_dash="dash", line_color="red", row=2, col=1, annotation_text="-2.0")

    # 全局布局：移动端自适应，关闭复杂交互栏，图例居下
    fig.update_layout(
        height=600,
        margin=dict(l=10, r=10, t=20, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
        dragmode=False,
        hovermode="x unified",
        template="plotly_white"
    )
    
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
    
    # ---------------- 新增模块一：绝对估值锚（PB 历史分位带） ----------------
    st.markdown("---")
    st.markdown("### ⚓ 绝对估值锚 (PB 历史分位带)")
    
    df_pb = fetch_PB_data()
    if df_pb is not None and not df_pb.empty and 'value' in df_pb.columns:
        # 计算过去 5 年的 PB 分位数
        current_pb = df_pb.iloc[-1]['value']
        pb_10 = df_pb['value'].quantile(0.10)
        pb_90 = df_pb['value'].quantile(0.90)
        
        # 计算当前所在历史分位百分比
        percentile = (df_pb['value'] < current_pb).mean() * 100
        
        # 智能总结
        if percentile <= 10:
            eval_text = "极度低估，处于左侧击球区底部。"
        elif percentile >= 90:
            eval_text = "极度高估，累积巨大回调风险。"
        elif percentile <= 30:
            eval_text = "偏向低估，具备安全边际。"
        elif percentile >= 70:
            eval_text = "偏向高估，需警惕估值杀。"
        else:
            eval_text = "绝对估值适中。"
            
        st.info(f"💡 当前 PB 为 **{current_pb:.2f}**，处于过去 5 年的 **{percentile:.1f}%** 分位，{eval_text}")
        
        # 绘制 PB 走势图
        fig_pb = go.Figure()
        fig_pb.add_trace(go.Scatter(x=df_pb['date'], y=df_pb['value'], 
                                   line=dict(color='#8A2BE2', width=2), name='市净率(PB)', mode='lines'))
        # 添加极度低估线和高估线
        fig_pb.add_hline(y=pb_90, line_dash="dash", line_color="red", annotation_text=f"90% 高估线 ({pb_90:.2f})")
        fig_pb.add_hline(y=pb_10, line_dash="dash", line_color="green", annotation_text=f"10% 低估线 ({pb_10:.2f})")
        
        fig_pb.update_layout(
            height=350, margin=dict(l=10, r=10, t=20, b=10),
            dragmode=False, hovermode="x unified", template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig_pb, use_container_width=True, config={'displayModeBar': False})
    else:
        st.warning("暂无法获取锡业股份历史市净率数据。")
        
    # ---------------- 新增模块二：底层供需照妖镜（交易所显性库存跟踪） ----------------
    st.markdown("---")
    st.markdown("### 🪞 底层供需照妖镜 (交易所显性库存跟踪)")
    
    df_inv = fetch_inventory_data()
    if df_inv is not None and not df_inv.empty and '日期' in df_inv.columns and '库存' in df_inv.columns:
        # 库存走势：判断首尾是在累库还是去库
        first_inv = df_inv.iloc[0]['库存']
        last_inv = df_inv.iloc[-1]['库存']
        inv_diff = last_inv - first_inv
        
        if inv_diff > 0:
            inv_status = f"🔴 **累库阶段** (近半年来增加 {inv_diff:.0f} 吨)"
        else:
            inv_status = f"🟢 **去库阶段** (近半年来减少 {abs(inv_diff):.0f} 吨)"
            
        st.markdown(f"**当前状态:** {inv_status}")
        
        # 绘制近半年库存柱状图
        fig_inv = go.Figure()
        fig_inv.add_trace(go.Bar(x=df_inv['日期'], y=df_inv['库存'], 
                                 marker_color='#4682B4', name='显性库存'))
        
        fig_inv.update_layout(
            height=300, margin=dict(l=10, r=10, t=20, b=10),
            dragmode=False, hovermode="x unified", template="plotly_white"
        )
        st.plotly_chart(fig_inv, use_container_width=True, config={'displayModeBar': False})
    else:
        st.warning("暂无法获取最新库存数据，源接口维护中。")

if __name__ == "__main__":
    main()
