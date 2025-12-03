import streamlit as st
import pandas as pd
import datetime
from dateutil import parser
import plotly.express as px
import uuid
import time
import io

# --- 尝试导入 GitHub 库 ---
try:
    from github import Github, InputFileContent  # <--- 修正点1: 明确导入 InputFileContent
    HAS_GITHUB = True
except ImportError:
    HAS_GITHUB = False

# --- 页面配置 ---
st.set_page_config(page_title="世纪名城智慧收费系统 V12.1", layout="wide", page_icon="🏢")

# --- 0. 数据库初始化 ---
def init_df(key, columns):
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame(columns=columns)

init_df('ledger', ['流水号', '房号', '业主', '费用类型', '应收', '实收', '减免金额', '欠费', '收费区间', '状态', '收费日期', '收据编号', '备注', '操作人', '来源文件'])
init_df('parking_ledger', ['流水号', '车位编号', '车位类型', '业主/车主', '联系电话', '收费起始', '收费截止', '单价', '应收', '实收', '减免金额', '欠费', '收据编号', '收费日期', '备注', '操作人', '收费区间'])
init_df('rooms_db', ["房号", "业主", "联系电话", "备用电话", "房屋状态", "收费面积", "物业费单价", "物业费标准/年", "电梯费标准/年"])
init_df('waiver_requests', ['申请单号', '房号', '业主', '费用类型', '原应收', '申请减免金额', '拟实收', '申请原因', '申请人', '申请时间', '审批状态', '审批意见', '审批人'])
init_df('audit_logs', ['时间', '操作人', '动作', '详情'])

if 'parking_types' not in st.session_state:
    st.session_state.parking_types = ["产权车位", "月租车位", "子母车位", "临时车位"]

# --- 1. 核心工具函数 ---

def safe_concat(df_list):
    non_empty = [d for d in df_list if not d.empty]
    if not non_empty: return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)

def log_action(user, action, detail):
    new_log = pd.DataFrame([{
        "时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "操作人": str(user), "动作": str(action), "详情": str(detail)
    }])
    st.session_state.audit_logs = safe_concat([st.session_state.audit_logs, new_log])

def parse_date(date_val):
    if pd.isna(date_val) or str(date_val).strip() == "" or str(date_val).strip() == "nan": return ""
    s = str(date_val).replace('\n', ' ').split(' ')[0]
    try: return parser.parse(s, fuzzy=True).strftime("%Y-%m-%d")
    except: return ""

def clean_str(val):
    if pd.isna(val): return ""
    s = str(val).replace('\n', ' ').strip()
    if s.lower() == 'nan': return ""
    return s

def smart_read_file(uploaded_file, header_keywords=None):
    if uploaded_file is None: return None
    uploaded_file.seek(0)
    try:
        if uploaded_file.name.endswith('.csv'):
            try: df_raw = pd.read_csv(uploaded_file, header=None, encoding='utf-8')
            except: 
                uploaded_file.seek(0)
                df_raw = pd.read_csv(uploaded_file, header=None, encoding='gbk')
        else:
            df_raw = pd.read_excel(uploaded_file, header=None)
    except Exception as e:
        st.error(f"文件读取失败: {e}")
        return None

    header_row = -1
    if header_keywords:
        for i, row in df_raw.head(20).iterrows():
            row_str = " ".join(row.astype(str).tolist())
            hits = sum([1 for k in header_keywords if k in row_str])
            if hits >= 1:
                header_row = i
                break
    
    uploaded_file.seek(0)
    if header_row != -1:
        if uploaded_file.name.endswith('.csv'):
            try: return pd.read_csv(uploaded_file, header=header_row, encoding='utf-8')
            except: 
                uploaded_file.seek(0)
                return pd.read_csv(uploaded_file, header=header_row, encoding='gbk')
        else: return pd.read_excel(uploaded_file, header=header_row)
    return df_raw

# --- Gist 同步工具函数 (V12.1 修复版) ---
def get_gist_client():
    try:
        token = st.secrets.connections.github.token
        g = Github(token)
        return g
    except Exception as e:
        st.error(f"GitHub 连接配置错误: {e}")
        return None

def save_to_gist():
    """将所有 session_state 数据打包存入 Gist"""
    g = get_gist_client()
    if not g: return False
    
    try:
        gist_id = st.secrets.connections.github.gist_id
        gist = g.get_gist(gist_id)
        
        # 将 DataFrame 转为 CSV 字符串
        files_content = {}
        
        # 1. 物业台账
        ledger_csv = st.session_state.ledger.to_csv(index=False)
        # 修正点2: 直接使用 InputFileContent 类，去掉 st.secrets 前缀
        files_content["ledger.csv"] = InputFileContent(ledger_csv)
        
        # 2. 车位台账
        park_csv = st.session_state.parking_ledger.to_csv(index=False)
        files_content["parking.csv"] = InputFileContent(park_csv)
        
        # 3. 基础信息
        rooms_csv = st.session_state.rooms_db.to_csv(index=False)
        files_content["rooms.csv"] = InputFileContent(rooms_csv)
        
        # 4. 审批单
        waiver_csv = st.session_state.waiver_requests.to_csv(index=False)
        files_content["waiver.csv"] = InputFileContent(waiver_csv)

        # 5. 日志
        log_csv = st.session_state.audit_logs.to_csv(index=False)
        files_content
