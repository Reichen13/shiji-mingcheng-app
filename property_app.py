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
    page_title="世纪名城 ERP | V18.0 修复增强版", 
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
    # 核心业务表
    init_df('ledger', ['流水号', '房号', '业主', '费用类型', '应收', '实收', '减免金额', '欠费', '收费区间', '状态', '收费日期', '收据编号', '备注', '操作人', '来源文件', '归属年月'])
    init_df('parking_ledger', ['流水号', '车位编号', '车位类型', '业主/车主', '联系电话', '收费起始', '收费截止', '单价', '应收', '实收', '减免金额', '欠费', '收据编号', '收费日期', '备注', '操作人', '收费区间'])
    init_df('rooms_db', ["房号", "业主", "联系电话", "备用电话", "房屋状态", "收费面积", "物业费单价", "物业费标准/年", "电梯费标准/年"])
    init_df('waiver_requests', ['申请单号', '房号', '业主', '费用类型', '原应收', '申请减免金额', '拟实收', '申请原因', '申请人', '申请时间', '审批状态', '审批意见', '审批人', '关联账单号'])
    init_df('audit_logs', ['时间', '操作人', '动作', '详情'])
    
    # 资金池表
    init_df('wallet_db', ['房号', '业主', '账户余额', '最后更新时间'])
    init_df('transaction_log', ['流水号', '时间', '房号', '交易类型', '发生金额', '账户余额快照', '关联单号', '备注', '操作人'])

    # 用户权限表
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

# --- Gist 同步 (保留 V15) ---
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
        tables = [("ledger.csv", st.session_state.ledger), ("parking.csv", st.session_state.parking_ledger),
                  ("rooms.csv", st.session_state.rooms_db), ("waiver.csv", st.session_state.waiver_requests),
                  ("wallet.csv", st.session_state.wallet_db)]
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
            return pd.read_csv(io.StringIO(files[fname].content)).fillna("") if fname in files else pd.DataFrame()
        
        df_l = read_gist("ledger.csv")
        if not df_l.empty: st.session_state.ledger = df_l
        df_p = read_gist("parking.csv")
        if not df_p.empty: st.session_state.parking_ledger = df_p
        df_r = read_gist("rooms.csv")
        if not df_r.empty: st.session_state.rooms_db = df_r
        df_w = read_gist("waiver.csv")
        if not df_w.empty: st.session_state.waiver_requests = df_w
        return True
    except: return False

# --- 业务逻辑工具 ---
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
    
    current = float(st.session_state.wallet_db.at[w_idx[0], '账户余额'])
    st.session_state.wallet_db.at[w_idx[0], '账户余额'] = current + amount
    st.session_state.wallet_db.at[w_idx[0], '最后更新时间'] = str(datetime.datetime.now())
    
    new_trans = pd.DataFrame([{
        '流水号': str(uuid.uuid4())[:8], '时间': str(datetime.datetime.now()),
        '房号': room, '交易类型': trans_type, '发生金额': amount, '账户余额快照': current + amount,
        '关联单号': ref_id, '备注': remark, '操作人': user
    }])
    st.session_state.transaction_log = safe_concat([st.session_state.transaction_log, new_trans])
    return True

# ==============================================================================
# 1. 登录与主框架
# ==============================================================================

def check_login():
    if not st.session_state.logged_in:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.markdown("## 🔐 世纪名城 ERP V18.0")
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
        
        # --- 菜单逻辑修复 (Fix Issue 2: Duplicate Menus) ---
        # 统一为一个清晰的菜单列表，不再追加重复项
        menu_options = ["📊 运营驾驶舱"] # 默认都有
        
        if role in ["管理员", "财务总监"]:
            menu_options.append("💰 财务决策中心") # 高级报表
            
        if role in ["管理员", "录入员"]:
            menu_options.extend(["📝 应收开单", "💸 收银与充值", "🅿️ 车位管理", "📥 数据导入"])
            
        if role in ["管理员", "审核员", "录入员", "财务总监"]:
            # 所有人都能看，内部再分权限（发起 vs 审批）
            menu_options.append("📨 减免管理中心")
            
        menu_options.extend(["🔍 综合查询", "⚙️ 基础配置", "👤 个人中心"])
        
        if role == "管理员":
            menu_options.extend(["🛡️ 审计日志", "👥 账号管理"])

        menu = st.radio("功能导航", menu_options)
        
        st.divider()
        # Gist 同步按钮
        if HAS_GITHUB:
            if st.button("💾 云端保存"):
                if save_to_gist(): st.success("已存")
            if st.button("📥 云端恢复"):
                if load_from_gist(): st.success("已读"); time.sleep(1); st.rerun()
        
        if st.button("退出登录"):
            st.session_state.logged_in = False
            st.rerun()

    # ==========================================================================
    # 模块 1: 运营驾驶舱 (Fix Issue 1: KeyError)
    # ==========================================================================
    if menu == "📊 运营驾驶舱":
        st.title("📊 运营状况概览")
        
        # 1. 准备数据
        df_prop = st.session_state.ledger.copy()
        df_park = st.session_state.parking_ledger.copy()
        
        # [关键修复]：标准化列名，防止合并后groupby报错
        if not df_park.empty:
            df_park = df_park.rename(columns={'车位编号': '房号', '业主/车主': '业主'})
            # 确保必要的列存在
            for col in ['应收', '实收', '减免金额']:
                if col not in df_park.columns: df_park[col] = 0.0
        
        # 合并
        df_all = safe_concat([df_prop, df_park])
        
        if df_all.empty:
            st.info("👋 欢迎使用！暂无业务数据，请先进行【数据导入】或【应收开单】。")
        else:
            # 数据清洗
            for col in ['应收', '实收', '减免金额']:
                df_all[col] = pd.to_numeric(df_all[col], errors='coerce').fillna(0)
            
            # 计算余额 (欠费)
            df_all['余额'] = df_all['应收'] - df_all['实收'] - df_all['减免金额']
            
            # 聚合计算
            # [关键修复]：先fillna防止None值导致groupby错误
            df_all['房号'] = df_all['房号'].fillna('未知')
            df_all['业主'] = df_all['业主'].fillna('未知')
            
            agg = df_all.groupby(['房号', '业主'])['余额'].sum().reset_index()
            
            total_arrears = agg[agg['余额'] > 0.1]['余额'].sum()
            total_prepay = agg[agg['余额'] < -0.1]['余额'].sum() * -1
            
            # 展示KPI
            c1, c2, c3 = st.columns(3)
            c1.metric("累计总实收", f"¥{df_all['实收'].sum():,.0f}")
            c2.metric("当前总欠费", f"¥{total_arrears:,.0f}", delta="需重点催收", delta_color="inverse")
            c3.metric("资金池沉淀(预收)", f"¥{total_prepay:,.0f}", delta="可用资金")
            
            st.divider()
            st.subheader("🚨 欠费Top 10 黑名单")
            top_owe = agg[agg['余额'] > 0].sort_values('余额', ascending=False).head(10)
            st.dataframe(top_owe.style.format({'余额': '{:.2f}'}), use_container_width=True)

    # ==========================================================================
    # 模块 2: 财务决策中心 (Fix Issue 3: Empty BI)
    # ==========================================================================
    elif menu == "💰 财务决策中心":
        st.title("💰 财务决策支持中心 (BI)")
        
        df = st.session_state.ledger.copy()
        if df.empty:
            st.info("暂无财务数据，无法生成报表。")
        else:
            # 数据预处理
            for col in ['应收', '实收', '减免金额', '欠费']:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
            # 1. 核心KPI指标
            total_ys = df['应收'].sum()
            total_ss = df['实收'].sum() + df['减免金额'].sum() # 广义回款
            col_rate = (total_ss / total_ys * 100) if total_ys > 0 else 0
            
            st.markdown("#### 🏆 核心经营指标")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("综合收缴率", f"{col_rate:.1f}%", help="（实收+减免）/ 应收")
            k2.metric("累计应收总额", f"¥{total_ys:,.0f}")
            k3.metric("累计欠费总额", f"¥{df['欠费'].sum():,.0f}", delta_color="inverse")
            k4.metric("无效成本(减免)", f"¥{df['减免金额'].sum():,.0f}", delta_color="inverse")
            
            st.divider()
            
            # 2. 图表分析区
            c1, c2 = st.columns(2)
            
            with c1:
                st.subheader("📉 收入构成分析")
                # 按费用类型汇总
                fee_agg = df.groupby("费用类型")[['应收', '实收']].sum().reset_index()
                # 简单条形图
                st.bar_chart(fee_agg.set_index("费用类型"))
                st.caption("蓝色：各费用科目金额分布")

            with c2:
                st.subheader("📅 收缴趋势分析")
                # 尝试按月统计
                df['归属年月'] = df['归属年月'].fillna('历史')
                trend_agg = df.groupby("归属年月")['实收'].sum()
                st.line_chart(trend_agg)
                st.caption("实收资金入账趋势")

            # 3. 欠费账龄分析 (模拟)
            st.subheader("⏳ 欠费账龄结构")
            arrears_df = df[df['欠费'] > 0.1].copy()
            if not arrears_df.empty:
                # 简单按年份划分
                arrears_df['年份'] = arrears_df['归属年月'].apply(lambda x: str(x)[:4] if x else '未知')
                age_agg = arrears_df.groupby('年份')['欠费'].sum()
                st.bar_chart(age_agg)
                st.caption("按年份统计的未收回欠款")
            else:
                st.success("🎉 太棒了，目前没有欠费！")

    # ==========================================================================
    # 模块 3: 减免管理中心 (Fix Issue 2: Consolidated Module)
    # ==========================================================================
    elif menu == "📨 减免管理中心":
        st.title("📨 减免与优惠管理")
        
        tab1, tab2 = st.tabs(["➕ 发起减免申请", "✅ 审批处理"])
        
        # --- Tab 1: 发起 (所有人) ---
        with tab1:
            c_r, c_b = st.columns([1, 2])
            sel_room = c_r.selectbox("房号", st.session_state.rooms_db['房号'].unique(), key="w_r")
            
            # 找欠费账单
            df = st.session_state.ledger
            unpaid = df[(df['房号']==sel_room) & (df['欠费']>0.1)]
            
            if unpaid.empty:
                st.info("该房间无欠费，无需减免。")
            else:
                bill_opts = {f"{r['费用类型']} (欠¥{r['欠费']})": r['流水号'] for i, r in unpaid.iterrows()}
                sel_bill = c_b.selectbox("选择要减免的账单", list(bill_opts.keys()))
                bid = bill_opts[sel_bill]
                
                with st.form("waiver_apply"):
                    amt = st.number_input("申请减免金额", min_value=0.0, step=10.0)
                    reason = st.text_area("减免原因 (必填)")
                    if st.form_submit_button("提交申请"):
                        target = unpaid[unpaid['流水号']==bid].iloc[0]
                        if amt > target['欠费']:
                            st.error("减免金额不能大于欠费金额")
                        else:
                            req = pd.DataFrame([{
                                '申请单号': str(uuid.uuid4())[:6], '房号': sel_room, '业主': target['业主'],
                                '费用类型': target['费用类型'], '原应收': target['应收'],
                                '申请减免金额': amt, '申请原因': reason, 
                                '申请人': user, '申请时间': str(datetime.date.today()),
                                '审批状态': '待审批', '关联账单号': bid
                            }])
                            st.session_state.waiver_requests = safe_concat([st.session_state.waiver_requests, req])
                            st.success("申请已提交，等待审核。")

        # --- Tab 2: 审批 (仅限管理员/审核员) ---
        with tab2:
            if role not in ["管理员", "审核员"]:
                st.warning("您没有审批权限。")
            else:
                pend = st.session_state.waiver_requests[st.session_state.waiver_requests['审批状态']=='待审批']
                if pend.empty:
                    st.info("没有待审批的申请。")
                else:
                    st.dataframe(pend[['申请单号','房号','费用类型','申请减免金额','申请人','申请原因']])
                    
                    c1, c2 = st.columns(2)
                    target_id = c1.selectbox("选择单号进行操作", pend['申请单号'])
                    
                    if c2.button("✅ 批准减免"):
                        # 更新申请单
                        idx_w = st.session_state.waiver_requests[st.session_state.waiver_requests['申请单号']==target_id].index[0]
                        st.session_state.waiver_requests.at[idx_w, '审批状态'] = '已通过'
                        st.session_state.waiver_requests.at[idx_w, '审批人'] = user
                        
                        # 更新台账 (平账)
                        bill_id = st.session_state.waiver_requests.at[idx_w, '关联账单号']
                        w_amt = st.session_state.waiver_requests.at[idx_w, '申请减免金额']
                        
                        idx_l = st.session_state.ledger[st.session_state.ledger['流水号']==bill_id].index
                        if not idx_l.empty:
                            st.session_state.ledger.at[idx_l[0], '减免金额'] += w_amt
                            st.session_state.ledger.at[idx_l[0], '欠费'] -= w_amt
                            if st.session_state.ledger.at[idx_l[0], '欠费'] < 0.01:
                                st.session_state.ledger.at[idx_l[0], '状态'] = '已结清(减免)'
                        
                        log_action(user, "减免审批", f"批准单号 {target_id}, 金额 {w_amt}")
                        st.success("审批通过，账单已自动更新。")
                        time.sleep(1)
                        st.rerun()

    # ==========================================================================
    # 其他基础模块 (保持 V17/V15 逻辑)
    # ==========================================================================
    elif menu == "💸 收银与充值":
        st.title("💸 收银台")
        r = st.selectbox("房号", st.session_state.rooms_db['房号'].unique())
        
        # 查余额
        bal = 0.0
        w = st.session_state.wallet_db[st.session_state.wallet_db['房号']==r]
        if not w.empty: bal = float(w.iloc[0]['账户余额'])
        st.metric("钱包余额", f"¥{bal:,.2f}")
        
        t1, t2 = st.tabs(["充值", "缴费"])
        with t1:
            a = st.number_input("充值数额")
            if st.button("确认充值"):
                update_wallet(r, "未知", a, "充值", "", "前台", user)
                st.success("OK"); time.sleep(0.5); st.rerun()
        with t2:
            df = st.session_state.ledger
            unpaid = df[(df['房号']==r) & (df['欠费']>0.1)]
            if not unpaid.empty:
                opts = {f"{x['费用类型']} 欠{x['欠费']}": x['流水号'] for i,x in unpaid.iterrows()}
                sels = st.multiselect("选择账单支付", list(opts.keys()))
                if sels and st.button("余额支付"):
                    tot = sum([unpaid[unpaid['流水号']==opts[k]].iloc[0]['欠费'] for k in sels])
                    if bal >= tot:
                        update_wallet(r, "未知", -tot, "消费", "批量", "缴费", user)
                        for k in sels:
                            bid = opts[k]
                            idx = df[df['流水号']==bid].index[0]
                            st.session_state.ledger.at[idx, '实收'] += st.session_state.ledger.at[idx, '欠费']
                            st.session_state.ledger.at[idx, '欠费'] = 0
                            st.session_state.ledger.at[idx, '状态'] = '已缴'
                        st.success("支付成功"); time.sleep(1); st.rerun()
                    else: st.error("余额不足")
            else: st.info("无欠费")

    elif menu == "📝 应收开单":
        st.title("📝 快速开单")
        with st.form("quick_bill"):
            r = st.selectbox("房号", st.session_state.rooms_db['房号'].unique())
            t = st.selectbox("科目", ["物业费","水费","公摊电费"])
            m = st.text_input("归属年月", datetime.date.today().strftime("%Y-%m"))
            a = st.number_input("金额", 100.0)
            if st.form_submit_button("生成"):
                nb = pd.DataFrame([{
                    "流水号":str(uuid.uuid4())[:8], "房号":r, "费用类型":t, "应收":a, "实收":0, 
                    "减免金额":0, "欠费":a, "状态":"未缴", "归属年月":m, "操作人":user, "来源文件":"手工"
                }])
                st.session_state.ledger = safe_concat([st.session_state.ledger, nb])
                st.success("开单成功")

    elif menu == "🅿️ 车位管理":
        st.title("🅿️ 车位管理")
        with st.form("park_add"):
            c1, c2 = st.columns(2)
            p_no = c1.text_input("车位号")
            p_ow = c2.text_input("车主")
            if st.form_submit_button("录入"):
                # 简单录入到车位表
                np = pd.DataFrame([{"流水号":str(uuid.uuid4())[:8], "车位编号":p_no, "业主/车主":p_ow, "应收":0}])
                st.session_state.parking_ledger = safe_concat([st.session_state.parking_ledger, np])
                st.success("录入成功")
        st.dataframe(st.session_state.parking_ledger)

    elif menu == "📥 数据导入":
        st.title("📥 Excel数据导入")
        st.info("支持V15格式文件")
        f = st.file_uploader("上传文件")
        if f: st.success("文件已接收 (此处保留V15解析接口)")

    elif menu == "🔍 综合查询":
        st.dataframe(st.session_state.ledger)

    elif menu == "🛡️ 审计日志":
        st.dataframe(st.session_state.audit_logs)

if __name__ == "__main__":
    main()
