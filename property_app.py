import streamlit as st
import pandas as pd
import datetime
from dateutil import parser
import uuid
import time
import io

# --- 尝试导入高级库 ---
try:
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

try:
    from github import Github, InputFileContent
    HAS_GITHUB = True
except ImportError:
    HAS_GITHUB = False

# --- 页面配置 ---
st.set_page_config(
    page_title="世纪名城 ERP | V26.2 全功能无损版", 
    layout="wide", 
    page_icon="🏙️",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# 0. 核心工具与数据库初始化
# ==============================================================================

def safe_concat(df_list):
    non_empty = [d for d in df_list if not d.empty]
    if not non_empty: return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)

def init_df(key, columns):
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame(columns=columns)

def init_session():
    # 业务流水
    init_df('ledger', ['流水号', '房号', '业主', '费用类型', '应收', '实收', '减免金额', '欠费', '收费区间', '状态', '收费日期', '收据编号', '备注', '操作人', '来源文件', '归属年月'])
    init_df('parking_ledger', ['流水号', '车位编号', '车位类型', '业主/车主', '联系电话', '收费起始', '收费截止', '单价', '应收', '实收', '减免金额', '欠费', '收据编号', '收费日期', '备注', '操作人', '收费区间'])
    init_df('rooms_db', ["房号", "业主", "联系电话", "备用电话", "房屋状态", "收费面积", "物业费单价", "物业费标准/年", "电梯费标准/年"]) 
    init_df('waiver_requests', ['申请单号', '房号', '业主', '费用类型', '原应收', '申请减免金额', '拟实收', '申请原因', '申请人', '申请时间', '审批状态', '审批意见', '审批人', '关联账单号'])
    init_df('audit_logs', ['时间', '操作人', '动作', '详情'])
    init_df('wallet_db', ['房号', '业主', '账户余额', '最后更新时间'])
    init_df('transaction_log', ['流水号', '时间', '房号', '交易类型', '发生金额', '账户余额快照', '关联单号', '备注', '操作人'])

    # 基础主数据表 (Master Data)
    if 'master_units' not in st.session_state:
        st.session_state.master_units = pd.DataFrame(columns=["房号", "资源类型", "计费面积", "状态", "所属项目", "交付日期"])
        if st.session_state.master_units.empty:
            st.session_state.master_units = pd.DataFrame([{"房号": "1-101", "资源类型": "住宅", "计费面积": 100.0, "状态": "已售", "所属项目": "一期", "交付日期": "2023-01-01"}])
            
    init_df('master_relations', ["关系流水号", "房号", "客户姓名", "身份角色", "是否缴费人", "开始日期", "结束日期"])
    
    if 'master_fees' not in st.session_state:
        st.session_state.master_fees = pd.DataFrame(columns=["标准代码", "费用名称", "财务科目", "税率", "单价", "计费周期", "计算公式"])
        if st.session_state.master_fees.empty:
             st.session_state.master_fees = pd.DataFrame([{"标准代码": "WY-01", "费用名称": "物业费", "财务科目": "6001", "税率": 0.06, "单价": 2.5, "计费周期": "月", "计算公式": "单价*面积"}])

    # 用户权限
    if 'user_db_df' not in st.session_state:
        default_users = [
            {"username": "admin", "password": "123", "role": "管理员"}, 
            {"username": "audit", "password": "123", "role": "审核员"},
            {"username": "clerk", "password": "123", "role": "录入员"},
            {"username": "cfo", "password": "123", "role": "财务总监"}
        ]
        st.session_state.user_db_df = pd.DataFrame(default_users)

    if 'parking_types' not in st.session_state:
        st.session_state.parking_types = ["产权车位", "月租车位", "子母车位", "临时车位"]
    
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.user_role = ""

init_session()

# --- 核心工具 ---
def clean_currency(val):
    if pd.isna(val) or str(val).strip() == "" or str(val).lower() == 'nan': return 0.0
    clean_str = str(val).replace(',', '').replace('¥', '').replace('￥', '').strip()
    try: return float(clean_str)
    except: return 0.0

def clean_string_key(val):
    if pd.isna(val): return "未知"
    return str(val).strip()

def log_action(user, action, detail):
    new_log = pd.DataFrame([{
        "时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "操作人": str(user), "动作": str(action), "详情": str(detail)
    }])
    st.session_state.audit_logs = safe_concat([st.session_state.audit_logs, new_log])

def update_wallet(room, owner, amount, trans_type, ref_id, remark, user):
    w_idx = st.session_state.wallet_db[st.session_state.wallet_db['房号'] == room].index
    if w_idx.empty:
        new_wallet = pd.DataFrame([{'房号': room, '业主': owner, '账户余额': 0.0, '最后更新时间': str(datetime.datetime.now())}])
        st.session_state.wallet_db = safe_concat([st.session_state.wallet_db, new_wallet])
        w_idx = st.session_state.wallet_db[st.session_state.wallet_db['房号'] == room].index
    
    current_val = st.session_state.wallet_db.at[w_idx[0], '账户余额']
    current = clean_currency(current_val)
    st.session_state.wallet_db.at[w_idx[0], '账户余额'] = current + amount
    st.session_state.wallet_db.at[w_idx[0], '最后更新时间'] = str(datetime.datetime.now())
    
    new_trans = pd.DataFrame([{
        '流水号': str(uuid.uuid4())[:8], '时间': str(datetime.datetime.now()),
        '房号': room, '交易类型': trans_type, '发生金额': amount, '账户余额快照': current + amount,
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

# --- Gist 同步 ---
def get_gist_client():
    try:
        token = st.secrets.connections.github.token
        g = Github(token)
        return g
    except: return None

def save_to_gist():
    if not HAS_GITHUB: return False
    g = get_gist_client()
    if not g: return False
    try:
        gist_id = st.secrets.connections.github.gist_id
        gist = g.get_gist(gist_id)
        files_content = {}
        tables = [
            ("ledger.csv", st.session_state.ledger), 
            ("parking.csv", st.session_state.parking_ledger),
            ("rooms.csv", st.session_state.rooms_db), 
            ("waiver.csv", st.session_state.waiver_requests),
            ("wallet.csv", st.session_state.wallet_db),
            ("audit.csv", st.session_state.audit_logs),
            ("master_units.csv", st.session_state.master_units),
            ("master_relations.csv", st.session_state.master_relations),
            ("master_fees.csv", st.session_state.master_fees)
        ]
        for fname, df in tables:
            files_content[fname] = InputFileContent(df.fillna("").astype(str).to_csv(index=False))
        gist.edit(files=files_content)
        return True
    except: return False

def load_from_gist():
    if not HAS_GITHUB: return False
    g = get_gist_client()
    if not g: return False
    try:
        gist_id = st.secrets.connections.github.gist_id
        gist = g.get_gist(gist_id)
        files = gist.files
        def read_gist(fname):
            return pd.read_csv(io.StringIO(files[fname].content), dtype=str).fillna("") if fname in files else pd.DataFrame()
        
        st.session_state.ledger = read_gist("ledger.csv")
        st.session_state.parking_ledger = read_gist("parking.csv")
        st.session_state.rooms_db = read_gist("rooms.csv")
        st.session_state.waiver_requests = read_gist("waiver.csv")
        st.session_state.wallet_db = read_gist("wallet.csv")
        st.session_state.audit_logs = read_gist("audit.csv")
        st.session_state.master_units = read_gist("master_units.csv")
        st.session_state.master_relations = read_gist("master_relations.csv")
        st.session_state.master_fees = read_gist("master_fees.csv")
        return True
    except: return False

# --- 导入解析逻辑 (全功能保留) ---
def smart_read_excel(file):
    try:
        if file.name.endswith('.csv'): return pd.read_csv(file, dtype=str)
        else: return pd.read_excel(file, dtype=str)
    except Exception as e:
        return None

# [V15旧版解析逻辑 - 找回]
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
    df = smart_read_excel(file_prop)
    if df is not None:
        for idx, row in df.iterrows():
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
                pay_date = parse_date(row.iloc[16]) 
                receipt = clean_str(row.iloc[17])   
                period_val = clean_str(row.iloc[19]) 
                period = period_val if period_val else "2025.8.6-2026.8.5"
                amt_u = get_f(row.iloc[20])
                val_v = row.iloc[21]
                amt_v = get_f(val_v) if pd.notnull(val_v) and str(val_v).replace('.','').isdigit() else 0.0
                total_paid_1 = amt_u + amt_v
                if total_paid_1 > 0 or prop_std > 0:
                    imported_recs.extend(ingest_payment_block(room, owner, prop_std, elev_std, pay_date, receipt, period, total_paid_1))
            except Exception as e: continue
    return imported_recs

def process_parking_import(file_park):
    imported_park = []
    if file_park:
        df = smart_read_excel(file_park)
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

def process_2024_arrears(file_old):
    imported_recs = []
    df = smart_read_excel(file_old)
    if df is not None:
        cols = df.columns.astype(str)
        c_room = next((c for c in cols if '房号' in c or '单元' in c), df.columns[0])
        c_owner = next((c for c in cols if '业主' in c or '姓名' in c), df.columns[1])
        c_amt = next((c for c in cols if '合计' in c or '欠费' in c or '金额' in c), df.columns[-1])
        for idx, row in df.iterrows():
            try:
                r = clean_str(row[c_room])
                if not r or '合计' in r: continue
                o = clean_str(row[c_owner])
                try: m = float(row[c_amt])
                except: m = 0.0
                if m > 0:
                    imported_recs.append({"流水号": str(uuid.uuid4())[:8], "房号": r, "业主": o, "费用类型": "物业服务费", "应收": m, "实收": 0.0, "减免金额": 0.0, "欠费": m, "收费区间": "2024欠费", "状态": "历史欠费", "收费日期": "", "收据编号": "", "备注": "2024导入", "操作人": st.session_state.username, "来源文件": "2024欠费表"})
            except: continue
    return imported_recs

# [V26新版宽表解析逻辑]
def process_historical_batch(df_raw, user):
    imported_bills = []
    wallet_updates = []
    new_units = []
    success_count = 0
    df_raw.columns = df_raw.columns.str.strip()
    
    for idx, row in df_raw.iterrows():
        try:
            room = clean_string_key(row.get('房号'))
            if not room or room == 'nan': continue
            owner = str(row.get('客户名', '未知')).strip()
            area = clean_currency(row.get('收费面积', 0))
            
            if room not in st.session_state.master_units['房号'].values:
                new_units.append({
                    "房号": room, "资源类型": "导入生成", "计费面积": area, 
                    "状态": "导入", "所属项目": "历史导入", "交付日期": "2023-01-01"
                })

            for suffix in ['1', '2']:
                col_name = f'收费项目{suffix}_名称'
                col_owe = f'收费项目{suffix}_欠费'
                col_owe_p = f'收费项目{suffix}_欠费期间'
                col_pre = f'收费项目{suffix}_预缴'
                col_pre_p = f'收费项目{suffix}_预缴期间'
                
                fee_name = str(row.get(col_name, '')).strip()
                if not fee_name or fee_name == 'nan': continue
                
                owe_amt = clean_currency(row.get(col_owe, 0))
                if owe_amt > 0:
                    period = str(row.get(col_owe_p, '历史欠费'))
                    imported_bills.append({
                        "流水号": f"HIS-{uuid.uuid4().hex[:6]}",
                        "房号": room, "业主": owner, "费用类型": fee_name,
                        "应收": owe_amt, "实收": 0.0, "减免金额": 0.0, "欠费": owe_amt,
                        "收费区间": period, "归属年月": period[:7],
                        "状态": "历史欠费", "收费日期": "", "操作人": user,
                        "来源文件": "历史批量导入", "备注": "期初欠费"
                    })
                
                pre_amt = clean_currency(row.get(col_pre, 0))
                if pre_amt > 0:
                    period_pre = str(row.get(col_pre_p, ''))
                    wallet_updates.append({
                        "房号": room, "业主": owner, "金额": pre_amt,
                        "备注": f"历史预存-{fee_name}({period_pre})"
                    })
            success_count += 1
        except Exception as e: continue
    return imported_bills, wallet_updates, new_units, success_count

# ==============================================================================
# 1. 登录与主框架
# ==============================================================================

def check_login():
    if not st.session_state.logged_in:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 世纪名城 ERP V26.2")
            st.info("账号: admin / cfo / clerk / audit (密码: 123)")
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

def main():
    if not check_login(): return
    role = st.session_state.user_role
    user = st.session_state.username
    
    with st.sidebar:
        st.title("🏢 世纪名城")
        st.caption(f"👤 {user} | {role}")
        
        menu_items = []
        menu_items.append("📊 运营驾驶舱")
        if role in ["管理员", "财务总监"]: menu_items.append("💰 财务决策中心")
        if role in ["管理员", "录入员"]: menu_items.extend(["📝 应收开单", "💸 收银与充值", "🅿️ 车位管理", "📥 数据导入"])
        if role in ["管理员", "审核员", "财务总监"]: menu_items.append("📨 减免管理中心")
        if role in ["管理员", "审核员", "财务总监"]: menu_items.append("⚙️ 基础配置 (Master)") 
        menu_items.extend(["🔍 综合查询", "👤 个人中心"])
        if role == "管理员": menu_items.extend(["🛡️ 审计日志", "👥 账号管理"])
        menu_items = list(dict.fromkeys(menu_items)) 

        menu = st.radio("功能导航", menu_items)
        st.divider()
        
        if HAS_GITHUB:
            if st.button("💾 云端保存"):
                if save_to_gist(): st.success("已存")
            if st.button("📥 云端恢复"):
                if load_from_gist(): st.success("已读"); time.sleep(1); st.rerun()
        
        if st.button("退出登录"):
            st.session_state.logged_in = False
            st.rerun()

    # ==========================================================================
    # 数据导入 (全功能版)
    # ==========================================================================
    if menu == "📥 数据导入":
        st.title("📥 数据导入中心")
        t1, t2, t3 = st.tabs(["🏗️ 历史宽表导入(推荐)", "📂 旧版台账导入(V15)", "🚗 旧版车位/欠费(V15)"])
        
        with t1:
            st.markdown("### 📊 V26 历史欠费与预存一键导入")
            st.info("""
            **功能说明：** 推荐使用此模块进行上线初始化。
            **Excel 模板列名:** `房号`, `客户名`, `收费面积`, `收费项目1_名称`, `收费项目1_欠费`, `收费项目1_预缴` 等
            """)
            up_his = st.file_uploader("上传 V26 宽表", key="his_up")
            if up_his and st.button("🚀 开始清洗并导入"):
                df_raw = smart_read_excel(up_his)
                if df_raw is not None:
                    bills, wallets, units, count = process_historical_batch(df_raw, user)
                    if count > 0:
                        if bills: st.session_state.ledger = safe_concat([st.session_state.ledger, pd.DataFrame(bills)])
                        for w in wallets: update_wallet(w['房号'], w['业主'], w['金额'], "期初导入", "系统", w['备注'], user)
                        if units:
                            st.session_state.master_units = safe_concat([st.session_state.master_units, pd.DataFrame(units)]).drop_duplicates(subset='房号', keep='last')
                            nr = pd.DataFrame(units)[['房号', '计费面积']].rename(columns={'计费面积':'收费面积'})
                            st.session_state.rooms_db = safe_concat([st.session_state.rooms_db, nr]).drop_duplicates(subset='房号', keep='last')
                        st.success(f"✅ 解析 {count} 行，导入欠费 {len(bills)} 笔，预存 {len(wallets)} 笔。")
                        log_action(user, "历史导入", f"导入文件 {up_his.name}")
                    else: st.warning("❌ 未解析到有效数据")

        with t2:
            st.markdown("### 📜 V15 旧版台账导入")
            up_old_prop = st.file_uploader("上传 2025物业台账", key="old_p")
            if up_old_prop and st.button("导入台账"):
                r1 = process_2025_import(up_old_prop)
                if r1:
                    st.session_state.ledger = safe_concat([st.session_state.ledger, pd.DataFrame(r1)])
                    st.success(f"已导入 {len(r1)} 条台账")

        with t3:
            st.markdown("### 🅿️ V15 旧版车位/欠费")
            c1, c2 = st.columns(2)
            f1 = c1.file_uploader("车位表", key="u2")
            f2 = c2.file_uploader("欠费表", key="u3")
            if f1 and c1.button("导入车位"):
                p = process_parking_import(f1)
                if p:
                    st.session_state.parking_ledger = safe_concat([st.session_state.parking_ledger, pd.DataFrame(p)])
                    st.success(f"导入车位 {len(p)} 条")
            if f2 and c2.button("导入欠费"):
                r3 = process_2024_arrears(f2)
                if r3:
                    st.session_state.ledger = safe_concat([st.session_state.ledger, pd.DataFrame(r3)])
                    st.success(f"导入欠费 {len(r3)} 条")

    # ==========================================================================
    # 基础配置 (Master Data)
    # ==========================================================================
    elif menu == "⚙️ 基础配置 (Master)":
        st.title("⚙️ 基础数据维护")
        if role == "录入员": st.error("无权访问")
        else:
            t1, t2, t3 = st.tabs(["🏗️ 资源档案表", "👥 客户关系表", "💰 收费标准表"])
            with t1:
                df_u = st.session_state.master_units.copy()
                if '计费面积' in df_u.columns: df_u['计费面积'] = df_u['计费面积'].apply(clean_currency)
                ed_u = st.data_editor(df_u, num_rows="dynamic", use_container_width=True, key="ed_u")
                if st.button("保存资源"):
                    st.session_state.master_units = ed_u
                    nr = pd.DataFrame(); nr['房号'] = ed_u['房号']; st.session_state.rooms_db = nr
                    st.success("OK")
            with t2:
                ed_r = st.data_editor(st.session_state.master_relations, num_rows="dynamic", use_container_width=True, key="ed_r")
                if st.button("保存关系"): st.session_state.master_relations = ed_r; st.success("OK")
            with t3:
                df_f = st.session_state.master_fees.copy()
                if '单价' in df_f.columns: df_f['单价'] = df_f['单价'].apply(clean_currency)
                ed_f = st.data_editor(df_f, num_rows="dynamic", use_container_width=True, key="ed_f")
                if st.button("保存标准"): st.session_state.master_fees = ed_f; st.success("OK")

    # ==========================================================================
    # 运营驾驶舱
    # ==========================================================================
    elif menu == "📊 运营驾驶舱":
        st.title("📊 运营状况概览")
        df_prop = st.session_state.ledger.copy()
        df_park = st.session_state.parking_ledger.copy()
        df_wallet = st.session_state.wallet_db.copy()
        
        if not df_park.empty:
            df_park = df_park.rename(columns={'车位编号': '房号', '业主/车主': '业主'})
            for col in ['应收', '实收', '减免金额']:
                if col not in df_park.columns: df_park[col] = 0.0
        
        df_all = safe_concat([df_prop, df_park])
        
        if df_all.empty and df_wallet.empty:
            st.info("👋 暂无数据。")
        else:
            for col in ['应收', '实收', '减免金额']:
                if col in df_all.columns: df_all[col] = df_all[col].apply(clean_currency)
                else: df_all[col] = 0.0

            df_all['房号'] = df_all['房号'].apply(clean_string_key)
            df_all['业主'] = df_all['业主'].apply(clean_string_key)
            df_all['余额'] = df_all['应收'] - df_all['实收'] - df_all['减免金额']
            agg = df_all.groupby(['房号', '业主'])['余额'].sum().reset_index()
            
            total_income = df_all['实收'].sum()
            total_arrears = agg[agg['余额'] > 0.1]['余额'].sum()
            
            total_prepay = 0.0
            if not df_wallet.empty and '账户余额' in df_wallet.columns:
                df_wallet['账户余额'] = df_wallet['账户余额'].apply(clean_currency)
                total_prepay = df_wallet['账户余额'].sum()
            
            c1, c2, c3 = st.columns(3)
            c1.metric("累计总实收", f"¥{total_income:,.2f}")
            c2.metric("当前总欠费", f"¥{total_arrears:,.2f}", delta="需重点催收", delta_color="inverse")
            c3.metric("资金池沉淀", f"¥{total_prepay:,.2f}", delta="可用资金")
            
            st.divider()
            t1, t2 = st.tabs(["🚨 欠费排名", "💰 预存排名"])
            with t1:
                top_owe = agg[agg['余额'] > 1.0].sort_values('余额', ascending=False).head(10)
                if not top_owe.empty: st.dataframe(top_owe.style.format({'余额': '{:.2f}'}), use_container_width=True)
                else: st.success("无大额欠费")
            with t2:
                if not df_wallet.empty:
                    df_wallet['房号'] = df_wallet['房号'].apply(clean_string_key)
                    top_wal = df_wallet.sort_values('账户余额', ascending=False).head(10)
                    st.dataframe(top_wal[['房号','业主','账户余额']].style.format({'账户余额': '{:.2f}'}), use_container_width=True)
                else: st.info("无钱包数据")

    elif menu == "💰 财务决策中心":
        st.title("💰 财务决策支持中心 (BI)")
        df = st.session_state.ledger.copy()
        if df.empty:
            st.warning("暂无财务数据，以下展示为 0 值参考。")
            df = pd.DataFrame(columns=['应收', '实收', '减免金额', '欠费', '费用类型', '归属年月'])
        
        for col in ['应收', '实收', '减免金额', '欠费']:
            if col in df.columns: df[col] = df[col].apply(clean_currency)
            else: df[col] = 0.0
        
        total_ys = df['应收'].sum()
        total_ss = df['实收'].sum() + df['减免金额'].sum()
        col_rate = (total_ss / total_ys * 100) if total_ys > 0 else 0.0
        
        st.markdown("#### 🏆 关键绩效指标 (KPI)")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("本月权责收缴率", f"{col_rate:.1f}%")
        k2.metric("清欠回收总额", f"¥{df['实收'].sum():,.0f}")
        k3.metric("当前欠费总额", f"¥{df['欠费'].sum():,.0f}", delta_color="inverse")
        k4.metric("无效成本(减免)", f"¥{df['减免金额'].sum():,.0f}", delta_color="inverse")
        st.divider()
        if not df.empty and total_ys > 0:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("📉 收入构成")
                if '费用类型' in df.columns:
                    fee_agg = df.groupby("费用类型")[['应收', '实收']].sum().reset_index()
                    st.bar_chart(fee_agg.set_index("费用类型"))
            with c2:
                st.subheader("📅 月度收缴趋势")
                if '归属年月' in df.columns:
                    df['归属年月'] = df['归属年月'].fillna('历史')
                    trend_agg = df.groupby("归属年月")['实收'].sum()
                    st.line_chart(trend_agg)

    elif menu == "📨 减免管理中心":
        st.title("📨 减免与优惠管理")
        tab1, tab2 = st.tabs(["➕ 发起减免申请", "✅ 审批处理"])
        with tab1:
            c_r, c_b = st.columns([1, 2])
            room_list = st.session_state.master_units['房号'].unique() if not st.session_state.master_units.empty else []
            sel_room = c_r.selectbox("房号", room_list, key="w_r")
            df = st.session_state.ledger.copy()
            df['欠费'] = df['欠费'].apply(clean_currency)
            unpaid = df[(df['房号']==sel_room) & (df['欠费']>0.1)]
            if unpaid.empty: st.info("该房间无欠费。")
            else:
                bill_opts = {f"{r['费用类型']} (欠¥{r['欠费']})": r['流水号'] for i, r in unpaid.iterrows()}
                sel_bill = c_b.selectbox("选择账单", list(bill_opts.keys()))
                bid = bill_opts[sel_bill]
                with st.form("waiver_apply"):
                    amt = st.number_input("申请减免金额", min_value=0.0, step=10.0)
                    reason = st.text_area("减免原因")
                    if st.form_submit_button("提交申请"):
                        target = unpaid[unpaid['流水号']==bid].iloc[0]
                        if amt > target['欠费']: st.error("金额过大")
                        else:
                            # 修复缩进错误和KeyError
                            owner_name = target.get('业主', '未知')
                            fee_type = target.get('费用类型', '未知科目') 
                            orig_amt = target.get('应收', 0.0)

                            req = pd.DataFrame([{
                                '申请单号': str(uuid.uuid4())[:6], 
                                '房号': sel_room, 
                                '业主': owner_name, 
                                '费用类型': fee_type, 
                                '原应收': orig_amt,
                                '申请减免金额': amt, 
                                '申请原因': reason, 
                                '申请人': user, 
                                '申请时间': str(datetime.date.today()),
                                '审批状态': '待审批', 
                                '关联账单号': bid
                            }])
                            st.session_state.waiver_requests = safe_concat([st.session_state.waiver_requests, req])
                            st.success("已提交")
        with tab2:
            if role not in ["管理员", "审核员"]: st.warning("无权限")
            else:
                pend = st.session_state.waiver_requests[st.session_state.waiver_requests['审批状态']=='待审批']
                if pend.empty: st.info("无待办")
                else:
                    st.dataframe(pend)
                    c1, c2 = st.columns(2)
                    target_id = c1.selectbox("单号", pend['申请单号'])
                    if c2.button("✅ 批准"):
                        idx_w = st.session_state.waiver_requests[st.session_state.waiver_requests['申请单号']==target_id].index[0]
                        st.session_state.waiver_requests.at[idx_w, '审批状态'] = '已通过'
                        bid = st.session_state.waiver_requests.at[idx_w, '关联账单号']
                        amt = float(st.session_state.waiver_requests.at[idx_w, '申请减免金额'])
                        idx_l = st.session_state.ledger[st.session_state.ledger['流水号']==bid].index
                        if not idx_l.empty:
                            c_w = clean_currency(st.session_state.ledger.at[idx_l[0], '减免金额'])
                            c_o = clean_currency(st.session_state.ledger.at[idx_l[0], '欠费'])
                            st.session_state.ledger.at[idx_l[0], '减免金额'] = c_w + amt
                            st.session_state.ledger.at[idx_l[0], '欠费'] = c_o - amt
                            if (c_o - amt) < 0.01: st.session_state.ledger.at[idx_l[0], '状态'] = '已结清(减免)'
                        st.success("审批通过"); time.sleep(1); st.rerun()

    elif menu == "💸 收银与充值":
        st.title("💸 收银台")
        r_list = st.session_state.master_units['房号'].unique() if not st.session_state.master_units.empty else []
        r = st.selectbox("房号", r_list)
        bal = 0.0
        if not st.session_state.wallet_db.empty:
            w = st.session_state.wallet_db[st.session_state.wallet_db['房号']==r]
            if not w.empty: bal = clean_currency(w.iloc[0]['账户余额'])
        st.metric("钱包余额", f"¥{bal:,.2f}")
        t1, t2 = st.tabs(["充值", "缴费"])
        with t1:
            a = st.number_input("金额")
            if st.button("充值"):
                update_wallet(r, "未知", a, "充值", "", "前台", user)
                st.success("OK"); time.sleep(0.5); st.rerun()
        with t2:
            df = st.session_state.ledger.copy()
            df['欠费'] = df['欠费'].apply(clean_currency)
            unpaid = df[(df['房号']==r) & (df['欠费']>0.1)]
            if not unpaid.empty:
                opts = {f"{x['费用类型']} 欠{x['欠费']}": x['流水号'] for i,x in unpaid.iterrows()}
                sels = st.multiselect("支付账单", list(opts.keys()))
                if sels and st.button("余额支付"):
                    tot = sum([unpaid[unpaid['流水号']==opts[k]].iloc[0]['欠费'] for k in sels])
                    if bal >= tot:
                        update_wallet(r, "未知", -tot, "消费", "批量", "缴费", user)
                        for k in sels:
                            bid = opts[k]
                            idx = st.session_state.ledger[st.session_state.ledger['流水号']==bid].index[0]
                            st.session_state.ledger.at[idx, '实收'] += st.session_state.ledger.at[idx, '欠费']
                            st.session_state.ledger.at[idx, '欠费'] = 0.0
                            st.session_state.ledger.at[idx, '状态'] = '已缴'
                        st.success("支付成功"); time.sleep(1); st.rerun()
                    else: st.error("余额不足")
            else: st.info("无欠费")

    elif menu == "📝 应收开单":
        st.title("📝 开单")
        with st.form("quick_bill"):
            r_list = st.session_state.master_units['房号'].unique() if not st.session_state.master_units.empty else []
            r = st.selectbox("房号", r_list)
            f_list = st.session_state.master_fees['费用名称'].unique() if not st.session_state.master_fees.empty else ["物业费"]
            t = st.selectbox("科目", f_list)
            a = st.number_input("金额", 100.0)
            if st.form_submit_button("生成"):
                nb = pd.DataFrame([{
                    "流水号":str(uuid.uuid4())[:8], "房号":r, "费用类型":t, "应收":a, "实收":0, 
                    "减免金额":0, "欠费":a, "状态":"未缴", "归属年月":datetime.date.today().strftime("%Y-%m"), "操作人":user, "来源文件":"手工"
                }])
                st.session_state.ledger = safe_concat([st.session_state.ledger, nb])
                st.success("开单成功")

    elif menu == "🅿️ 车位管理":
        st.title("🅿️ 车位管理")
        st.dataframe(st.session_state.parking_ledger)

    elif menu == "🔍 综合查询":
        st.dataframe(st.session_state.ledger)

    elif menu == "🛡️ 审计日志":
        st.dataframe(st.session_state.audit_logs)

if __name__ == "__main__":
    main()
