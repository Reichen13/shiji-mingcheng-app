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
    page_title="世纪名城 ERP | V24.0 基础导入增强版", 
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
    init_df('rooms_db', ["房号", "业主", "联系电话", "备用电话", "房屋状态", "收费面积", "物业费单价", "物业费标准/年", "电梯费标准/年"]) # 兼容旧层
    init_df('waiver_requests', ['申请单号', '房号', '业主', '费用类型', '原应收', '申请减免金额', '拟实收', '申请原因', '申请人', '申请时间', '审批状态', '审批意见', '审批人', '关联账单号'])
    init_df('audit_logs', ['时间', '操作人', '动作', '详情'])
    init_df('wallet_db', ['房号', '业主', '账户余额', '最后更新时间'])
    init_df('transaction_log', ['流水号', '时间', '房号', '交易类型', '发生金额', '账户余额快照', '关联单号', '备注', '操作人'])

    # --- 基础主数据表 (Master Data) ---
    if 'master_units' not in st.session_state:
        st.session_state.master_units = pd.DataFrame(columns=[
            "Unit_ID", "Unit_Type", "Chargeable_Area", "Status", "Project_ID", "Delivery_Date"
        ])
        if st.session_state.master_units.empty:
            st.session_state.master_units = pd.DataFrame([
                {"Unit_ID": "1-101", "Unit_Type": "住宅", "Chargeable_Area": 100.0, "Status": "已售", "Project_ID": "一期", "Delivery_Date": "2023-01-01"},
            ])
            
    init_df('master_relations', ["Relation_ID", "Unit_ID", "Customer_ID", "Role", "Is_Current_Payer", "Start_Date", "End_Date"])
    
    if 'master_fees' not in st.session_state:
        st.session_state.master_fees = pd.DataFrame(columns=[
            "Standard_ID", "Fee_Name", "Subject_Code", "Tax_Rate", "Price", "Billing_Cycle", "Formula_Type"
        ])
        if st.session_state.master_fees.empty:
             st.session_state.master_fees = pd.DataFrame([
                {"Standard_ID": "F01", "Fee_Name": "物业费", "Subject_Code": "6001", "Tax_Rate": 0.06, "Price": 2.5, "Billing_Cycle": "月", "Formula_Type": "单价*面积"},
            ])

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
        
        df_mu = read_gist("master_units.csv")
        if not df_mu.empty: st.session_state.master_units = df_mu
        
        df_mr = read_gist("master_relations.csv")
        if not df_mr.empty: st.session_state.master_relations = df_mr
        
        df_mf = read_gist("master_fees.csv")
        if not df_mf.empty: st.session_state.master_fees = df_mf

        return True
    except: return False

# --- 导入解析逻辑 ---
def smart_read_excel(file):
    try:
        if file.name.endswith('.csv'): return pd.read_csv(file, dtype=str)
        else: return pd.read_excel(file, dtype=str)
    except Exception as e:
        st.error(f"读取失败: {e}")
        return None

# ==============================================================================
# 1. 登录与主框架
# ==============================================================================

def check_login():
    if not st.session_state.logged_in:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 世纪名城 ERP V24.0")
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
        if role in ["管理员", "审核员", "财务总监"]: menu_items.append("⚙️ 基础配置 (Master)") # Master Data
        menu_items.extend(["🔍 综合查询", "👤 个人中心"])
        if role == "管理员": menu_items.extend(["🛡️ 审计日志", "👥 账号管理"])
        menu_items = list(dict.fromkeys(menu_items)) # 去重

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
    # V24.0 增强模块: 基础配置 (含批量导入)
    # ==========================================================================
    if menu == "⚙️ 基础配置 (Master)":
        st.title("⚙️ 基础数据维护 (Master Data)")
        
        if role == "录入员":
            st.error("⛔ 权限不足")
        else:
            st.info(f"✅ 当前操作身份：{role}。此处维护系统核心档案。")
            t1, t2, t3, t4 = st.tabs(["🏗️ 资源档案表", "👥 客户关系表", "💰 收费标准表", "📥 批量导入助手"])
            
            # --- Tab 1: 资源档案 ---
            with t1:
                df_units = st.session_state.master_units.copy()
                if 'Delivery_Date' in df_units.columns: df_units['Delivery_Date'] = pd.to_datetime(df_units['Delivery_Date'], errors='coerce')
                if 'Chargeable_Area' in df_units.columns: df_units['Chargeable_Area'] = df_units['Chargeable_Area'].apply(clean_currency)

                edited_units = st.data_editor(
                    df_units,
                    num_rows="dynamic",
                    use_container_width=True,
                    column_config={
                        "Unit_ID": st.column_config.TextColumn("资源ID (房号)", required=True),
                        "Unit_Type": st.column_config.SelectboxColumn("类型", options=["住宅", "商铺", "车位"], required=True),
                        "Chargeable_Area": st.column_config.NumberColumn("面积", min_value=0.0, format="%.2f"),
                        "Status": st.column_config.SelectboxColumn("状态", options=["已售", "未售", "空置"]),
                        "Delivery_Date": st.column_config.DateColumn("交付日期")
                    },
                    key="editor_units_fix"
                )
                if st.button("💾 保存资源"):
                    st.session_state.master_units = edited_units
                    # 兼容层同步
                    nr = pd.DataFrame()
                    nr['房号'] = edited_units['Unit_ID']
                    if not st.session_state.rooms_db.empty:
                        nr = pd.concat([st.session_state.rooms_db, nr]).drop_duplicates(subset='房号', keep='last')
                    st.session_state.rooms_db = nr
                    log_action(user, "更新主数据", "资源表更新")
                    st.success("保存成功")

            # --- Tab 2: 客户关系 ---
            with t2:
                df_rel = st.session_state.master_relations.copy()
                if 'Start_Date' in df_rel.columns: df_rel['Start_Date'] = pd.to_datetime(df_rel['Start_Date'], errors='coerce')
                
                edited_rel = st.data_editor(
                    df_rel,
                    num_rows="dynamic",
                    use_container_width=True,
                    column_config={
                        "Unit_ID": st.column_config.TextColumn("房号", required=True),
                        "Start_Date": st.column_config.DateColumn("开始日期"),
                        "Is_Current_Payer": st.column_config.CheckboxColumn("当前缴费人?")
                    },
                    key="editor_rel_fix"
                )
                if st.button("💾 保存关系"):
                    st.session_state.master_relations = edited_rel
                    st.success("保存成功")

            # --- Tab 3: 收费标准 ---
            with t3:
                df_fees = st.session_state.master_fees.copy()
                if 'Price' in df_fees.columns: df_fees['Price'] = df_fees['Price'].apply(clean_currency)
                
                edited_fees = st.data_editor(
                    df_fees,
                    num_rows="dynamic",
                    use_container_width=True,
                    column_config={
                        "Price": st.column_config.NumberColumn("单价", format="%.4f"),
                        "Standard_ID": st.column_config.TextColumn("标准代码", required=True)
                    },
                    key="editor_fees_fix"
                )
                if st.button("💾 保存标准"):
                    st.session_state.master_fees = edited_fees
                    st.success("保存成功")

            # --- Tab 4: [V24新增] 批量导入助手 ---
            with t4:
                st.markdown("### 📥 基础数据批量导入")
                st.info("请上传 Excel/CSV 文件。系统将自动追加数据到对应表格。")
                
                target_table = st.selectbox("选择要导入的目标表", ["资源档案表 (master_units)", "客户关系表 (master_relations)", "收费标准表 (master_fees)"])
                
                # 提示所需列名
                if "资源" in target_table:
                    req_cols = list(st.session_state.master_units.columns)
                elif "客户" in target_table:
                    req_cols = list(st.session_state.master_relations.columns)
                else:
                    req_cols = list(st.session_state.master_fees.columns)
                
                st.caption(f"💡 文件需包含以下列头 (顺序不限): {', '.join(req_cols)}")
                
                uploaded_master = st.file_uploader("上传基础数据文件", key="master_up")
                
                if uploaded_master and st.button("开始导入基础数据"):
                    df_new = smart_read_excel(uploaded_master)
                    if df_new is not None:
                        # 简单的列名匹配检查
                        missing = [c for c in req_cols if c not in df_new.columns]
                        if len(missing) > 0:
                            st.error(f"❌ 导入失败：文件中缺少列 {missing}")
                        else:
                            # 根据选择追加到不同的表
                            if "资源" in target_table:
                                st.session_state.master_units = safe_concat([st.session_state.master_units, df_new[req_cols]])
                                # 兼容层同步
                                nr = pd.DataFrame()
                                nr['房号'] = df_new['Unit_ID']
                                st.session_state.rooms_db = safe_concat([st.session_state.rooms_db, nr]).drop_duplicates(subset='房号', keep='last')
                                
                            elif "客户" in target_table:
                                st.session_state.master_relations = safe_concat([st.session_state.master_relations, df_new[req_cols]])
                            else:
                                st.session_state.master_fees = safe_concat([st.session_state.master_fees, df_new[req_cols]])
                            
                            log_action(user, "批量导入", f"导入了 {len(df_new)} 条记录到 {target_table}")
                            st.success(f"✅ 成功导入 {len(df_new)} 条数据！")
                            time.sleep(1)
                            st.rerun()

    # ==========================================================================
    # 其他模块 (保持 V23 稳定版逻辑)
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
            st.info("👋 暂无业务数据。")
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
            sel_room = c_r.selectbox("房号", st.session_state.rooms_db['房号'].unique(), key="w_r")
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
                            req = pd.DataFrame([{
                                '申请单号': str(uuid.uuid4())[:6], '房号': sel_room, '业主': target['业主'],
                                '费用类型': target['费用类型'], '原应收': target['应收'],
                                '申请减免金额': amt, '申请原因': reason, 
                                '申请人': user, '申请时间': str(datetime.date.today()),
                                '审批状态': '待审批', '关联账单号': bid
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
        r = st.selectbox("房号", st.session_state.rooms_db['房号'].unique())
        bal = 0.0
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
            r = st.selectbox("房号", st.session_state.rooms_db['房号'].unique())
            t = st.selectbox("科目", ["物业费","水费"])
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

    elif menu == "📥 数据导入":
        st.title("📥 历史流水导入")
        st.info("此处用于导入历史收费台账（交易流水）。如需导入基础档案，请前往【基础配置】模块。")
        f = st.file_uploader("上传交易流水")
        if f: st.success("文件已接收 (V15接口保留)")

    elif menu == "🔍 综合查询":
        st.dataframe(st.session_state.ledger)

    elif menu == "🛡️ 审计日志":
        st.dataframe(st.session_state.audit_logs)

if __name__ == "__main__":
    main()
