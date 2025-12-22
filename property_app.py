import streamlit as st
import pandas as pd
import datetime
from dateutil import parser
import uuid
import time
import sqlite3
import hashlib
import hmac
import os
from decimal import Decimal, ROUND_HALF_UP

# --- 尝试导入可视化库 ---
try:
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

# ==============================================================================
# 0. 系统配置
# ==============================================================================
st.set_page_config(
    page_title="世纪名城 ERP | V32.0 终极完整版", 
    layout="wide", 
    page_icon="🏙️",
    initial_sidebar_state="expanded"
)

# [Security] 生产环境请修改密钥
SECRET_KEY = "CenturyCity_V32_Ultimate_Secret_!@#"
DB_FILE = "property_core.db"

# ==============================================================================
# 1. 数据库层 (Database Layer)
# ==============================================================================

def get_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    
    # --- 核心业务表 ---
    c.execute('''CREATE TABLE IF NOT EXISTS ledger (
        uuid TEXT PRIMARY KEY,
        room_id TEXT,
        owner TEXT,
        fee_type TEXT,
        receivable TEXT,
        received TEXT,
        waived TEXT,
        arrears TEXT,
        period TEXT,
        status TEXT,
        charge_date TEXT,
        receipt_no TEXT,
        remark TEXT,
        operator TEXT,
        source TEXT,
        month_group TEXT,
        invoice_status TEXT DEFAULT '未开票'
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS wallet (
        room_id TEXT PRIMARY KEY,
        owner TEXT,
        balance TEXT,
        last_updated TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS trans_log (
        trans_id TEXT PRIMARY KEY,
        trans_time TEXT,
        room_id TEXT,
        trans_type TEXT,
        amount TEXT,
        balance_snapshot TEXT,
        ref_id TEXT,
        remark TEXT,
        operator TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        log_time TEXT,
        operator TEXT,
        action TEXT,
        detail TEXT
    )''')

    # --- 基础档案表 ---
    c.execute('''CREATE TABLE IF NOT EXISTS master_units (
        room_id TEXT PRIMARY KEY,
        type TEXT,
        area TEXT,
        status TEXT,
        project TEXT,
        delivery_date TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS master_fees (
        fee_code TEXT PRIMARY KEY,
        fee_name TEXT,
        price TEXT,
        cycle TEXT,
        formula TEXT,
        late_fee_rate TEXT
    )''')

    # --- [New in V32] 车位管理表 ---
    c.execute('''CREATE TABLE IF NOT EXISTS parking (
        spot_id TEXT PRIMARY KEY,
        type TEXT,
        status TEXT,
        owner_name TEXT,
        plate_num TEXT,
        rent_price TEXT,
        start_date TEXT,
        end_date TEXT
    )''')

    # --- [New in V32] 减免审批表 ---
    c.execute('''CREATE TABLE IF NOT EXISTS waivers (
        req_id TEXT PRIMARY KEY,
        room_id TEXT,
        owner TEXT,
        fee_type TEXT,
        orig_arrears TEXT,
        waive_amount TEXT,
        reason TEXT,
        applicant TEXT,
        apply_time TEXT,
        status TEXT,
        approver TEXT,
        ref_bill_id TEXT
    )''')

    # --- 用户表 ---
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT,
        role TEXT
    )''')

    # [Seed Data]
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        h = "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3" # 123
        c.executemany("INSERT INTO users VALUES (?,?,?)", [
            ('admin', h, '管理员'), ('cfo', h, '财务总监'), 
            ('clerk', h, '录入员'), ('audit', h, '审核员')
        ])
    
    c.execute("SELECT count(*) FROM master_fees")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO master_fees VALUES (?,?,?,?,?,?)", 
                  ('WY-01', '物业费', '2.50', '月', '单价*面积', '0.003'))

    conn.commit()
    conn.close()

# --- 工具函数 ---
def to_decimal(val):
    if val is None or str(val).lower() == 'nan': return Decimal('0.00')
    try:
        clean = str(val).replace(',', '').replace('¥', '').strip()
        if clean == '': return Decimal('0.00')
        return Decimal(clean).quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)
    except: return Decimal('0.00')

def clean_str(val):
    return str(val).strip() if pd.notnull(val) else ""

def hash_password(password):
    return hashlib.sha256(str(password).encode()).hexdigest()

def db_log(user, action, detail):
    conn = get_connection()
    conn.execute("INSERT INTO audit_logs (log_time, operator, action, detail) VALUES (?,?,?,?)",
                 (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user, action, detail))
    conn.commit()
    conn.close()

def smart_read_excel(file):
    try:
        if file.name.endswith('.csv'): return pd.read_csv(file, dtype=str)
        else: return pd.read_excel(file, dtype=str)
    except: return None

# ==============================================================================
# 2. 核心业务逻辑封装
# ==============================================================================

def process_waiver_approval(req_id, approver_name):
    """
    [V32 New] 减免审批核心逻辑 (原子性操作)
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute("BEGIN TRANSACTION")
        
        # 1. 获取申请单详情
        cursor.execute("SELECT ref_bill_id, waive_amount, room_id, status FROM waivers WHERE req_id=?", (req_id,))
        req = cursor.fetchone()
        if not req: raise Exception("申请单不存在")
        if req[3] != '待审批': raise Exception("该单据状态不是待审批")
        
        bill_uuid = req[0]
        waive_amt = to_decimal(req[1])
        room_id = req[2]
        
        # 2. 获取原账单详情
        cursor.execute("SELECT arrears, waived FROM ledger WHERE uuid=?", (bill_uuid,))
        bill = cursor.fetchone()
        if not bill: raise Exception("关联账单已不存在")
        
        curr_arrears = to_decimal(bill[0])
        curr_waived = to_decimal(bill[1])
        
        if waive_amt > curr_arrears:
            raise Exception("减免金额大于当前欠费金额")
            
        # 3. 更新账单 (增加减免额，减少欠费额)
        new_waived = curr_waived + waive_amt
        new_arrears = curr_arrears - waive_amt
        new_status = "已结清(减免)" if new_arrears < Decimal('0.01') else "部分欠费"
        
        cursor.execute("UPDATE ledger SET waived=?, arrears=?, status=? WHERE uuid=?", 
                       (str(new_waived), str(new_arrears), new_status, bill_uuid))
                       
        # 4. 更新申请单状态
        cursor.execute("UPDATE waivers SET status='已通过', approver=? WHERE req_id=?", (approver_name, req_id))
        
        conn.commit()
        return True, "审批通过，账单已自动核销"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

def process_import_sql(df_raw, user):
    conn = get_connection()
    cursor = conn.cursor()
    count_bills = 0; count_wallet = 0; new_units = 0
    df_raw.columns = df_raw.columns.str.strip()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn.execute("BEGIN TRANSACTION")
        for idx, row in df_raw.iterrows():
            room = clean_str(row.get('房号'))
            if not room or room == 'nan': continue
            
            cursor.execute("SELECT room_id FROM master_units WHERE room_id = ?", (room,))
            if not cursor.fetchone():
                area = str(to_decimal(row.get('收费面积', 0)))
                cursor.execute("INSERT INTO master_units VALUES (?,?,?,?,?,?)",
                               (room, "导入生成", area, "已售", "一期", "2023-01-01"))
                new_units += 1

            for suffix in ['1', '2']:
                col_name = f'收费项目{suffix}_名称'; col_owe = f'收费项目{suffix}_欠费'
                col_pre = f'收费项目{suffix}_预缴'; col_owe_p = f'收费项目{suffix}_欠费期间'
                fee_name = clean_str(row.get(col_name))
                if not fee_name: continue
                
                owe_amt = to_decimal(row.get(col_owe, 0))
                if owe_amt > 0:
                    period = clean_str(row.get(col_owe_p, '历史导入'))
                    uid = f"IMP-{uuid.uuid4().hex[:8]}"
                    cursor.execute('''INSERT INTO ledger (uuid, room_id, owner, fee_type, receivable, received, waived, arrears, period, status, charge_date, operator, source)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (uid, room, clean_str(row.get('客户名','未知')), fee_name, str(owe_amt), "0.00", "0.00", str(owe_amt), period, "历史欠费", now_str, user, "Excel导入"))
                    count_bills += 1
                
                pre_amt = to_decimal(row.get(col_pre, 0))
                if pre_amt > 0:
                    cursor.execute("SELECT balance FROM wallet WHERE room_id = ?", (room,))
                    r_wal = cursor.fetchone()
                    curr = to_decimal(r_wal[0]) if r_wal else Decimal(0)
                    new_bal = curr + pre_amt
                    cursor.execute("INSERT OR REPLACE INTO wallet (room_id, owner, balance, last_updated) VALUES (?,?,?,?)",
                                   (room, clean_str(row.get('客户名','未知')), str(new_bal), now_str))
                    cursor.execute("INSERT INTO trans_log VALUES (?,?,?,?,?,?,?,?,?)",
                                   (f"TR-{uuid.uuid4().hex[:6]}", now_str, room, "导入预存", str(pre_amt), str(new_bal), "IMPORT", f"{fee_name}结转", user))
                    count_wallet += 1
        conn.commit()
        return True, f"导入成功: 新增档案 {new_units} 户, 欠费 {count_bills} 笔, 预存 {count_wallet} 笔"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

def save_master_data(table_name, df_edited, pk_col):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        conn.execute("BEGIN TRANSACTION")
        for idx, row in df_edited.iterrows():
            placeholders = ', '.join(['?'] * len(row))
            cols = ', '.join(df_edited.columns)
            sql = f"INSERT OR REPLACE INTO {table_name} ({cols}) VALUES ({placeholders})"
            cursor.execute(sql, tuple(row.astype(str).values))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        return False
    finally:
        conn.close()

def process_payment_transaction(room, pay_list, pay_mode, total_pay_amt, user):
    conn = get_connection()
    cursor = conn.cursor()
    try:
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_decimal = to_decimal(total_pay_amt)
        if pay_mode == "余额支付":
            cursor.execute("SELECT balance FROM wallet WHERE room_id = ?", (room,))
            row = cursor.fetchone()
            curr_bal = to_decimal(row[0]) if row else Decimal('0.00')
            if curr_bal < total_decimal: raise Exception("余额不足")
            new_bal = curr_bal - total_decimal
            cursor.execute("INSERT OR REPLACE INTO wallet (room_id, owner, balance, last_updated) VALUES (?, ?, ?, ?)", (room, "未知", str(new_bal), now_str))
            cursor.execute("INSERT INTO trans_log VALUES (?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4())[:8], now_str, room, "消费", str(total_decimal), str(new_bal), "BATCH", "缴费", user))

        for item in pay_list:
            deduct = to_decimal(item['deduct'])
            cursor.execute("SELECT receivable, received, arrears FROM ledger WHERE uuid = ?", (item['uuid'],))
            bill_row = cursor.fetchone()
            if not bill_row: continue
            new_received = to_decimal(bill_row[1]) + deduct
            new_arrears = to_decimal(bill_row[2]) - deduct
            status = "已缴" if new_arrears < Decimal('0.01') else "部分欠费"
            cursor.execute("UPDATE ledger SET received=?, arrears=?, status=? WHERE uuid=?", (str(new_received), str(new_arrears), status, item['uuid']))
        conn.commit()
        return True, "支付成功"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

def verify_access(room, token):
    if not room or not token: return False
    expected = hmac.new(SECRET_KEY.encode(), str(room).encode(), hashlib.sha256).hexdigest()[:16]
    return hmac.compare_digest(expected, token)

def get_signed_url(base_url, room):
    sign = hmac.new(SECRET_KEY.encode(), str(room).encode(), hashlib.sha256).hexdigest()[:16]
    return f"{base_url}/?mode=guest&room={room}&token={sign}"

def check_login(username, password):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT password_hash, role FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row and hash_password(password) == row[0]: return True, row[1]
    return False, None

def guest_view_sql(room):
    st.markdown(f"### 🏠 房号：{room} - 实时账单")
    conn = get_connection()
    df = pd.read_sql("SELECT period, fee_type, arrears, status, remark FROM ledger WHERE room_id = ?", conn, params=(room,))
    conn.close()
    if not df.empty:
        df['arrears'] = df['arrears'].apply(to_decimal)
        unpaid = df[df['arrears'] > Decimal('0.01')]
        if not unpaid.empty:
            st.dataframe(unpaid.style.format({'arrears': '{:.2f}'}), use_container_width=True)
            st.metric("合计应付", f"¥{unpaid['arrears'].sum():,.2f}")
        else: st.success("🎉 无待缴账单")
    else: st.info("暂无数据")

# ==============================================================================
# 3. 主程序
# ==============================================================================

def main():
    init_db()
    
    try: qp = st.query_params
    except: qp = st.experimental_get_query_params()
    if qp.get("mode") == "guest":
        gr = qp.get("room") if not isinstance(qp.get("room"), list) else qp.get("room")[0]
        gt = qp.get("token") if not isinstance(qp.get("token"), list) else qp.get("token")[0]
        if verify_access(gr, gt): guest_view_sql(gr)
        else: st.error("🛑 链接失效")
        return

    if 'logged_in' not in st.session_state: st.session_state.logged_in = False
    if not st.session_state.logged_in:
        c1, c2, c3 = st.columns([1,2,1])
        with c2:
            st.title("🔐 V32 终极完整版")
            st.info("默认账号: admin / 123 (含CFO/Audit角色)")
            u = st.text_input("账号"); p = st.text_input("密码", type="password")
            if st.button("登录", use_container_width=True):
                ok, role = check_login(u.lower().strip(), p.strip())
                if ok:
                    st.session_state.logged_in = True
                    st.session_state.username = u
                    st.session_state.role = role
                    st.rerun()
                else: st.error("失败")
        return

    user = st.session_state.username
    role = st.session_state.role
    
    with st.sidebar:
        st.title("🏢 世纪名城")
        st.caption(f"👤 {user} | {role}")
        
        # [V32] 完整导航菜单
        nav = st.radio("导航", [
            "📊 运营驾驶舱", 
            "💰 财务决策中心", # [New]
            "📝 应收开单", 
            "💸 收银台", 
            "🅿️ 车位管理",     # [New]
            "📨 减免审批",     # [New]
            "⚙️ 基础配置",  
            "📥 数据导入",  
            "🛡️ 审计日志"
        ])
        
        st.divider()
        with st.expander("🔗 访客链接"):
            qr = st.text_input("房号", "1-101")
            if st.button("生成"):
                st.code(get_signed_url("http://localhost:8501", qr), language='text')

        if st.button("退出"):
            st.session_state.logged_in = False
            st.rerun()

    # --- 模块实现 ---
    
    if nav == "📊 运营驾驶舱":
        st.title("📊 实时运营看板")
        conn = get_connection()
        df_led = pd.read_sql("SELECT room_id, arrears, received FROM ledger", conn)
        df_wal = pd.read_sql("SELECT balance FROM wallet", conn)
        conn.close()
        
        df_led['arrears'] = df_led['arrears'].apply(to_decimal)
        df_led['received'] = df_led['received'].apply(to_decimal)
        total_inc = df_led['received'].sum()
        total_arr = df_led[df_led['arrears'] > 0]['arrears'].sum()
        df_wal['balance'] = df_wal['balance'].apply(to_decimal)
        total_pool = df_wal['balance'].sum()
        
        c1, c2, c3 = st.columns(3)
        c1.metric("累计实收", f"¥{total_inc:,.2f}")
        c2.metric("当前欠费", f"¥{total_arr:,.2f}", delta_color="inverse")
        c3.metric("资金池", f"¥{total_pool:,.2f}")

    # [V32 New Module] 财务决策中心
    elif nav == "💰 财务决策中心":
        st.title("💰 财务决策支持中心 (BI)")
        if not HAS_PLOTLY:
            st.warning("请先安装 plotly 库: `pip install plotly` 以查看图表。")
        else:
            conn = get_connection()
            # 1. 收入构成分析
            df_fee = pd.read_sql("SELECT fee_type, SUM(received) as total FROM ledger GROUP BY fee_type", conn)
            df_fee['total'] = df_fee['total'].apply(float) # Plotly needs float
            
            # 2. 月度收费趋势
            df_trend = pd.read_sql("SELECT period, SUM(received) as total FROM ledger GROUP BY period ORDER BY period", conn)
            df_trend['total'] = df_trend['total'].apply(float)
            conn.close()
            
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("📉 收入构成 (按费项)")
                if not df_fee.empty:
                    fig_pie = px.pie(df_fee, values='total', names='fee_type', hole=0.4)
                    st.plotly_chart(fig_pie, use_container_width=True)
                else: st.info("无数据")
            
            with c2:
                st.subheader("📅 月度收费趋势")
                if not df_trend.empty:
                    fig_line = px.line(df_trend, x='period', y='total', markers=True)
                    st.plotly_chart(fig_line, use_container_width=True)
                else: st.info("无数据")
                
            st.info("💡 提示：图表数据基于 SQL 实时聚合，无需手动刷新。")

    # [V32 New Module] 车位管理
    elif nav == "🅿️ 车位管理":
        st.title("🅿️ 车位资源管理")
        t1, t2 = st.tabs(["🚗 车位列表", "➕ 新增/登记"])
        
        conn = get_connection()
        with t1:
            df_park = pd.read_sql("SELECT * FROM parking", conn)
            st.dataframe(df_park, use_container_width=True)
        
        with t2:
            with st.form("add_spot"):
                c1, c2 = st.columns(2)
                spot_id = c1.text_input("车位编号 (如 B1-001)")
                p_type = c2.selectbox("类型", ["产权", "人防", "临时"])
                status = c1.selectbox("状态", ["空置", "已租", "自用"])
                owner = c2.text_input("车主/租户姓名")
                plate = c1.text_input("车牌号")
                price = c2.text_input("租金/管理费标准", "0.00")
                if st.form_submit_button("保存车位信息"):
                    try:
                        conn.execute("INSERT OR REPLACE INTO parking (spot_id, type, status, owner_name, plate_num, rent_price) VALUES (?,?,?,?,?,?)",
                                     (spot_id, p_type, status, owner, plate, price))
                        conn.commit()
                        st.success("车位保存成功")
                        db_log(user, "车位管理", f"更新车位 {spot_id}")
                    except Exception as e: st.error(str(e))
        conn.close()

    # [V32 New Module] 减免审批
    elif nav == "📨 减免审批":
        st.title("📨 减免与优惠管理")
        t1, t2 = st.tabs(["➕ 发起申请", "✅ 审批处理"])
        
        conn = get_connection()
        with t1:
            st.subheader("发起减免申请")
            q_room = st.text_input("输入房号查找欠费", "1-101")
            if q_room:
                df_owe = pd.read_sql("SELECT uuid, fee_type, period, arrears FROM ledger WHERE room_id=? AND arrears > 0", conn, params=(q_room,))
                if not df_owe.empty:
                    opts = {f"[{r['period']}] {r['fee_type']} 欠¥{r['arrears']}": r['uuid'] for i,r in df_owe.iterrows()}
                    sel_bill_label = st.selectbox("选择要减免的账单", list(opts.keys()))
                    sel_bill_id = opts[sel_bill_label]
                    
                    with st.form("waiver_req"):
                        w_amt = st.number_input("申请减免金额", min_value=0.01)
                        w_reason = st.text_area("申请原因")
                        if st.form_submit_button("提交申请"):
                            try:
                                # 获取原欠费校验
                                cur_owe = float(sel_bill_label.split('¥')[1])
                                if w_amt > cur_owe: st.error("减免金额不能大于欠费金额")
                                else:
                                    req_id = f"W-{uuid.uuid4().hex[:6]}"
                                    conn.execute("INSERT INTO waivers (req_id, room_id, fee_type, orig_arrears, waive_amount, reason, applicant, apply_time, status, ref_bill_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                                 (req_id, q_room, "账单减免", str(cur_owe), str(w_amt), w_reason, user, str(datetime.date.today()), "待审批", sel_bill_id))
                                    conn.commit()
                                    st.success("申请已提交，等待审核")
                            except Exception as e: st.error(str(e))
                else: st.info("该房间无欠费")
        
        with t2:
            st.subheader("待审批列表")
            if role not in ["管理员", "审核员", "财务总监"]:
                st.error("您没有审批权限")
            else:
                df_wait = pd.read_sql("SELECT * FROM waivers WHERE status='待审批'", conn)
                if not df_wait.empty:
                    st.dataframe(df_wait)
                    c1, c2 = st.columns(2)
                    target_req = c1.selectbox("选择申请单号", df_wait['req_id'].unique())
                    if c2.button("✅ 批准并核销"):
                        ok, msg = process_waiver_approval(target_req, user)
                        if ok: 
                            st.success(msg)
                            db_log(user, "审批通过", f"单号 {target_req}")
                            time.sleep(1); st.rerun()
                        else: st.error(msg)
                else:
                    st.info("目前没有待审批的申请")
        conn.close()

    elif nav == "⚙️ 基础配置":
        st.title("⚙️ 基础档案配置 (Master Data)")
        t1, t2 = st.tabs(["🏠 房间档案", "💰 收费标准"])
        conn = get_connection()
        with t1:
            st.caption("直接修改下方表格，点击保存同步至数据库。")
            df_units = pd.read_sql("SELECT * FROM master_units", conn)
            edited_units = st.data_editor(df_units, num_rows="dynamic", use_container_width=True, key="ed_u")
            if st.button("💾 保存房间档案"):
                if save_master_data("master_units", edited_units, "room_id"):
                    st.success("保存成功！"); time.sleep(1); st.rerun()
                else: st.error("保存失败")
        with t2:
            st.caption("定义费项、单价及公式。")
            df_fees = pd.read_sql("SELECT * FROM master_fees", conn)
            edited_fees = st.data_editor(df_fees, num_rows="dynamic", use_container_width=True, key="ed_f")
            if st.button("💾 保存收费标准"):
                if save_master_data("master_fees", edited_fees, "fee_code"):
                    st.success("保存成功！"); time.sleep(1); st.rerun()
                else: st.error("保存失败")
        conn.close()

    elif nav == "📥 数据导入":
        st.title("📥 历史数据导入")
        st.info("支持 V26 格式宽表导入：包含 `房号`, `收费项目1_名称`, `收费项目1_欠费` 等列。")
        f = st.file_uploader("上传 Excel 文件", type=['xlsx', 'xls', 'csv'])
        if f:
            if st.button("🚀 开始清洗并导入数据库"):
                df_raw = smart_read_excel(f)
                if df_raw is not None:
                    ok, msg = process_import_sql(df_raw, user)
                    if ok: st.success(msg); db_log(user, "数据导入", f"文件: {f.name}")
                    else: st.error(f"导入失败: {msg}")
                else: st.error("文件读取失败")

    elif nav == "📝 应收开单":
        st.title("📝 单户开单")
        conn = get_connection()
        fees = pd.read_sql("SELECT fee_name FROM master_fees", conn)['fee_name'].tolist()
        if not fees: fees = ["物业费", "水费"]
        with st.form("bill"):
            c1, c2 = st.columns(2)
            rm = c1.text_input("房号", "1-101")
            ft = c2.selectbox("费用类型", fees)
            amt = st.number_input("金额", min_value=0.01)
            pd_val = st.date_input("归属月份", datetime.date.today()).strftime("%Y-%m")
            if st.form_submit_button("提交"):
                uid = str(uuid.uuid4())[:8]
                try:
                    conn.execute("INSERT INTO ledger (uuid, room_id, fee_type, receivable, received, arrears, period, status, charge_date, operator) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (uid, rm, ft, str(amt), "0.00", str(amt), pd_val, "未缴", str(datetime.date.today()), user))
                    conn.commit()
                    st.success("开单成功"); db_log(user, "开单", f"{rm} {ft} {amt}")
                except Exception as e: st.error(e)
        conn.close()

    elif nav == "💸 收银台":
        st.title("💸 智能收银")
        q_r = st.text_input("查询房号", "1-101")
        if q_r:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT balance FROM wallet WHERE room_id=?", (q_r,))
            row = cur.fetchone()
            bal = to_decimal(row[0]) if row else Decimal(0)
            st.metric("钱包余额", f"¥{bal:,.2f}")
            
            t1, t2 = st.tabs(["充值", "缴费"])
            with t1:
                v = st.number_input("充值额", 0.0)
                if st.button("确认充值"):
                    cursor = conn.cursor()
                    n_b = bal + to_decimal(v)
                    cursor.execute("INSERT OR REPLACE INTO wallet (room_id, balance, last_updated) VALUES (?,?,?)", 
                                   (q_r, str(n_b), datetime.datetime.now().strftime("%Y-%m-%d")))
                    cursor.execute("INSERT INTO trans_log (trans_id, room_id, trans_type, amount, operator) VALUES (?,?,?,?,?)",
                                   (uuid.uuid4().hex[:8], q_r, "充值", str(v), user))
                    conn.commit()
                    st.success("OK"); time.sleep(1); st.rerun()
            with t2:
                df = pd.read_sql("SELECT * FROM ledger WHERE room_id=? AND status!='已缴'", conn, params=(q_r,))
                if not df.empty:
                    df['arrears'] = df['arrears'].apply(to_decimal)
                    unp = df[df['arrears']>0]
                    opts = {f"[{r['period']}] {r['fee_type']} ¥{r['arrears']}": {'id':r['uuid'], 'val':r['arrears']} for i,r in unp.iterrows()}
                    sels = st.multiselect("选择账单", list(opts.keys()), default=list(opts.keys()))
                    if sels:
                        tot = sum([opts[k]['val'] for k in sels])
                        st.info(f"选定总额: ¥{tot:,.2f}")
                        pay = st.number_input("实付", 0.0, float(tot), float(tot))
                        mode = st.radio("方式", ["余额支付", "现金"])
                        if st.button("支付"):
                            queue = []
                            rem = to_decimal(pay)
                            for k in sels:
                                if rem <= 0: break
                                u_id = opts[k]['id']; u_val = opts[k]['val']
                                d = min(rem, u_val)
                                queue.append({'uuid': u_id, 'deduct': d})
                                rem -= d
                            ok, m = process_payment_transaction(q_r, queue, mode, pay, user)
                            if ok: st.success("成功"); time.sleep(1); st.rerun()
                            else: st.error(m)
                else: st.info("无欠费")
            conn.close()

    elif nav == "🛡️ 审计日志":
        st.title("🛡️ 操作日志")
        conn = get_connection()
        st.dataframe(pd.read_sql("SELECT * FROM audit_logs ORDER BY log_id DESC LIMIT 50", conn), use_container_width=True)
        conn.close()

if __name__ == "__main__":
    main()
