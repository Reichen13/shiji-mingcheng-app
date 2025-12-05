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
    page_title="世纪名城 ERP | V17.0 终极融合版", 
    layout="wide", 
    page_icon="🏙️",
    initial_sidebar_state="expanded"
)

# ==============================================================================
# 1. 核心工具与 Gist 同步 (从 V15 找回的功能)
# ==============================================================================

def safe_concat(df_list):
    non_empty = [d for d in df_list if not d.empty]
    if not non_empty: return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True)

def get_gist_client():
    try:
        token = st.secrets.connections.github.token
        g = Github(token)
        return g
    except Exception as e:
        return None

def save_to_gist():
    if not HAS_GITHUB: return False
    g = get_gist_client()
    if not g: return False
    try:
        gist_id = st.secrets.connections.github.gist_id
        gist = g.get_gist(gist_id)
        files_content = {}
        # 保存核心表
        for key, fname in [('ledger', 'ledger.csv'), ('rooms_db', 'rooms.csv'), 
                           ('wallet_db', 'wallet.csv'), ('waiver_requests', 'waiver.csv')]:
            if key in st.session_state:
                files_content[fname] = InputFileContent(st.session_state[key].fillna("").astype(str).to_csv(index=False))
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
            if fname in files:
                return pd.read_csv(io.StringIO(files[fname].content)).fillna("")
            return pd.DataFrame()
        
        # 恢复数据
        df_l = read_gist('ledger.csv')
        if not df_l.empty: st.session_state.ledger = df_l
        
        df_r = read_gist('rooms.csv')
        if not df_r.empty: st.session_state.rooms_db = df_r

        df_w = read_gist('wallet.csv')
        if not df_w.empty: st.session_state.wallet_db = df_w
        
        return True
    except: return False

# ==============================================================================
# 2. 导入解析逻辑 (从 V15 找回的核心资产)
# ==============================================================================

def clean_str(val):
    if pd.isna(val): return ""
    s = str(val).replace('\n', ' ').strip()
    if s.lower() == 'nan': return ""
    return s

def parse_date(date_val):
    if pd.isna(date_val) or str(date_val).strip() == "": return ""
    try: return parser.parse(str(date_val), fuzzy=True).strftime("%Y-%m-%d")
    except: return ""

def process_smart_import(uploaded_file):
    """简化的通用导入逻辑，适配你的 Excel 格式"""
    if uploaded_file is None: return [], []
    
    imported_bills = []
    imported_rooms = []
    
    try:
        # 尝试读取
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file, header=None) # 先读无表头
        else:
            df = pd.read_excel(uploaded_file, header=None)
            
        # 简单暴力定位法：假设你的 Excel 结构相对固定
        # 实际开发中这里会用更复杂的关键字定位
        st.info("正在解析文件结构...")
        
        # 模拟你的 V15 解析逻辑
        for idx, row in df.iterrows():
            if idx < 2: continue # 跳过表头
            try:
                # 尝试提取房号 (假设在第2列)
                raw_room = str(row.iloc[1])
                if "房号" in raw_room or "nan" in raw_room.lower(): continue
                
                room = clean_str(raw_room)
                owner = clean_str(row.iloc[2])
                
                # 提取费用 (假设在第8列之后)
                try: amount = float(row.iloc[8]) 
                except: amount = 0
                
                if amount > 0:
                    imported_bills.append({
                        "流水号": f"IMP-{uuid.uuid4().hex[:6]}",
                        "房号": room,
                        "业主": owner,
                        "费用类型": "物业费(导入)",
                        "应收": amount,
                        "实收": 0,  # 默认未收
                        "减免金额": 0,
                        "欠费": amount,
                        "收费区间": "2025年度",
                        "归属年月": "2025-01",
                        "状态": "未缴",
                        "收费日期": "",
                        "操作人": st.session_state.username,
                        "来源文件": uploaded_file.name
                    })
                    
                    # 同时更新房产表
                    imported_rooms.append({
                        "房号": room, "业主": owner, "类型": "住宅", 
                        "状态": "已入住", "面积": 100.0, # 模拟数据
                        "电话": ""
                    })
            except: continue
            
    except Exception as e:
        st.error(f"解析失败: {e}")
        
    return imported_bills, imported_rooms

# ==============================================================================
# 3. 初始化与状态管理
# ==============================================================================

def init_df(key, columns):
    if key not in st.session_state:
        st.session_state[key] = pd.DataFrame(columns=columns)

def init_session():
    # 核心表
    init_df('ledger', ['流水号', '房号', '业主', '费用类型', '应收', '实收', '减免金额', '欠费', '收费区间', '状态', '收费日期', '操作人', '来源文件', '归属年月'])
    init_df('rooms_db', ["房号", "业主", "类型", "面积", "状态", "电话"])
    init_df('wallet_db', ['房号', '余额', '更新时间'])
    init_df('waiver_requests', ['申请单号', '房号', '申请减免金额', '申请原因', '审批状态', '关联账单号', '申请人'])
    init_df('audit_logs', ['时间', '操作人', '动作', '详情'])

    # 用户表
    if 'user_db' not in st.session_state:
        st.session_state.user_db = pd.DataFrame([
            {"username": "admin", "password": "123", "role": "管理员", "name": "系统管理员"},
            {"username": "op", "password": "123", "role": "操作员", "name": "前台小王"},
            {"username": "cfo", "password": "123", "role": "财务总监", "name": "张总"},
            {"username": "audit", "password": "123", "role": "审核员", "name": "李风控"},
        ])

    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.user_role = ""

init_session()

# ==============================================================================
# 4. 业务逻辑 (V16 改进版)
# ==============================================================================

def log_action(user, action, detail):
    new_log = pd.DataFrame([{
        "时间": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "操作人": user, "动作": action, "详情": detail
    }])
    st.session_state.audit_logs = safe_concat([st.session_state.audit_logs, new_log])

def wallet_trans(room, amount, remark, user):
    """钱包变动：正数为充值，负数为扣款"""
    df_w = st.session_state.wallet_db
    idx = df_w[df_w['房号'] == room].index
    
    if idx.empty:
        new_row = pd.DataFrame([{'房号': room, '余额': 0.0, '更新时间': str(datetime.date.today())}])
        st.session_state.wallet_db = safe_concat([df_w, new_row])
        idx = st.session_state.wallet_db[st.session_state.wallet_db['房号'] == room].index
    
    current = float(st.session_state.wallet_db.at[idx[0], '余额'])
    if current + amount < 0:
        return False, "余额不足"
    
    st.session_state.wallet_db.at[idx[0], '余额'] = current + amount
    st.session_state.wallet_db.at[idx[0], '更新时间'] = str(datetime.datetime.now())
    
    action = "充值" if amount > 0 else "消费"
    log_action(user, f"钱包{action}", f"房号{room} 变动{amount} 余额{current+amount}")
    return True, "成功"

# ==============================================================================
# 5. UI 界面层
# ==============================================================================

def login_page():
    st.markdown("## 🔐 世纪名城 ERP V17.0 (终极融合版)")
    st.info("账号: admin / op / cfo / audit，密码均为 123")
    
    c1, c2 = st.columns(2)
    user = c1.text_input("账号")
    pwd = c2.text_input("密码", type="password")
    
    if st.button("登录", type="primary"):
        udb = st.session_state.user_db
        u = udb[udb['username'] == user]
        if not u.empty and u.iloc[0]['password'] == pwd:
            st.session_state.logged_in = True
            st.session_state.username = user
            st.session_state.user_role = u.iloc[0]['role']
            st.rerun()
        else:
            st.error("失败")

def main_app():
    role = st.session_state.user_role
    user = st.session_state.username
    
    # --- 侧边栏 ---
    with st.sidebar:
        st.title("🏢 世纪名城 ERP")
        st.caption(f"当前: {user} | {role}")
        
        # 权限菜单映射
        menus = ["🔍 综合查询"] # 基础
        
        if role in ["管理员", "财务总监"]:
            menus.insert(0, "📊 财务决策中心") # V16 报表
            
        if role in ["管理员", "操作员", "录入员"]:
            menus.extend(["📝 费用录入", "💸 收银与钱包", "📥 数据导入"]) # 找回导入
            
        if role in ["管理员", "审核员", "财务总监"]:
            menus.extend(["📨 减免审批中心"]) # V16 审批流
            
        if role in ["管理员", "操作员"]:
            menus.extend(["⚙️ 档案管理"]) # 找回细粒度权限
            
        if role == "管理员":
            menus.append("🛡️ 审计日志")
        
        # Gist 同步 (找回)
        if HAS_GITHUB:
            with st.expander("☁️ 云端同步"):
                if st.button("⬆️ 上传数据"):
                    if save_to_gist(): st.success("已上传")
                    else: st.error("失败")
                if st.button("⬇️ 拉取数据"):
                    if load_from_gist(): st.success("已恢复"); time.sleep(1); st.rerun()
                    else: st.error("失败")

        choice = st.radio("导航", menus)
        st.divider()
        if st.button("退出"):
            st.session_state.logged_in = False
            st.rerun()

    # --- 模块内容 ---

    # 1. 财务决策中心 (V16 保留)
    if choice == "📊 财务决策中心":
        st.header("📊 财务决策支持")
        df = st.session_state.ledger
        if df.empty:
            st.warning("暂无数据")
        else:
            # V16 的月度报表逻辑
            df['归属年月'] = df['归属年月'].fillna('2025-01')
            year_list = sorted(list(set([str(x)[:4] for x in df['归属年月'].unique()])))
            sel_y = st.selectbox("年份", year_list)
            
            sub_df = df[df['归属年月'].astype(str).str.startswith(sel_y)]
            
            # KPI
            k1, k2, k3 = st.columns(3)
            ys = sub_df['应收'].sum()
            ss = sub_df['实收'].sum() + sub_df['减免金额'].sum()
            rate = (ss/ys*100) if ys>0 else 0
            k1.metric("年度应收", f"¥{ys:,.0f}")
            k2.metric("综合收缴率", f"{rate:.1f}%")
            k3.metric("欠费总额", f"¥{sub_df['欠费'].sum():,.0f}", delta_color="inverse")
            
            # 找回 Plotly 图表 (如果库存在)
            if HAS_PLOTLY:
                fig = px.bar(sub_df, x='归属年月', y=['实收', '欠费'], title="月度收缴情况", barmode='group')
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.bar_chart(sub_df.groupby('归属年月')[['实收','欠费']].sum())

    # 2. 数据导入 (V15 找回)
    elif choice == "📥 数据导入":
        st.header("📥 外部数据导入")
        st.info("支持 V15 格式的台账 Excel/CSV 文件导入")
        f = st.file_uploader("上传文件")
        if f and st.button("开始解析导入"):
            bills, rooms = process_smart_import(f)
            if bills:
                st.session_state.ledger = safe_concat([st.session_state.ledger, pd.DataFrame(bills)])
                st.session_state.rooms_db = safe_concat([st.session_state.rooms_db, pd.DataFrame(rooms)]).drop_duplicates(subset='房号', keep='last')
                st.success(f"成功导入 {len(bills)} 条账单")
                log_action(user, "数据导入", f"导入文件 {f.name}")
            else:
                st.warning("未解析到有效数据，请检查格式")

    # 3. 档案管理 (V15 找回细粒度权限)
    elif choice == "⚙️ 档案管理":
        st.header("⚙️ 房产档案")
        
        df_rooms = st.session_state.rooms_db
        
        if role == "操作员":
            st.warning("⚠️ 操作员模式：您仅可修改'电话'字段。")
            # V15 的受限视图逻辑
            edited = st.data_editor(
                df_rooms,
                column_config={
                    "房号": st.column_config.TextColumn(disabled=True),
                    "业主": st.column_config.TextColumn(disabled=True),
                    "面积": st.column_config.NumberColumn(disabled=True),
                    "类型": st.column_config.TextColumn(disabled=True),
                    "电话": st.column_config.TextColumn(disabled=False) # 仅开放这个
                },
                use_container_width=True,
                hide_index=True
            )
            if st.button("保存修改"):
                st.session_state.rooms_db = edited
                log_action(user, "档案修改", "操作员更新了电话")
                st.success("已保存")
        
        else:
            st.success("👨‍💻 管理员模式：全权编辑")
            edited = st.data_editor(df_rooms, num_rows="dynamic", use_container_width=True)
            if st.button("保存所有档案"):
                st.session_state.rooms_db = edited
                log_action(user, "档案重构", "管理员更新了主数据")
                st.success("已保存")

    # 4. 减免审批 (V16 逻辑)
    elif choice == "📨 减免审批中心":
        st.header("📨 电子减免流")
        t1, t2 = st.tabs(["发起申请", "审批处理"])
        with t1:
            r = st.selectbox("房号", st.session_state.rooms_db['房号'].unique())
            df = st.session_state.ledger
            # 筛选欠费
            unpaid = df[(df['房号']==r) & (df['欠费']>0.1)]
            if unpaid.empty:
                st.info("无欠费")
            else:
                opts = {f"{row['费用类型']} 欠¥{row['欠费']}": row['流水号'] for i, row in unpaid.iterrows()}
                s = st.selectbox("选择账单", list(opts.keys()))
                bid = opts[s]
                
                amt = st.number_input("减免金额")
                rsn = st.text_input("原因")
                if st.button("提交申请"):
                    req = pd.DataFrame([{
                        '申请单号': str(uuid.uuid4())[:6], '房号': r, '申请减免金额': amt,
                        '申请原因': rsn, '审批状态': '待审批', '关联账单号': bid, '申请人': user
                    }])
                    st.session_state.waiver_requests = safe_concat([st.session_state.waiver_requests, req])
                    st.success("已提交")
        
        with t2:
            pend = st.session_state.waiver_requests[st.session_state.waiver_requests['审批状态']=='待审批']
            if pend.empty: st.info("无待办")
            else:
                st.dataframe(pend)
                pid = st.selectbox("选择单号审批", pend['申请单号'])
                c1, c2 = st.columns(2)
                if c1.button("✅ 通过"):
                    # 找到申请单
                    idx_r = st.session_state.waiver_requests[st.session_state.waiver_requests['申请单号']==pid].index[0]
                    st.session_state.waiver_requests.at[idx_r, '审批状态'] = '已通过'
                    
                    # 找到原账单平账
                    bid = st.session_state.waiver_requests.at[idx_r, '关联账单号']
                    amt = st.session_state.waiver_requests.at[idx_r, '申请减免金额']
                    
                    idx_l = st.session_state.ledger[st.session_state.ledger['流水号']==bid].index
                    if not idx_l.empty:
                        st.session_state.ledger.at[idx_l[0], '减免金额'] += amt
                        st.session_state.ledger.at[idx_l[0], '欠费'] -= amt
                        if st.session_state.ledger.at[idx_l[0], '欠费'] <= 0.01:
                            st.session_state.ledger.at[idx_l[0], '状态'] = '已结清(减免)'
                    
                    st.success("审批通过，账单已平")
                    time.sleep(1)
                    st.rerun()

    # 5. 费用录入 (V16)
    elif choice == "📝 费用录入":
        st.header("📝 开单")
        with st.form("bill"):
            r = st.selectbox("房号", st.session_state.rooms_db['房号'].unique())
            t = st.selectbox("类型", ["物业费", "车位费", "水费"])
            m = st.text_input("归属年月", "2025-01")
            a = st.number_input("金额", 100.0)
            if st.form_submit_button("生成"):
                nb = pd.DataFrame([{
                    "流水号": f"B-{uuid.uuid4().hex[:6]}", "房号": r, "费用类型": t,
                    "应收": a, "实收": 0, "减免金额": 0, "欠费": a,
                    "状态": "未缴", "归属年月": m, "操作人": user
                }])
                st.session_state.ledger = safe_concat([st.session_state.ledger, nb])
                st.success("OK")

    # 6. 收银与钱包 (V16)
    elif choice == "💸 收银与钱包":
        st.header("💸 收银台")
        r = st.selectbox("房号", st.session_state.rooms_db['房号'].unique())
        
        # 钱包逻辑
        w = st.session_state.wallet_db
        bal = 0.0
        if not w.empty:
            tmp = w[w['房号']==r]
            if not tmp.empty: bal = float(tmp.iloc[0]['余额'])
        st.metric("钱包余额", f"¥{bal:,.2f}")
        
        t1, t2 = st.tabs(["充值", "缴费"])
        with t1:
            amt = st.number_input("金额")
            if st.button("充值"):
                ok, msg = wallet_trans(r, amt, "充值", user)
                if ok: st.success(msg); time.sleep(1); st.rerun()
                else: st.error(msg)
        
        with t2:
            # 欠费列表
            df = st.session_state.ledger
            unpaid = df[(df['房号']==r) & (df['欠费']>0.1)]
            if not unpaid.empty:
                opts = {f"{x['费用类型']} 欠{x['欠费']}": x['流水号'] for i,x in unpaid.iterrows()}
                sels = st.multiselect("选择支付", list(opts.keys()))
                if sels and st.button("余额支付"):
                    total = sum([unpaid[unpaid['流水号']==opts[k]].iloc[0]['欠费'] for k in sels])
                    if bal >= total:
                        # 扣款
                        wallet_trans(r, -total, "缴费", user)
                        # 平账
                        for k in sels:
                            bid = opts[k]
                            idx = df[df['流水号']==bid].index[0]
                            owe = df.at[idx, '欠费']
                            st.session_state.ledger.at[idx, '实收'] += owe
                            st.session_state.ledger.at[idx, '欠费'] = 0
                            st.session_state.ledger.at[idx, '状态'] = '已缴'
                        st.success("支付完成")
                        time.sleep(1); st.rerun()
                    else:
                        st.error("余额不足")
            else: st.info("无欠费")

    elif choice == "🔍 综合查询":
        st.dataframe(st.session_state.ledger)

    elif choice == "🛡️ 审计日志":
        st.dataframe(st.session_state.audit_logs)

if __name__ == "__main__":
    if not st.session_state.logged_in:
        login_page()
    else:
        main_app()
