import streamlit as st
import pandas as pd
import akshare as ak
import plotly.graph_objects as go
from datetime import datetime
from plotly.subplots import make_subplots

# 全局风控配置
NEXT_REPORT_DATE = "2026-03-30"

st.set_page_config(page_title="锡产业链量化监控与风控看板", layout="wide", initial_sidebar_state="collapsed")

@st.cache_data(ttl=1800)
def fetch_000960_data():
    try:
        # A股历史与最新行情：提取收盘价和日期
        df = ak.stock_zh_a_hist(symbol="000960", period="daily")
        df = df[['日期', '收盘']].rename(columns={'日期': 'Date', '收盘': 'Close_000960'})
        df['Date'] = pd.to_datetime(df['Date'])
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
        # LME伦锡行情：作为参考，提取最新收盘价
        df = ak.futures_foreign_hist(symbol="SND")
        if not df.empty:
            if 'close' in df.columns:
                return float(df.iloc[-1]['close'])
            elif '收盘价' in df.columns:
                return float(df.iloc[-1]['收盘价'])
        return None
    except Exception as e:
        st.error(f"获取LME伦锡行情失败: {e}")
        return None

def main():
    st.title("🛡️ 锡产业链量化监控与风控看板")
    
    # 获取数据
    df_000960 = fetch_000960_data()
    df_SN0 = fetch_SN0_data()
    snd_latest = fetch_SND_data()
    
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
    if snd_latest is not None:
        col3.metric("LME伦锡 (SND)", f"${snd_latest:.2f}", "-") 
    else:
        col3.metric("LME伦锡 (SND)", "N/A", "-")

    # 财报倒计时模块：获取当前系统日期计算还有多少个自然日
    today = datetime.now().date()
    report_date = datetime.strptime(NEXT_REPORT_DATE, "%Y-%m-%d").date()
    days_to_report = (report_date - today).days
    
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

if __name__ == "__main__":
    main()
