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
    from github import Github, InputFileContent
    HAS_GITHUB = True
except ImportError:
    HAS_GITHUB = False

# --- 页面配置 ---
st.set_page_config(page_title="世纪名城智慧收费系统 V15.0 (行业规范版)", layout="wide", page_icon="🏢")

# --- 0. 数据库初始化 ---
def init_df(key, columns):
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame(columns=columns)

# 核心业务表
init_df('ledger', ['流水号', '房号', '业主', '费用类型', '应收', '实收', '减免金额', '欠费', '收费区间', '状态', '收费日期', '收据编号', '备注', '操作人', '来源文件'])
init_df('parking_ledger', ['流水号', '车位编号', '车位类型', '业主/车主', '联系电话', '收费起始', '收费截止', '单价', '应收', '实收', '减免金额', '欠费', '收据编号', '收费日期', '备注', '操作人', '收费区间'])
init_df('rooms_db', ["房号", "业主", "联系电话", "备用电话", "房屋状态", "收费面积", "物业费单价", "物业费标准/年", "电梯费标准/年"])
init_df('waiver_requests', ['申请单号', '房号', '业主', '费用类型', '原应收', '申请减免金额', '拟实收', '申请原因', '申请人', '申请时间', '审批状态', '审批意见', '审批人', '关联账单号'])
init_df('audit_logs', ['时间', '操作人', '动作', '详情'])

# 资金池表 (V14+)
init_df('wallet_db', ['房号', '业主', '账户余额', '最后更新时间'])
init_df('transaction_log', ['流水号', '时间', '房号', '交易类型', '发生金额', '账户余额快照', '关联单号', '备注', '操作人'])

# 用户权限表
if 'user_db_df' not in st.session_state:
    default_users = [
        {"username": "admin", "password": "admin123", "role": "管理员"},
        {"username": "audit", "password": "audit123", "role": "审核员"},
        {"username": "clerk", "password": "clerk123", "role": "录入员"},
        {"username": "cfo", "password": "cfo123", "role": "财务总监"}
    ]
    st.session_state.user_db_df = pd.DataFrame(default_users)

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

# 钱包操作逻辑
def update_wallet(room, owner, amount, trans_type, ref_id, remark, user):
    w_idx = st.session_state.wallet_db[st.session_state.wallet_db['房号'] == room].index
    if w_idx.empty:
        current_balance = 0.0
        new_wallet = pd.DataFrame([{
            '房号': room, '业主': owner, '账户余额': 0.0, '最后更新时间': str(datetime.datetime.now())
        }])
        st.session_state.wallet_db = safe_concat([st.session_state.wallet_db, new_wallet])
        w_idx = st.session_state.wallet_db[st.session_state.wallet_db['房号'] == room].index
    else:
        current_balance = float(st.session_state.wallet_db.at[w_idx[0], '账户余额'])

    new_balance = current_balance + amount
    st.session_state.wallet_db.at[w_idx[0], '账户余额'] = new_balance
    st.session_state.wallet_db.at[w_idx[0], '最后更新时间'] = str(datetime.datetime.now())
    
    new_trans = pd.DataFrame([{
        '流水号': str(uuid.uuid4())[:8], '时间': str(datetime.datetime.now()),
        '房号': room, '交易类型': trans_type, '发生金额': amount, '账户余额快照': new_balance,
        '关联单号': ref_id, '备注': remark, '操作人': user
    }])
    st.session_state.transaction_log = safe_concat([st.session_state.transaction_log, new_trans])
    return True

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

# --- Gist 同步工具 ---
def get_gist_client():
    try:
        token = st.secrets.connections.github.token
        g = Github(token)
        return g
    except Exception as e:
        st.error(f"GitHub 连接配置错误: {e}")
        return None

def save_to_gist():
    g = get_gist_client()
    if not g: return False
    try:
        gist_id = st.secrets.connections.github.gist_id
        gist = g.get_gist(gist_id)
        files_content = {}
        files_content["ledger.csv"] = InputFileContent(st.session_state.ledger.fillna("").astype(str).to_csv(index=False))
        files_content["parking.csv"] = InputFileContent(st.session_state.parking_ledger.fillna("").astype(str).to_csv(index=False))
        files_content["rooms.csv"] = InputFileContent(st.session_state.rooms_db.fillna("").astype(str).to_csv(index=False))
        files_content["waiver.csv"] = InputFileContent(st.session_state.waiver_requests.fillna("").astype(str).to_csv(index=False))
        files_content["audit.csv"] = InputFileContent(st.session_state.audit_logs.fillna("").astype(str).to_csv(index=False))
        files_content["users.csv"] = InputFileContent(st.session_state.user_db_df.to_csv(index=False))
        files_content["wallet.csv"] = InputFileContent(st.session_state.wallet_db.fillna("").astype(str).to_csv(index=False))
        files_content["trans_log.csv"] = InputFileContent(st.session_state.transaction_log.fillna("").astype(str).to_csv(index=False))
        gist.edit(files=files_content)
        return True
    except Exception as e:
        st.error(f"Gist 保存失败: {e}")
        return False

def load_from_gist():
    g = get_gist_client()
    if not g: return False
    try:
        gist_id = st.secrets.connections.github.gist_id
        gist = g.get_gist(gist_id)
        files = gist.files
        def read_gist_csv(filename):
            if filename in files:
                content = files[filename].content
                if not content.strip(): return pd.DataFrame()
                return pd.read_csv(io.StringIO(content)).fillna("")
            return pd.DataFrame()

        df1 = read_gist_csv("ledger.csv")
        if not df1.empty: st.session_state.ledger = df1
        df2 = read_gist_csv("parking.csv")
        if not df2.empty: st.session_state.parking_ledger = df2
        df3 = read_gist_csv("rooms.csv")
        if not df3.empty: st.session_state.rooms_db = df3
        df4 = read_gist_csv("waiver.csv")
        if not df4.empty: st.session_state.waiver_requests = df4
        df5 = read_gist_csv("audit.csv")
        if not df5.empty: st.session_state.audit_logs = df5
        df6 = read_gist_csv("users.csv")
        if not df6.empty: st.session_state.user_db_df = df6
        df7 = read_gist_csv("wallet.csv")
        if not df7.empty: st.session_state.wallet_db = df7
        df8 = read_gist_csv("trans_log.csv")
        if not df8.empty: st.session_state.transaction_log = df8
        return True
    except Exception as e:
        st.error(f"Gist 读取失败: {e}")
        return False

# --- 2. 导入逻辑 (V9.5 资金池版) ---
def ingest_payment_block(room, owner, prop_std, elev_std, pay_date, receipt, period, total_paid):
    recs = []
    alloc_prop = min(total_paid, prop_std) if prop_std > 0 else total_paid
    if elev_std == 0: alloc_prop = total_paid
    remain_after_prop = total_paid - alloc_prop
    bal_p = prop_std - alloc_prop
    status_p = "已缴"
    if bal_p > 0.1: status_p = "部分欠费"
    if alloc_prop == 0 and prop_std > 0: status_p = "未缴"
    if bal_p < -0.1: status_p = "溢缴/预收"
    recs.append({"流水号": str(uuid.uuid4())[:8], "房号": room, "业主": owner, "费用类型": "物业服务费", "应收": prop_std, "实收": alloc_prop, "减免金额": 0.0, "欠费": max(0, bal_p), "收费区间": period, "状态": status_p, "收费日期": pay_date, "收据编号": receipt, "备注": "导入", "操作人": st.session_state.username, "来源文件": "2025台账"})
    if elev_std > 0 or remain_after_prop > 0:
        alloc_elev = remain_after_prop
        bal_e = elev_std - alloc_elev
        status_e = "已缴"
        if bal_e > 0.1: status_e = "部分欠费"
        if alloc_elev == 0 and elev_std > 0: status_e = "未缴"
        if bal_e < -0.1: status_e = "溢缴/预收"
        recs.append({"流水号": str(uuid.uuid4())[:8], "房号": room, "业主": owner, "费用类型": "电梯运行费", "应收": elev_std, "实收": alloc_elev, "减免金额": 0.0, "欠费": max(0, bal_e), "收费区间": period, "状态": status_e, "收费日期": pay_date, "收据编号": receipt, "备注": "导入", "操作人": st.session_state.username, "来源文件": "2025台账"})
    return recs

def process_2025_import(file_prop):
    imported_recs = []
    imported_rooms = []
    df = smart_read_file(file_prop, header_keywords=["单元", "房号", "业主"])
    if df is not None:
        total_rows = len(df)
        progress = st.progress(0)
        for idx, row in df.iterrows():
            if idx % 100 == 0: progress.progress(min(idx / total_rows, 1.0))
            try:
                if len(row) < 22: continue 
                room = clean_str(row.iloc[1])
                owner = clean_str(row.iloc[2])
                if not room or room == 'nan': continue
                def get_f(val):
                    try: return float(val)
                    except: return 0.0
                prop_std = get_f(row.iloc[8])
                elev_std = get_f(row.iloc[9])
                imported_rooms.append({"房号": room, "业主": owner, "联系电话": clean_str(row.iloc[3]), "备用电话": clean_str(row.iloc[4]), "房屋状态": clean_str(row.iloc[5]), "收费面积": get_f(row.iloc[6]), "物业费单价": get_f(row.iloc[7]), "物业费标准/年": prop_std, "电梯费标准/年": elev_std})
                pay_date = parse_date(row.iloc[16]) 
                receipt = clean_str(row.iloc[17])   
                period_val = clean_str(row.iloc[19]) 
                period = period_val if period_val else "2025.8.6-2026.8.5"
                amt_u = get_f(row.iloc[20])
                val_v = row.iloc[21]
                is_v_date = False
                if pd.notnull(val_v) and len(str(val_v)) > 6 and any(c in str(val_v) for c in ['.','-']) and not str(val_v).replace('.','').isdigit():
                     is_v_date = True
                amt_v = 0.0
                if not is_v_date: amt_v = get_f(val_v)
                total_paid_1 = amt_u + amt_v
                if total_paid_1 > 0 or prop_std > 0:
                    imported_recs.extend(ingest_payment_block(room, owner, prop_std, elev_std, pay_date, receipt, period, total_paid_1))
                if is_v_date and len(row) >= 26:
                    date2 = parse_date(val_v)
                    rec2 = clean_str(row.iloc[22])
                    prd2 = clean_str(row.iloc[23])
                    if not prd2: prd2 = period
                    amt_y = get_f(row.iloc[24])
                    amt_z = get_f(row.iloc[25])
                    total_paid_2 = amt_y + amt_z
                    if total_paid_2 > 0:
                        imported_recs.extend(ingest_payment_block(room, owner, 0, 0, date2, rec2, prd2, total_paid_2))
            except Exception as e: continue
        progress.empty()
    return imported_recs, imported_rooms

def process_2024_arrears(file_old):
    imported_recs = []
    df = smart_read_file(file_old, header_keywords=["房号", "单元", "业主", "姓名", "欠费", "合计", "金额"])
    if df is not None:
        cols = df.columns.astype(str)
        c_room = next((c for c in cols if '房号' in c or '单元' in c), df.columns[0])
        c_owner = next((c for c in cols if '业主' in c or '姓名' in c), df.columns[1])
        c_amt = next((c for c in cols if '合计' in c or '欠费' in c and '年' not in c or '金额' in c), df.columns[-1])
        c_period = next((c for c in cols if '年限' in c or '周期' in c or '区间' in c), None)

        for idx, row in df.iterrows():
            try:
                r = clean_str(row[c_room])
                if not r or '合计' in r: continue
                o = clean_str(row[c_owner])
                try: m = float(row[c_amt])
                except: m = 0.0
                p_val = "2024.8.6-2025.8.5"
                if c_period:
                    val = clean_str(row[c_period])
                    if val: p_val = val
                if m > 0:
                    imported_recs.append({"流水号": str(uuid.uuid4())[:8], "房号": r, "业主": o, "费用类型": "物业服务费", "应收": m, "实收": 0.0, "减免金额": 0.0, "欠费": m, "收费区间": p_val, "状态": "历史欠费", "收费日期": "", "收据编号": "", "备注": "2024难缠户", "操作人": st.session_state.username, "来源文件": "2024欠费表"})
            except: continue
    return imported_recs

def process_parking_import(file_park):
    imported_park = []
    if file_park:
        df = smart_read_file(file_park, header_keywords=["车位", "业主"])
        if df is not None:
            for idx, row in df.iterrows():
                try:
                    room = clean_str(row.iloc[1])
                    if not room: continue
                    owner = clean_str(row.iloc[2])
                    car_no = clean_str(row.iloc[4])
                    pay_date = parse_date(row.iloc[15])
                    period = clean_str(row.iloc[17])
                    try: amount = float(row.iloc[18])
                    except: amount = 0.0
                    receipt = clean_str(row.iloc[12])
                    if not receipt: receipt = clean_str(row.iloc[16])
                    if amount > 0:
                        imported_park.append({"流水号": str(uuid.uuid4())[:8], "车位编号": car_no, "车位类型": "导入车位", "业主/车主": f"{owner}({room})", "联系电话": "", "收费起始": period.split('-')[0] if '-' in period else "", "收费截止": period.split('-')[1] if '-' in period else "", "收费区间": period, "单价": 0.0, "应收": amount, "实收": amount, "减免金额": 0.0, "欠费": 0.0, "收据编号": receipt, "收费日期": pay_date, "备注": "批量导入", "操作人": st.session_state.username})
                except: continue
    return imported_park

# --- 3. 权限与登录 ---
def check_login():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.user_role = ""
    if not st.session_state.logged_in:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 世纪名城 V15.0")
            user = st.text_input("账号")
            pwd = st.text_input("密码", type="password")
            if st.button("登录", use_container_width=True):
                clean_user = user.strip().lower()
                clean_pwd = pwd.strip()
                user_df = st.session_state.user_db_df
                match = user_df[user_df['username'] == clean_user]
                if not match.empty and str(match.iloc[0]['password']) == clean_pwd:
                    st.session_state.logged_in = True
                    st.session_state.username = clean_user
                    st.session_state.user_role = match.iloc[0]['role']
                    st.rerun()
                else: st.error("错误")
        return False
    return True

def logout():
    st.session_state.logged_in = False
    st.rerun()

# --- 4. 主程序 ---
def main():
    if not check_login(): return
    role = st.session_state.user_role
    user = st.session_state.username
    
    with st.sidebar:
        st.title("🏢 世纪名城")
        st.info(f"👤 {user} | {role}")
        
        with st.expander("☁️ Gist 数据库同步", expanded=True):
            if HAS_GITHUB:
                if st.button("💾 保存数据"):
                    with st.spinner("同步中..."):
                        if save_to_gist(): st.success("已保存")
                if st.button("📥 恢复数据"):
                    with st.spinner("拉取中..."):
                        if load_from_gist(): 
                            st.success("已恢复")
                            time.sleep(1)
                            st.rerun()
            else: st.error("缺库")

        st.divider()
        menu_options = ["📊 运营驾驶舱"] # 所有人
        if role in ["录入员", "管理员"]: menu_options.extend(["📝 应收开单", "💰 钱包充值", "💸 收银台", "🅿️ 车位管理", "📨 发起减免", "📥 数据导入"])
        if role in ["审核员", "管理员"]: menu_options.extend(["✅ 减免审批", "🔧 交易纠错"])
        if role in ["财务总监", "管理员"]: menu_options.extend(["💰 财务决策中心"])
        menu_options.extend(["🔍 综合查询", "⚙️ 基础配置", "👤 个人中心"])
        if role == "管理员": menu_options.extend(["🛡️ 审计日志", "👥 账号管理"])

        menu = st.radio("功能导航", menu_options)
        if st.button("退出"): logout()

    # === 模块1: 运营驾驶舱 ===
    if menu == "📊 运营驾驶舱":
        st.title("📊 运营状况概览")
        df_prop = st.session_state.ledger.copy()
        df_park = st.session_state.parking_ledger.copy()
        df_all = safe_concat([df_prop, df_park])
        
        if not df_all.empty:
            for col in ['应收', '实收', '减免金额']:
                df_all[col] = pd.to_numeric(df_all[col], errors='coerce').fillna(0)
            df_all['余额'] = df_all['应收'] - df_all['实收'] - df_all['减免金额']
            
            agg = df_all.groupby(['房号', '业主']).agg({'余额': 'sum'}).reset_index()
            arrears = agg[agg['余额'] > 0.1]['余额'].sum()
            prepay = agg[agg['余额'] < -0.1]['余额'].sum() * -1
            
            c1, c2, c3 = st.columns(3)
            c1.metric("总实收", f"¥{df_all['实收'].sum():,.0f}")
            c2.metric("当前欠费", f"¥{arrears:,.0f}", delta="待追缴", delta_color="inverse")
            c3.metric("当前预收", f"¥{prepay:,.0f}", delta="资金池")
        else: st.info("暂无数据")

    # === 模块: 财务决策中心 (V15.0 升级: 收缴率KPI) ===
    elif menu == "💰 财务决策中心":
        st.title("💰 财务决策中心")
        df = st.session_state.ledger.copy()
        df_p = st.session_state.parking_ledger.copy()
        df_all = safe_concat([df, df_p])
        
        if not df_all.empty:
            # V15.0 新增: 收缴率 KPI
            current_ys = df_all['应收'].sum()
            current_ss = df_all['实收'].sum() + df_all['减免金额'].sum()
            collection_rate = (current_ss / current_ys * 100) if current_ys > 0 else 0
            
            k1, k2, k3 = st.columns(3)
            k1.metric("综合收缴率 (KPI)", f"{collection_rate:.2f}%")
            k2.metric("累计减免金额", f"¥{df_all['减免金额'].sum():,.2f}")
            k3.metric("累计实收现金", f"¥{df_all['实收'].sum():,.2f}")
            
            st.divider()
            
            t1, t2 = st.tabs(["月度收入报表", "现金流趋势"])
            with t1:
                df_all['收费日期'] = pd.to_datetime(df_all['收费日期'], errors='coerce')
                df_all['月度'] = df_all['收费日期'].dt.to_period('M').astype(str)
                pivot = df_all.pivot_table(index='月度', columns='费用类型', values='实收', aggfunc='sum', fill_value=0, margins=True)
                st.dataframe(pivot, use_container_width=True)
                st.download_button("📥 导出报表", pivot.to_csv().encode('utf-8-sig'), "monthly_report.csv")
            with t2:
                daily = df_all.groupby('收费日期')['实收'].sum().reset_index()
                st.line_chart(daily.set_index('收费日期'))
        else: st.info("暂无数据")

    # === 模块: 应收开单 ===
    elif menu == "📝 应收开单":
        st.title("📝 费用发生录入 (生成账单)")
        c1, c2 = st.columns(2)
        sel_room = c1.selectbox("选择房号", st.session_state.rooms_db['房号'].unique())
        
        with st.form("bill"):
            f_type = st.selectbox("类型", ["物业服务费", "电梯运行费", "公摊费"])
            f_period = st.text_input("周期", "2025.8.6-2026.8.5")
            f_ys = st.number_input("应收金额", 1000.0)
            if st.form_submit_button("开单"):
                info = st.session_state.rooms_db[st.session_state.rooms_db['房号']==sel_room]
                owner = info.iloc[0]['业主'] if not info.empty else "未知"
                new_rec = pd.DataFrame([{
                    "流水号": str(uuid.uuid4())[:8], "房号": sel_room, "业主": owner,
                    "费用类型": f_type, "应收": f_ys, "实收": 0.0, "减免金额": 0.0, 
                    "欠费": f_ys, "收费区间": f_period, "状态": "未缴", 
                    "收费日期": "", "收据编号": "", "备注": "手动开单", "操作人": user, "来源文件": "手工"
                }])
                st.session_state.ledger = safe_concat([st.session_state.ledger, new_rec])
                log_action(user, "开具账单", f"房号{sel_room} 应收{f_ys}")
                st.success("账单已生成")

    # === 模块: 钱包充值 ===
    elif menu == "💰 钱包充值":
        st.title("💰 钱包账户充值")
        sel_room = st.selectbox("充值房号", st.session_state.rooms_db['房号'].unique())
        curr = 0.0
        if not st.session_state.wallet_db.empty:
            w = st.session_state.wallet_db[st.session_state.wallet_db['房号']==sel_room]
            if not w.empty: curr = float(w.iloc[0]['账户余额'])
        st.metric("当前余额", f"¥{curr:,.2f}")
        
        with st.form("recharge"):
            amt = st.number_input("充值金额", 1000.0)
            rmk = st.text_input("备注")
            if st.form_submit_button("充值"):
                info = st.session_state.rooms_db[st.session_state.rooms_db['房号']==sel_room]
                owner = info.iloc[0]['业主'] if not info.empty else "未知"
                update_wallet(sel_room, owner, amt, "充值", "", rmk, user)
                st.success("充值成功")
                time.sleep(0.5)
                st.rerun()

    # === 模块: 收银台 (核销) ===
    elif menu == "💸 收银台":
        st.title("💸 收银台 (核销支付)")
        sel_room = st.selectbox("选择房号", st.session_state.rooms_db['房号'].unique())
        
        # 钱包信息
        curr_bal = 0.0
        if not st.session_state.wallet_db.empty:
            w = st.session_state.wallet_db[st.session_state.wallet_db['房号']==sel_room]
            if not w.empty: curr_bal = float(w.iloc[0]['账户余额'])
        st.info(f"👛 钱包余额: ¥{curr_bal:,.2f}")
        
        # 待付账单
        df = st.session_state.ledger
        unpaid = df[(df['房号']==sel_room) & (df['欠费']>0.1)]
        if not unpaid.empty:
            bill_opts = {f"{r['费用类型']} ({r['收费区间']}) - 欠¥{r['欠费']}": i for i, r in unpaid.iterrows()}
            sel_bills = st.multiselect("勾选账单", options=bill_opts.keys())
            
            if sel_bills:
                total_pay = sum([unpaid.loc[bill_opts[k], '欠费'] for k in sel_bills])
                st.write(f"待付: ¥{total_pay:,.2f}")
                if curr_bal >= total_pay:
                    if st.button("✅ 确认支付"):
                        update_wallet(sel_room, unpaid.iloc[0]['业主'], -total_pay, "核销", "多笔", "收银台", user)
                        for k in sel_bills:
                            idx = bill_opts[k]
                            st.session_state.ledger.at[idx, '实收'] += st.session_state.ledger.at[idx, '欠费']
                            st.session_state.ledger.at[idx, '欠费'] = 0.0
                            st.session_state.ledger.at[idx, '状态'] = '已缴'
                            st.session_state.ledger.at[idx, '收费日期'] = str(datetime.date.today())
                        st.success("支付成功")
                        time.sleep(1)
                        st.rerun()
                else: st.error("余额不足")
        else: st.success("无欠费")

    # === 模块: 交易纠错 (V15.0 升级: 红字冲销) ===
    elif menu == "🔧 交易纠错":
        st.title("🔧 交易纠错 (冲红)")
        q_id = st.text_input("输入流水号")
        if q_id:
            target = st.session_state.ledger[st.session_state.ledger['流水号'] == q_id]
            if not target.empty:
                st.dataframe(target)
                if st.button("🔴 冲红/撤销此单"):
                    target_row = target.iloc[0].copy()
                    # 生成反向记录
                    new_rec = target_row.copy()
                    new_rec['流水号'] = "REV-" + str(uuid.uuid4())[:8]
                    new_rec['应收'] = -float(target_row['应收'])
                    new_rec['实收'] = -float(target_row['实收'])
                    new_rec['减免金额'] = -float(target_row['减免金额'])
                    new_rec['欠费'] = -float(target_row['欠费'])
                    new_rec['备注'] = f"冲销原单:{target_row['流水号']} - 操作人:{user}"
                    new_rec['收费日期'] = str(datetime.date.today())
                    
                    st.session_state.ledger = safe_concat([st.session_state.ledger, pd.DataFrame([new_rec])])
                    log_action(user, "交易冲红", f"冲销了流水号 {q_id}")
                    st.success("红字冲销记录已生成")
            else: st.error("未找到")

    # === 模块: 综合查询 (V15.0 升级: 电子收据) ===
    elif menu == "🔍 综合查询":
        st.title("🔍 业主全景查询")
        q = st.text_input("输入房号 / 业主 / 收据号")
        if q:
            st.markdown("### 📜 交易流水")
            df = st.session_state.ledger
            res = df[df['房号'].astype(str).str.contains(q, na=False) | df['业主'].astype(str).str.contains(q, na=False)]
            st.dataframe(res, use_container_width=True)
            
            # V15.0 新增: 电子收据
            st.markdown("#### 🖨️ 电子收据预览")
            if not res.empty:
                sel_id = st.selectbox("选择一笔流水生成收据", res['流水号'])
                if st.button("生成收据"):
                    rec_row = res[res['流水号']==sel_id].iloc[0]
                    receipt_txt = f"""
                    ========================================
                              世纪名城物业管理费收据
                    ========================================
                    流水号: {rec_row['流水号']}
                    日  期: {rec_row['收费日期']}
                    房  号: {rec_row['房号']}
                    业  主: {rec_row['业主']}
                    ----------------------------------------
                    项  目: {rec_row['费用类型']}
                    周  期: {rec_row['收费区间']}
                    ----------------------------------------
                    实收金额: ¥ {rec_row['实收']}
                    备    注: {rec_row['备注']}
                    ----------------------------------------
                    收款人: {rec_row['操作人']}
                    ========================================
                    """
                    st.code(receipt_txt)

    # === 其他通用模块 (保持不变) ===
    elif menu == "🅿️ 车位管理":
        st.title("🅿️ 车位管理")
        t1, t2 = st.tabs(["录入", "台账"])
        with t1:
            with st.form("park"):
                c1, c2 = st.columns(2)
                p_no = c1.text_input("车位编号")
                p_type = c2.selectbox("类型", st.session_state.parking_types)
                p_owner = c1.text_input("车主")
                p_ys = c2.number_input("应收", 360.0)
                p_ss = c1.number_input("实收", 360.0)
                p_rec = c2.text_input("收据编号")
                p_period = st.text_input("收费区间")
                p_waive = st.number_input("减免", 0.0)
                if st.form_submit_button("提交"):
                    new_p = pd.DataFrame([{
                        "流水号": str(uuid.uuid4())[:8], "车位编号": p_no, "车位类型": p_type,
                        "业主/车主": p_owner, "应收": p_ys, "实收": p_ss, "减免金额": p_waive, "欠费": p_ys-p_ss-p_waive,
                        "收据编号": p_rec, "收费日期": str(datetime.date.today()), "收费区间": p_period, "操作人": user
                    }])
                    st.session_state.parking_ledger = safe_concat([st.session_state.parking_ledger, new_p])
                    st.success("成功")
        with t2:
            st.dataframe(st.session_state.parking_ledger, use_container_width=True)

    elif menu == "📨 减免与审批":
        st.title("减免审批")
        t1, t2 = st.tabs(["发起", "审批"])
        with t1:
            sel = st.selectbox("房号", st.session_state.rooms_db['房号'].unique() if not st.session_state.rooms_db.empty else [])
            # 必须选择一个未付账单来减免
            df = st.session_state.ledger
            unpaid = df[(df['房号']==sel) & (df['欠费']>0.1)]
            if not unpaid.empty:
                bill_opts = {f"{r['费用类型']} - 欠¥{r['欠费']}": r['流水号'] for i, r in unpaid.iterrows()}
                target_bill = st.selectbox("选择账单", list(bill_opts.keys()))
                target_id = bill_opts[target_bill]
                with st.form("w"):
                    amt = st.number_input("减免金额")
                    rsn = st.text_area("原因")
                    if st.form_submit_button("提交"):
                        row = df[df['流水号']==target_id].iloc[0]
                        req = pd.DataFrame([{
                            '申请单号':str(uuid.uuid4())[:6], '房号':sel, '申请减免金额':amt, 
                            '申请原因':rsn, '审批状态':'待审批', '申请人':user, '申请时间':str(datetime.date.today()), 
                            '费用类型':row['费用类型'], '原应收':row['应收'], '拟实收':row['应收']-amt, '关联账单号':target_id
                        }])
                        st.session_state.waiver_requests = safe_concat([st.session_state.waiver_requests, req])
                        st.success("已提交")
            else: st.info("无欠费账单")
        with t2:
            if role in ["管理员","审核员"]:
                p = st.session_state.waiver_requests[st.session_state.waiver_requests['审批状态']=='待审批']
                if not p.empty:
                    for i, r in p.iterrows():
                        with st.expander(f"{r['房号']} 减免 ¥{r['申请减免金额']}"):
                            if st.button("通过", key=f"p_{i}"):
                                st.session_state.waiver_requests.at[i,'审批状态']='已通过'
                                # 更新原账单
                                target_id = r['关联账单号']
                                ledger_idx = st.session_state.ledger[st.session_state.ledger['流水号']==target_id].index
                                if not ledger_idx.empty:
                                    idx = ledger_idx[0]
                                    st.session_state.ledger.at[idx, '减免金额'] += r['申请减免金额']
                                    st.session_state.ledger.at[idx, '欠费'] -= r['申请减免金额']
                                    if st.session_state.ledger.at[idx, '欠费'] <= 0:
                                        st.session_state.ledger.at[idx, '状态'] = '减免结清'
                                log_action(user, "审批通过", f"单号{r['申请单号']}")
                                st.rerun()
                else: st.info("无待办")

    elif menu == "📥 数据导入":
        st.title("数据导入")
        t1, t2 = st.tabs(["2025台账/车位", "2024历史欠费"])
        with t1:
            f1 = st.file_uploader("2025物业费", key="u1")
            f2 = st.file_uploader("车位费", key="u2")
            if st.button("导入"):
                if f1 or f2:
                    r1, r2 = process_2025_import(f1)
                    p = process_parking_import(f2)
                    if r1: st.session_state.ledger = safe_concat([st.session_state.ledger, pd.DataFrame(r1)])
                    if p: st.session_state.parking_ledger = safe_concat([st.session_state.parking_ledger, pd.DataFrame(p)])
                    if r2: st.session_state.rooms_db = pd.DataFrame(r2).drop_duplicates(subset='房号', keep='last')
                    log_action(user, "批量导入", f"物业费{len(r1)}条, 车位{len(p)}条")
                    st.success(f"导入完成")
                    time.sleep(1)
                    st.rerun()
        with t2:
            f3 = st.file_uploader("2024欠费", key="u3")
            if st.button("导入欠费"):
                if f3:
                    r3 = process_2024_arrears(f3)
                    if r3:
                        st.session_state.ledger = safe_concat([st.session_state.ledger, pd.DataFrame(r3)])
                        log_action(user, "欠费导入", f"历史欠费{len(r3)}条")
                        st.success(f"导入 {len(r3)} 条")
                        time.sleep(1)
                        st.rerun()

    elif menu == "🛡️ 审计日志":
        if role=="管理员": st.dataframe(st.session_state.audit_logs, use_container_width=True)
        else: st.error("无权")
    elif menu == "⚙️ 基础配置":
        st.data_editor(st.session_state.rooms_db, use_container_width=True)
    elif menu == "👥 账号管理":
        if role=="管理员":
            st.dataframe(st.session_state.user_db_df)
            with st.form("au"):
                u = st.text_input("用户名")
                p = st.text_input("密码")
                r = st.selectbox("角色", ["录入员","审核员","财务总监","管理员"])
                if st.form_submit_button("新增/修改"):
                    df = st.session_state.user_db_df
                    if u in df['username'].values:
                        idx = df[df['username']==u].index[0]
                        st.session_state.user_db_df.at[idx,'password'] = p
                        st.session_state.user_db_df.at[idx,'role'] = r
                    else:
                        new_u = pd.DataFrame([{"username":u, "password":p, "role":r}])
                        st.session_state.user_db_df = safe_concat([df, new_u])
                    st.success("操作成功")
                    st.rerun()
        else: st.error("无权")
    elif menu == "👤 个人中心":
        st.write(f"当前用户: {user}")
        with st.form("cp"):
            p1 = st.text_input("新密码", type="password")
            if st.form_submit_button("修改"):
                df = st.session_state.user_db_df
                idx = df[df['username']==user].index[0]
                st.session_state.user_db_df.at[idx,'password'] = p1
                st.success("修改成功")

if __name__ == "__main__":
    main()
