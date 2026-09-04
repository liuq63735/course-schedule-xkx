# -*- coding: utf-8 -*-
"""
校科协智能课表管理
技术栈：Flask + SQLite + Bootstrap 5

角色说明：
    - 第一个注册的用户自动成为管理员(admin)
    - 主席身份不需要选择部门；届数 = 注册年份
"""
import os
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, abort)

from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "schedule.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "student-org-schedule-secret-please-change")


def _register_jinja_helpers():
    """把工具函数注册成模板全局函数。"""
    for fn in (week_text, section_of, period_label, course_style):
        app.jinja_env.globals[fn.__name__] = fn

# 第几节 -> (开始时间, 结束时间)
PERIOD_TIMES = {
    1: ("08:00", "08:45"), 2: ("08:55", "09:40"), 3: ("09:55", "10:40"), 4: ("10:50", "11:35"),
    5: ("13:30", "14:15"), 6: ("14:25", "15:10"), 7: ("15:25", "16:10"), 8: ("16:20", "17:05"),
    9: ("18:30", "19:15"), 10: ("19:25", "20:10"), 11: ("20:20", "21:05"), 12: ("21:15", "22:00"),
}

IDENTITIES = ["主席", "部长", "副部长", "干事"]
DEPARTMENTS = ["办公室", "科普部", "学术部", "宣传部", "外联部", "网络部"]
WEEK_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
WEEK_TYPES = {"all": "全周", "odd": "单周", "even": "双周"}
WEEKEND_SLOTS = {"morning": "上午", "noon": "中午", "evening": "晚上"}
# 周六、周日选择的三类时段（与节次错开，只用于自定义事项）
WEEKEND_SLOT_BLOCKS = {
    "morning": (1, 4),   # 对应上午 08:00-11:35
    "noon": (5, 8),      # 对应下午/中午后 13:30-17:05
    "evening": (9, 12),  # 对应晚上 18:30-22:00
}

ROW_H = 50  # 每天表格中一个节次的高度(px)


def section_of(period):
    """返回第几节属于哪个大时段。"""
    if period <= 4:
        return "上午"
    if period <= 8:
        return "下午"
    return "晚上"


def period_label(period):
    s, e = PERIOD_TIMES.get(period, ("", ""))
    return s, e


# ---------------------------------------------------------------------------
# 数据库工具
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def query_one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def execute(sql, args=()):
    cur = get_db().execute(sql, args)
    get_db().commit()
    return cur.lastrowid


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id    TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        name          TEXT NOT NULL,
        identity      TEXT NOT NULL,               -- 主席/部长/干事
        department    TEXT,                        -- 主席可以为空
        cohort        INTEGER NOT NULL,            -- 届数，如 2026
        role          TEXT NOT NULL DEFAULT 'member',  -- admin/member
        created_at    TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS semesters (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,          -- 如 2026-2027-1
        start_date  TEXT NOT NULL,                 -- YYYY-MM-DD(周一)
        total_weeks INTEGER NOT NULL,
        is_active   INTEGER NOT NULL DEFAULT 0,
        created_at  TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS courses (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
        semester_id   INTEGER NOT NULL REFERENCES semesters(id) ON DELETE CASCADE,
        name          TEXT NOT NULL,
        day           INTEGER NOT NULL,            -- 1-7 (周一..周日)
        start_period  INTEGER,                     -- 周一~周五 用节次
        end_period    INTEGER,
        weekend_slot  TEXT,                        -- 周六/周日 用时段 morning/noon/evening
        start_week    INTEGER NOT NULL,
        end_week      INTEGER NOT NULL,
        week_type     TEXT NOT NULL DEFAULT 'all', -- all/odd/even
        created_at    TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_courses_user   ON courses(user_id);
    CREATE INDEX IF NOT EXISTS idx_courses_sem    ON courses(semester_id);
    """)
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# 认证与权限装饰器 / 常用工具
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            flash("请先登录", "warning")
            return redirect(url_for("login", next=request.full_path if request.query_string else url_for("index")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if user is None or user["role"] != "admin":
            flash("需要管理员权限", "danger")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def get_current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return query_one("SELECT * FROM users WHERE id = ?", (uid,))


def safe_next():
    nxt = request.args.get("next")
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return url_for("index")


def parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def current_week(semester, on=None):
    """根据系统日期计算当前教学周，限制在 1..总周数 之间。"""
    if not semester:
        return 1
    total = semester["total_weeks"]
    start = parse_date(semester["start_date"])
    if start is None:
        return 1
    today = on or date.today()
    if today < start:
        return 1
    week = (today - start).days // 7 + 1
    return max(1, min(total, week))


def active_semester():
    """当前学期：优先取 is_active=1，否则取最早创建的学期。"""
    s = query_one("SELECT * FROM semesters WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
    if s is None:
        s = query_one("SELECT * FROM semesters ORDER BY id LIMIT 1")
    return s


def resolve_semester(sid):
    if sid:
        return query_one("SELECT * FROM semesters WHERE id = ?", (sid,))
    return active_semester()


def course_in_week(c, week):
    """判断课程是否出现在某教学周。"""
    if not (c["start_week"] <= week <= c["end_week"]):
        return False
    if c["week_type"] == "odd":
        return week % 2 == 1
    if c["week_type"] == "even":
        return week % 2 == 0
    return True


def week_text(start, end, wtype):
    if start == end:
        base = f"第{start}周"
    else:
        base = f"第{start}-{end}周"
    if wtype == "odd":
        return base + "（单周）"
    if wtype == "even":
        return base + "（双周）"
    return base


def stable_color(key):
    """由字符串产生稳定的柔和颜色(HSL)，用于课程配色。"""
    total = sum(ord(ch) for ch in str(key)) + len(str(key)) * 13
    h = total % 360
    return f"hsl({h}, 62%, 92%)", f"hsl({h}, 60%, 34%)"


def course_style(row):
    """生成课程色块的内联样式。"""
    bg, fg = stable_color(row["name"] + "-" + str(row["id"]))
    return f"background:{bg};color:{fg};border-color:{fg};"


# 单个星期列内课程的重叠排版：返回带定位信息的列表
def layout_day(rows, row_h=ROW_H):
    """rows: 该天(节次型)有效的课程列表 [row, ...]
    返回 items，每项附带 top/height/left/width 便于绝对定位渲染。
    """
    if not rows:
        return []
    items = [dict(r) for r in rows]
    items.sort(key=lambda x: (x["start_period"], -x["end_period"]))
    # 1. 分组为若干“相互重叠”的簇(链式重叠合并)
    clusters, cur, cur_end = [], [], -1
    for it in items:
        if not cur or it["start_period"] <= cur_end:
            cur.append(it)
            cur_end = max(cur_end, it["end_period"])
        else:
            clusters.append(cur)
            cur = [it]
            cur_end = it["end_period"]
    if cur:
        clusters.append(cur)

    placed = []
    for cluster in clusters:
        lanes = []  # 每个车道当前最后结束的节次
        assignments = []
        for it in sorted(cluster, key=lambda x: (x["start_period"], -x["end_period"])):
            lane = None
            for i, lane_end in enumerate(lanes):
                if lane_end < it["start_period"]:  # 不与车道内已有课程重叠
                    lane = i
                    break
            if lane is None:
                lane = len(lanes)
                lanes.append(it["end_period"])
            else:
                lanes[lane] = max(lanes[lane], it["end_period"])
            assignments.append((it, lane))
        n = len(lanes)
        for it, lane in assignments:
            placed.append({
                "item": it,
                "top": (it["start_period"] - 1) * row_h,
                "height": (it["end_period"] - it["start_period"] + 1) * row_h - 4,
                "left": round(lane * 100.0 / n, 3),
                "width": round(100.0 / n, 3),
            })
    return placed


# ---------------------------------------------------------------------------
# 页面路由：登录注册
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("schedule"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("schedule"))
    if request.method == "POST":
        student_id = (request.form.get("student_id") or "").strip()
        password = request.form.get("password") or ""
        user = query_one("SELECT * FROM users WHERE student_id = ?", (student_id,))
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            session["role"] = user["role"]
            flash(f"欢迎回来，{user['name']}", "success")
            return redirect(safe_next())
        flash("学号或密码错误", "danger")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        student_id = (request.form.get("student_id") or "").strip()
        password = request.form.get("password") or ""
        name = (request.form.get("name") or "").strip()
        identity = request.form.get("identity") or ""
        department = (request.form.get("department") or "").strip()
        year = date.today().year

        err = None
        if not student_id:
            err = "请输入学号"
        elif query_one("SELECT id FROM users WHERE student_id = ?", (student_id,)):
            err = "该学号已被注册"
        elif not name:
            err = "请输入真实姓名"
        elif len(password) < 6:
            err = "密码长度至少为 6 位"
        elif identity not in IDENTITIES:
            err = "请选择正确的身份"
        elif identity != "主席" and department not in DEPARTMENTS:
            err = "请选择所属部门"
        if err:
            flash(err, "danger")
            return render_template("register.html", IDENTITIES=IDENTITIES,
                                   departments=DEPARTMENTS, form=request.form)

        # 主席不需要部门
        if identity == "主席":
            department = None
        # 第一个注册的用户自动成为管理员
        user_count = query_one("SELECT COUNT(*) AS n FROM users")["n"]
        role = "admin" if user_count == 0 else "member"
        execute(
            "INSERT INTO users(student_id, password_hash, name, identity, department, cohort, role, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (student_id, generate_password_hash(password), name, identity, department,
             year, role, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        if role == "admin":
            flash("注册成功！您是系统中的第一位用户，已自动设为管理员。", "success")
        else:
            flash("注册成功，请登录。", "success")
        return redirect(url_for("login"))
    return render_template("register.html", IDENTITIES=IDENTITIES,
                           departments=DEPARTMENTS, form=None, current_year=date.today().year)


@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("已退出登录", "info")
    return redirect(url_for("login"))


@app.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    user = get_current_user()
    if request.method == "POST":
        old = request.form.get("old_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not check_password_hash(user["password_hash"], old):
            flash("原密码错误", "danger")
        elif len(new) < 6:
            flash("新密码长度至少为 6 位", "danger")
        elif new != confirm:
            flash("两次输入的新密码不一致", "danger")
        else:
            execute("UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new), user["id"]))
            flash("密码修改成功", "success")
            return redirect(url_for("schedule"))
    return render_template("change_password.html")


# ---------------------------------------------------------------------------
# 课表：查看 + 管理
# ---------------------------------------------------------------------------
@app.route("/schedule")
@login_required
def schedule():
    me = get_current_user()
    semester = resolve_semester(request.args.get("semester_id", type=int))
    total_weeks = semester["total_weeks"] if semester else 0

    week = request.args.get("week", type=int)
    if semester is None:
        week = 1
    elif not week or week < 1:
        week = current_week(semester)
    elif week > total_weeks:
        week = total_weeks

    target_id = request.args.get("user", type=int)
    target = None
    if target_id:
        target = query_one("SELECT * FROM users WHERE id = ?", (target_id,))
    if target is None:
        target = me
        target_id = me["id"]

    manageable = (me["role"] == "admin") or (target_id == me["id"])

    members = query("SELECT * FROM users ORDER BY department IS NULL, department, cohort DESC, name")
    departments = DEPARTMENTS + ["无部门"]

    # 周一~周日合并成一张 12 节矩阵：
    #   - 周一~周五按实际节次排布；
    #   - 周六、周日把 上午/中午/晚上 视为 第1-4 / 第5-8 / 第9-12 节，
    #     同一行内自由填写的安排以块状显示在对应列上。
    grid_layout = {d: [] for d in range(1, 8)}
    if semester:
        rows = query(
            "SELECT * FROM courses WHERE user_id=? AND semester_id=?",
            (target_id, semester["id"]),
        )
        for r in rows:
            if not course_in_week(r, week):
                continue
            d = r["day"]
            if d <= 5 and r["start_period"]:
                grid_layout[d].append(dict(r))
            elif d >= 6 and r["weekend_slot"] in WEEKEND_SLOT_BLOCKS:
                b = WEEKEND_SLOT_BLOCKS[r["weekend_slot"]]
                it = dict(r)
                it["start_period"] = b[0]
                it["end_period"] = b[1]
                grid_layout[d].append(it)

    for d in range(1, 8):
        grid_layout[d] = layout_day(grid_layout[d])

    # 今天所在教学周是否就是当前查看周（用于高亮“今天”列）
    today_col = None
    current_week_no = 1
    week_start = week_end = None
    if semester:
        current_week_no = current_week(semester)
        wstart = parse_date(semester["start_date"])
        if wstart:
            ws = wstart + timedelta(days=(week - 1) * 7)
            week_start = ws
            week_end = ws + timedelta(days=6)
            today = date.today()
            if ws.toordinal() <= today.toordinal() <= week_end.toordinal():
                today_col = (today.isoweekday() % 7) or 7

    return render_template(
        "schedule.html",
        me=me,
        target=target,
        target_color=stable_color(target["name"] + "-" + str(target["id"]))[1],
        members=members,
        departments=departments,
        semester=semester,
        semesters=query("SELECT * FROM semesters ORDER BY id DESC"),
        week=week,
        total_weeks=total_weeks,
        grid_layout=grid_layout,
        course_style=course_style,
        manageable=manageable,
        today_col=today_col,
        current_week_no=current_week_no,
        week_start=week_start,
        week_end=week_end,
        row_h=ROW_H,
        WEEK_NAMES=WEEK_NAMES,
        WEEKEND_SLOTS=WEEKEND_SLOTS,
        period_times=PERIOD_TIMES,
    )


def _course_semester_ok(user, semester):
    """成员只能在当前学期添加课程；管理员可以在任何学期。"""
    if user["role"] == "admin":
        return True
    return bool(semester and semester["is_active"])


@app.route("/course/new", methods=["GET", "POST"])
@login_required
def course_new():
    me = get_current_user()
    semester = resolve_semester(request.args.get("semester_id", type=int))
    week = request.args.get("week", type=int) or (current_week(semester) if semester else 1)
    target_id = request.args.get("user", type=int) or me["id"]
    target = query_one("SELECT * FROM users WHERE id = ?", (target_id,))
    if target is None:
        abort(404)
    if not ((me["role"] == "admin") or target_id == me["id"]):
        flash("只能编辑自己的课表", "danger")
        return redirect(url_for("schedule", semester_id=semester["id"] if semester else None,
                                week=week, user=target_id))

    if request.method == "POST":
        sid = request.form.get("semester_id", type=int)
        sem = resolve_semester(sid)
        return _save_course(me, target, sem, None)

    all_sems = query("SELECT * FROM semesters ORDER BY id DESC")
    # 成员只允许选择当前(激活)学期
    sem_choices = all_sems if me["role"] == "admin" else [s for s in all_sems if s["is_active"]]
    if not sem_choices and me["role"] != "admin":
        flash("尚未设置当前学期，请联系管理员创建并激活学期。", "warning")
    else:
        # 成员若默认落在非激活学期上，则纠正到当前学期
        if me["role"] != "admin" and (semester is None or not semester["is_active"]):
            semester = next((s for s in sem_choices), semester)

    day_default = request.args.get("day", type=int)
    slot_default = request.args.get("slot") or "morning"
    return render_template("item_form.html", me=me, target=target,
                           semesters=sem_choices, semester=semester,
                           week=week, course=None,
                           day_default=day_default, slot_default=slot_default,
                           PERIOD_TIMES=PERIOD_TIMES, WEEKEND_SLOTS=WEEKEND_SLOTS,
                           WEEK_NAMES=WEEK_NAMES, WEEK_TYPES=WEEK_TYPES,
                           section_of=section_of, period_label=period_label,
                           IDENTITIES=IDENTITIES)


@app.route("/course/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def course_edit(cid):
    me = get_current_user()
    course = query_one("SELECT * FROM courses WHERE id = ?", (cid,))
    if course is None:
        abort(404)
    owner = query_one("SELECT * FROM users WHERE id = ?", (course["user_id"],))
    if not ((me["role"] == "admin") or course["user_id"] == me["id"]):
        flash("只能编辑自己的课表", "danger")
        return redirect(url_for("schedule"))

    week = request.args.get("week", type=int) or current_week(active_semester())
    semester = query_one("SELECT * FROM semesters WHERE id = ?", (course["semester_id"],))

    if request.method == "POST":
        return _save_course(me, owner, semester, course)

    sem_choices = [semester]
    return render_template("item_form.html", me=me, target=owner,
                           semesters=sem_choices, semester=semester,
                           week=week, course=course,
                           PERIOD_TIMES=PERIOD_TIMES, WEEKEND_SLOTS=WEEKEND_SLOTS,
                           WEEK_NAMES=WEEK_NAMES, WEEK_TYPES=WEEK_TYPES,
                           section_of=section_of, period_label=period_label,
                           IDENTITIES=IDENTITIES)


def _save_course(me, target, semester, course):
    name = (request.form.get("name") or "").strip()
    day = request.form.get("day", type=int)
    start_period = request.form.get("start_period", type=int)
    end_period = request.form.get("end_period", type=int)
    weekend_slot = request.form.get("weekend_slot")
    start_week = request.form.get("start_week", type=int)
    end_week = request.form.get("end_week", type=int)
    week_type = request.form.get("week_type", "all")
    next_url = request.form.get("next") or url_for("schedule",
                                                   semester_id=semester["id"] if semester else None,
                                                   user=target["id"])

    if semester is None:
        flash("请先创建并选择学期", "warning")
    elif not _course_semester_ok(me, semester):
        flash("成员只能为当前（激活）学期添加课程，请管理员先激活学期。", "warning")
    elif not name:
        flash("请输入课程名称 / 事项内容", "danger")
    elif day not in range(1, 8):
        flash("请选择正确的星期", "danger")
    elif not week_type or week_type not in WEEK_TYPES:
        flash("周次类型不正确", "danger")
    elif not start_week or not end_week or start_week < 1 or start_week > end_week or end_week > semester["total_weeks"]:
        flash(f"周次范围需在 1 - {semester['total_weeks']} 周之间且起止正确", "danger")
    elif day <= 5:
        # 工作日：按节次
        if not start_period or not end_period or start_period < 1 or end_period > 12 or start_period > end_period:
            flash("请正确选择开始节次与结束节次（第 1-12 节）", "danger")
        else:
            weekend_slot = None
            data = (name, day, start_period, end_period, None,
                    start_week, end_week, week_type, target["id"], semester["id"])
            _upsert_course(course, data)
            flash("已保存（工作日课程）", "success")
    else:
        # 周末：按上午/中午/晚上 + 自由填写
        if weekend_slot not in WEEKEND_SLOTS:
            flash("周六/周日请选择时段（上午/中午/晚上）", "danger")
        else:
            start_period = end_period = None
            data = (name, day, None, None, weekend_slot,
                    start_week, end_week, week_type, target["id"], semester["id"])
            _upsert_course(course, data)
            flash("已保存（周末安排）", "success")

    return redirect(next_url)


def _upsert_course(course, data):
    name, day, sp, ep, slot, sw, ew, wt, uid, sid = data
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if course is None:
        execute(
            "INSERT INTO courses(name,day,start_period,end_period,weekend_slot,start_week,end_week,week_type,user_id,semester_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (name, day, sp, ep, slot, sw, ew, wt, uid, sid, now),
        )
    else:
        execute(
            "UPDATE courses SET name=?, day=?, start_period=?, end_period=?, weekend_slot=?,"
            " start_week=?, end_week=?, week_type=? WHERE id=?",
            (name, day, sp, ep, slot, sw, ew, wt, course["id"]),
        )


@app.route("/course/<int:cid>/delete", methods=["POST"])
@login_required
def course_delete(cid):
    me = get_current_user()
    course = query_one("SELECT * FROM courses WHERE id = ?", (cid,))
    if course is None:
        abort(404)
    if not ((me["role"] == "admin") or course["user_id"] == me["id"]):
        flash("只能删除自己课表中的内容", "danger")
        return redirect(url_for("schedule"))
    execute("DELETE FROM courses WHERE id = ?", (cid,))
    flash("已删除", "success")
    semester_id = request.args.get("semester_id", type=int)
    week = request.args.get("week", type=int)
    user = request.args.get("user", type=int)
    return redirect(url_for("schedule", semester_id=semester_id, week=week, user=user))


# ---------------------------------------------------------------------------
# 管理员后台
# ---------------------------------------------------------------------------
@app.route("/admin")
@app.route("/admin/")
@admin_required
def admin_index():
    user_count = query_one("SELECT COUNT(*) AS n FROM users")["n"]
    sem_count = query_one("SELECT COUNT(*) AS n FROM semesters")["n"]
    course_count = query_one("SELECT COUNT(*) AS n FROM courses")["n"]
    admin_count = query_one("SELECT COUNT(*) AS n FROM users WHERE role='admin'")["n"]
    act = query_one("SELECT * FROM semesters WHERE is_active=1 ORDER BY id DESC LIMIT 1")
    return render_template("admin/dashboard.html",
                           user_count=user_count, sem_count=sem_count,
                           course_count=course_count, admin_count=admin_count,
                           active_semester=act)


# ---- 用户管理 ----
@app.route("/admin/users")
@admin_required
def admin_users():
    users = query(
        "SELECT u.*, (SELECT COUNT(*) FROM courses c WHERE c.user_id = u.id) AS course_cnt "
        "FROM users u ORDER BY u.department IS NULL, u.department, u.cohort DESC, u.name")
    return render_template("admin/users.html", users=users, DEPARTMENTS=DEPARTMENTS)


@app.route("/admin/users/<int:uid>/toggle_admin", methods=["POST"])
@admin_required
def admin_toggle(uid):
    user = query_one("SELECT * FROM users WHERE id = ?", (uid,))
    if user is None:
        abort(404)
    if user["role"] == "admin":
        # 防止系统内没有管理员
        n_admin = query_one("SELECT COUNT(*) AS n FROM users WHERE role='admin'")["n"]
        if n_admin <= 1:
            flash("系统至少需要保留一名管理员", "danger")
            return redirect(url_for("admin_users"))
        execute("UPDATE users SET role='member' WHERE id=?", (uid,))
        flash(f"已将 {user['name']} 降为普通成员", "info")
    else:
        execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
        flash(f"已将 {user['name']} 设为管理员", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def admin_user_edit(uid):
    user = query_one("SELECT * FROM users WHERE id = ?", (uid,))
    if user is None:
        abort(404)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        identity = request.form.get("identity") or ""
        department = (request.form.get("department") or "").strip()
        cohort = request.form.get("cohort", type=int)
        err = None
        if not name:
            err = "请输入真实姓名"
        elif identity not in IDENTITIES:
            err = "身份不正确"
        elif identity != "主席" and department not in DEPARTMENTS:
            err = "请选择部门"
        elif not cohort or cohort < 2000 or cohort > date.today().year + 1:
            err = "届数不正确（应约为注册/入学年份）"
        if err:
            flash(err, "danger")
        else:
            if identity == "主席":
                department = None
            execute("UPDATE users SET name=?, identity=?, department=?, cohort=? WHERE id=?",
                    (name, identity, department, cohort, uid))
            flash("用户信息已更新", "success")
            return redirect(url_for("admin_users"))
    return render_template("admin/user_edit.html", user=user,
                           IDENTITIES=IDENTITIES, DEPARTMENTS=DEPARTMENTS)


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@admin_required
def admin_user_delete(uid):
    user = query_one("SELECT * FROM users WHERE id = ?", (uid,))
    if user is None:
        abort(404)
    if user["role"] == "admin":
        n_admin = query_one("SELECT COUNT(*) AS n FROM users WHERE role='admin'")["n"]
        if n_admin <= 1:
            flash("不能删除唯一的管理员", "danger")
            return redirect(url_for("admin_users"))
    execute("DELETE FROM users WHERE id=?", (uid,))
    flash(f"已删除用户 {user['name']} 及其课表", "info")
    return redirect(url_for("admin_users"))


# ---- 学期管理 ----
@app.route("/admin/semesters")
@admin_required
def admin_semesters():
    semesters = query("SELECT s.*, (SELECT COUNT(*) FROM courses c WHERE c.semester_id=s.id) AS cnt "
                      "FROM semesters s ORDER BY s.id DESC")
    return render_template("admin/semesters.html", semesters=semesters)


@app.route("/admin/semesters/add", methods=["POST"])
@admin_required
def admin_semester_add():
    name = (request.form.get("name") or "").strip()
    start_date = request.form.get("start_date") or ""
    total_weeks = request.form.get("total_weeks", type=int)
    d = parse_date(start_date)
    err = None
    if not name:
        err = "请输入学期名称，如 2026-2027-1"
    elif query_one("SELECT id FROM semesters WHERE name=?", (name,)):
        err = "学期名称已存在"
    elif not d:
        err = "开始日期格式不正确（应为 YYYY-MM-DD，且为周一）"
    elif not total_weeks or total_weeks < 1 or total_weeks > 30:
        err = "总周数应在 1 - 30 之间"
    if err:
        flash(err, "danger")
    else:
        has_active = query_one("SELECT COUNT(*) AS n FROM semesters WHERE is_active=1")["n"]
        is_active = 1 if has_active == 0 else 0
        execute("INSERT INTO semesters(name,start_date,total_weeks,is_active,created_at) VALUES (?,?,?,?,?)",
                (name, start_date, total_weeks, is_active,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        flash(f"学期「{name}」创建成功" + ("，并已设为当前学期。" if is_active else ""), "success")
    return redirect(url_for("admin_semesters"))


@app.route("/admin/semesters/<int:sid>/edit", methods=["GET", "POST"])
@admin_required
def admin_semester_edit(sid):
    semester = query_one("SELECT * FROM semesters WHERE id = ?", (sid,))
    if semester is None:
        abort(404)
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        start_date = request.form.get("start_date") or ""
        total_weeks = request.form.get("total_weeks", type=int)
        d = parse_date(start_date)
        dup = query_one("SELECT id FROM semesters WHERE name=? AND id<>?", (name, sid))
        if not name:
            flash("请输入学期名称", "danger")
        elif dup:
            flash("学期名称已存在", "danger")
        elif not d:
            flash("开始日期格式不正确", "danger")
        elif not total_weeks or total_weeks < 1 or total_weeks > 30:
            flash("总周数应在 1 - 30 之间", "danger")
        else:
            execute("UPDATE semesters SET name=?, start_date=?, total_weeks=? WHERE id=?",
                    (name, start_date, total_weeks, sid))
            flash("学期信息已更新", "success")
            return redirect(url_for("admin_semesters"))
    return render_template("admin/semester_edit.html", semester=semester)


@app.route("/admin/semesters/<int:sid>/activate", methods=["POST"])
@admin_required
def admin_semester_activate(sid):
    execute("UPDATE semesters SET is_active=0")
    execute("UPDATE semesters SET is_active=1 WHERE id=?", (sid,))
    flash("已将学期设为当前学期", "success")
    return redirect(url_for("admin_semesters"))


@app.route("/admin/semesters/<int:sid>/delete", methods=["POST"])
@admin_required
def admin_semester_delete(sid):
    semester = query_one("SELECT * FROM semesters WHERE id = ?", (sid,))
    if semester is None:
        abort(404)
    if semester["is_active"]:
        flash("当前学期不能删除，请先激活其他学期", "danger")
        return redirect(url_for("admin_semesters"))
    execute("DELETE FROM semesters WHERE id=?", (sid,))
    flash(f"已删除学期「{semester['name']}」及其全部课程记录", "info")
    return redirect(url_for("admin_semesters"))


# ---- 空闲查询（管理员）----
@app.route("/admin/free", methods=["GET"])
@admin_required
def admin_free():
    semester = resolve_semester(request.args.get("semester_id", type=int))
    week = request.args.get("week", type=int) or current_week(semester)
    day = request.args.get("day", type=int) or 1
    dep_filter = request.args.get("department", "all")
    cohort_filter = request.args.get("cohort", type=int) or 0

    result = None
    query_meta = None
    if semester:
        if week < 1:
            week = 1
        week = min(week, semester["total_weeks"])

        # 构建成员基础过滤 SQL
        where = "1=1"
        args = []
        if dep_filter != "all":
            if dep_filter == "无部门":
                where += " AND department IS NULL"
            else:
                where += " AND department=?"
                args.append(dep_filter)
        if cohort_filter:
            where += " AND cohort=?"
            args.append(cohort_filter)
        users = query(f"SELECT * FROM users WHERE {where} ORDER BY department IS NULL, department, cohort DESC, name", args)

        busy = set()
        if day <= 5:
            s_period = request.args.get("start_period", type=int) or 1
            e_period = request.args.get("end_period", type=int) or s_period
            s_period = max(1, min(12, s_period))
            e_period = max(s_period, min(12, e_period))
            for u in users:
                rows = query("SELECT * FROM courses WHERE user_id=? AND semester_id=? AND day=? AND start_period IS NOT NULL",
                             (u["id"], semester["id"], day))
                for c in rows:
                    if (course_in_week(c, week)
                            and c["start_period"] <= e_period and c["end_period"] >= s_period):
                        busy.add(u["id"])
                        break
            query_meta = f"{WEEK_NAMES[day]} · 第{s_period}节 - 第{e_period}节" + (
                f"（{PERIOD_TIMES[s_period][0]}-{PERIOD_TIMES[e_period][1]}）" if s_period == e_period else "")
            time_args = dict(start_period=s_period, end_period=e_period)
        else:
            slot = request.args.get("slot", "morning")
            if slot not in WEEKEND_SLOTS:
                slot = "morning"
            for u in users:
                rows = query("SELECT * FROM courses WHERE user_id=? AND semester_id=? AND day=? AND weekend_slot=?",
                             (u["id"], semester["id"], day, slot))
                for c in rows:
                    if course_in_week(c, week):
                        busy.add(u["id"])
                        break
            query_meta = f"{WEEK_NAMES[day]} · {WEEKEND_SLOTS[slot]}"
            time_args = dict(slot=slot)

        free_users = [u for u in users if u["id"] not in busy]
        result = dict(free=free_users, busy=busy, total=len(users))
    else:
        time_args = {}

    semesters = query("SELECT * FROM semesters ORDER BY id DESC")
    all_cohorts = [r["cohort"] for r in query("SELECT DISTINCT cohort FROM users ORDER BY cohort DESC")]
    return render_template(
        "admin/free.html",
        semesters=semesters, semester=semester, week=week,
        day=day, dep_filter=dep_filter, cohort_filter=cohort_filter,
        result=result, query_meta=query_meta,
        departments=DEPARTMENTS + ["无部门"],
        all_cohorts=all_cohorts,
        WEEK_NAMES=WEEK_NAMES, WEEKEND_SLOTS=WEEKEND_SLOTS,
        PERIOD_TIMES=PERIOD_TIMES,
        time_args=time_args,
    )


_register_jinja_helpers()


if __name__ == "__main__":
    init_db()
    # 局域网可用 host=0.0.0.0；如需局域网访问请保留，否则建议 127.0.0.1
    app.run(host="127.0.0.1", port=5000, debug=True)
