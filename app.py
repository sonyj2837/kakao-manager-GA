import math
import re
from datetime import datetime, date, time, timedelta
import pandas as pd
import streamlit as st
import kakao_database as db

# ==========================================
# 1. 페이지 설정 & DB 초기화
# ==========================================
st.set_page_config(
    page_title="오픈채팅 부방 통합 관리 사이트",
    page_icon="🛡️",
    layout="wide",
)

db.init_db()

WEEKDAY_NAMES = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
REVIEW_STATUSES = ["정상유지", "재심요청", "조치해제(감경)", "내풀대상", "무효(점수제외)"]


def init_extra_tables():
    conn = db.get_conn()
    cur = conn.cursor()
    # 경고/강퇴 테이블
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_name TEXT NOT NULL,
            action_date TEXT NOT NULL,
            action_time TEXT NOT NULL,
            action_dt TEXT NOT NULL,
            cutoff_date TEXT NOT NULL,
            room TEXT NOT NULL,
            action_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            is_valid_score INTEGER DEFAULT 1,
            review_status TEXT DEFAULT '정상유지',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE TABLE IF NOT EXISTS mod_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_score_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_cutoff TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            member_id INTEGER,
            valid_count INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            UNIQUE(week_cutoff, staff_name)
        )
        """
    )
    for k, v in {"cutoff_weekday": "2", "cutoff_hour": "22", "cutoff_minute": "0"}.items():
        cur.execute("INSERT OR IGNORE INTO mod_settings (key, value) VALUES (?, ?)", (k, v))

    # 보이스룸 테이블
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_name TEXT NOT NULL,
            room TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            end_time TEXT NOT NULL,
            end_dt TEXT NOT NULL,
            cutoff_date TEXT NOT NULL,
            duration_str TEXT NOT NULL,
            duration_seconds INTEGER NOT NULL,
            is_valid_score INTEGER DEFAULT 1,
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute("CREATE TABLE IF NOT EXISTS voice_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_score_sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_cutoff TEXT NOT NULL,
            staff_name TEXT NOT NULL,
            member_id INTEGER,
            total_seconds INTEGER NOT NULL,
            score_delta INTEGER NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            UNIQUE(week_cutoff, staff_name)
        )
        """
    )
    for k, v in {"cutoff_weekday": "2", "cutoff_hour": "22", "cutoff_minute": "0"}.items():
        cur.execute("INSERT OR IGNORE INTO voice_settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


init_extra_tables()

# ==========================================
# 2. 통합 로그인/회원가입 게이트
# ==========================================
current_user = db.render_auth_gate("unified_app", require_manager=False)
is_mgr = db.is_manager_grade(current_user)


# ==========================================
# 3. 공통 날짜/마감/파서 유틸리티
# ==========================================
def get_tbl_settings(table_name: str) -> dict:
    conn = db.get_conn()
    rows = conn.execute(f"SELECT key, value FROM {table_name}").fetchall()
    conn.close()
    s = {r["key"]: r["value"] for r in rows}
    return {
        "cutoff_weekday": int(s.get("cutoff_weekday", 2)),
        "cutoff_hour": int(s.get("cutoff_hour", 22)),
        "cutoff_minute": int(s.get("cutoff_minute", 0)),
    }


def save_tbl_setting(table_name: str, key: str, value: str):
    conn = db.get_conn()
    conn.execute(
        f"INSERT INTO {table_name} (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_cutoff_date_str(dt: datetime, cutoff_weekday: int, cutoff_hour: int, cutoff_minute: int) -> str:
    weekday = dt.weekday()
    cutoff_t = time(cutoff_hour, cutoff_minute, 0)
    if weekday == cutoff_weekday:
        target_date = dt.date() if dt.time() <= cutoff_t else dt.date() + timedelta(days=7)
    else:
        days_ahead = (cutoff_weekday - weekday) % 7
        target_date = dt.date() + timedelta(days=days_ahead)
    return target_date.strftime("%Y-%m-%d")


def recalculate_cutoffs(record_table: str, dt_col: str, cw: int, ch: int, cm: int):
    conn = db.get_conn()
    rows = conn.execute(f"SELECT id, {dt_col} FROM {record_table}").fetchall()
    for r in rows:
        dt_obj = datetime.strptime(r[dt_col], "%Y-%m-%d %H:%M")
        new_c = get_cutoff_date_str(dt_obj, cw, ch, cm)
        conn.execute(f"UPDATE {record_table} SET cutoff_date = ? WHERE id = ?", (new_c, r["id"]))
    conn.commit()
    conn.close()


def parse_date_str(raw_date: str) -> date:
    digits = re.sub(r"[^0-9]", "", raw_date.strip())
    if len(digits) == 6:
        return date(2000 + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    elif len(digits) == 8:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    raise ValueError(f"날짜 형식 오류: {raw_date}")


def parse_time_str(raw_time: str) -> time:
    m = re.match(r"^\s*(\d{1,2})\s*:\s*(\d{2})\s*$", raw_time)
    if not m:
        raise ValueError(f"시간 형식 오류: {raw_time}")
    return time(int(m.group(1)), int(m.group(2)))


# ==========================================
# 4. 사이드바 통합 네비게이션
# ==========================================
with st.sidebar:
    st.subheader("📌 메뉴 이동")
    nav_items = [
        "🏆 순위 · 내 정보 · 휴가 · 상점",
        "🛡️ 경고/강퇴 관리",
        "🎙️ 보이스룸(보룸) 관리",
    ]
    if is_mgr:
        nav_items.append("📊 관리자 통합 대시보드 (MS/MP)")

    page = st.radio("메뉴 선택", nav_items, label_visibility="collapsed")


# ==========================================
# PAGE 1: 순위 · 내 정보 · 휴가 · 상점
# ==========================================
if page == "🏆 순위 · 내 정보 · 휴가 · 상점":
    st.title("🏆 오픈채팅 부방 등급 · 순위표 및 휴가/상점")
    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 전체 순위 및 등급표",
        "📋 시즌/주차별 상세 지급 내역",
        "🏖️ 정기휴가 기준표 & 신청",
        "🎁 포인트 상점",
    ])

    with tab1:
        st.subheader("🔥 실시간 부방 랭킹")
        grade_info = " | ".join([f"**{g}**: {t}P 이상" for t, g in db.GRADE_THRESHOLDS])
        st.info(f"🎖️ **승급 기준:** {grade_info} *(MP, MS는 대표 스태프 임명 전용)*\n\n💡 **월말 결산:** 매월 말 포인트 리셋, 60%(소수점 내림) 휴가 이월")
        members = db.get_members()
        if members:
            st.dataframe(
                [{
                    "순위": f"{idx}위",
                    "등급 & 닉네임": f"[{m.get('grade', 'M1')}] {m['nickname']}",
                    "직책": m["role"],
                    "포인트": f"{m['points']}P",
                    "잔여휴가": f"{m['vacation_days']}일",
                    "상태": m["status"],
                } for idx, m in enumerate(members, 1)],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("등록된 부방이 없습니다.")

    with tab2:
        st.subheader("📋 개인별 · 시즌별 상세 지급 내역 조회")
        members = db.get_members()
        if members:
            nick_options = [m["nickname"] for m in members]
            def_idx = nick_options.index(current_user["nickname"]) if current_user["nickname"] in nick_options else 0
            selected_nick = st.selectbox("조회할 부방 닉네임", nick_options, index=def_idx)
            target = next((m for m in members if m["nickname"] == selected_nick), None)
            if target:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("현재 등급", target.get("grade", "M1"))
                c2.metric("보유 포인트", f"{target['points']}P")
                c3.metric("잔여 휴가", f"{target['vacation_days']}일")
                c4.metric("직책", target["role"])
                st.divider()
                user_logs = [l for l in db.get_logs(100) if l["nickname"] == selected_nick]
                if user_logs:
                    st.dataframe(
                        [{
                            "시간": l["created_at"],
                            "시즌": l.get("season", "-"),
                            "주차": l.get("week", "-"),
                            "구분": l["log_type"],
                            "항목": l["category"],
                            "포인트변동": f"{l['points_delta']:+d}P",
                            "휴가변동": f"{l['vacation_delta']:+d}일",
                            "메모": l["memo"],
                        } for l in user_logs],
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("기록된 지급 내역이 없습니다.")

    with tab3:
        st.subheader("🏖️ 정기휴가 기준표 안내 및 신청")
        with st.expander("📋 정기휴가 기준표 보기", expanded=False):
            st.markdown("""
            * **7일:** 중간/기말고사 (가정통신문 인증)
            * **5일:** 모의고사 (학년 인증)
            * **3일:** 연휴 (추석/설날)
            * **2일:** 공휴일 (월 1회) / 교내·사내행사 1박 이상 (월 1회)
            * **1일:** 생일 / 가족행사 (월 1회) / 교내·사내행사 당일치기 (월 2회)
            * **관리자재량:** 부상 (월 2회 협의) / 가족사 (협의)
            """)
        members = db.get_members(only_active=True)
        if members:
            m_opts = {f"[{m.get('grade', 'M1')}] {m['nickname']}": m["id"] for m in members}
            def_label = next((k for k in m_opts if current_user["nickname"] in k), list(m_opts.keys())[0])
            sel_m_label = st.selectbox("신청자 본인 선택", list(m_opts.keys()), index=list(m_opts.keys()).index(def_label))
            sel_m_id = m_opts[sel_m_label]
            sel_cat = st.selectbox("휴가 항목 선택", list(db.VACATION_RULES.keys()))
            rule = db.VACATION_RULES[sel_cat]
            st.info(rule["guide"])
            req_days = st.number_input("협의 요청 일수", min_value=1, max_value=30, value=1) if "관리자재량" in sel_cat else rule["days"]
            if "관리자재량" not in sel_cat:
                st.write(f"📌 **적용 일수:** `{req_days}일`")
            proof_memo = st.text_input("사유 및 메모")
            proof_img = st.text_input("📸 인증 사진 이미지 링크 (선택)")
            if proof_img.strip():
                st.image(proof_img, width=200)
            if st.button("🏖️ 정기휴가 신청하기", type="primary"):
                ok, msg = db.submit_vacation_request(sel_m_id, sel_cat, req_days, proof_memo, proof_img)
                if ok:
                    db.log_activity("kakao_user_app", current_user["username"], current_user["nickname"], current_user["grade"], "정기휴가신청", f"{sel_cat} ({req_days}일)")
                    st.success(msg)
                else:
                    st.error(msg)

    with tab4:
        st.subheader("🎁 포인트 상점")
        members = db.get_members(only_active=True)
        items = db.get_shop_items()
        if members and items:
            buyer_opts = {f"[{m.get('grade', 'M1')}] {m['nickname']} ({m['points']}P)": m["id"] for m in members}
            def_b = next((k for k in buyer_opts if current_user["nickname"] in k), list(buyer_opts.keys())[0])
            buyer_id = buyer_opts[st.selectbox("본인 선택", list(buyer_opts.keys()), index=list(buyer_opts.keys()).index(def_b), key="shop_u")]
            st.divider()
            for item in items:
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.write(f"**{item['item_name']}** — `{item['price']}P` (재고: {item['stock']}개) | {item['description']}")
                c2.write(f"{item['price']}P")
                if c3.button("교환", key=f"b_{item['id']}"):
                    ok, msg = db.order_shop_item(buyer_id, item["id"])
                    if ok:
                        db.log_activity("kakao_user_app", current_user["username"], current_user["nickname"], current_user["grade"], "상점교환", f"{item['item_name']} ({item['price']}P)")
                        st.success(msg)
                    else:
                        st.error(msg)
        else:
            st.info("등록된 상점 상품이 없습니다.")


# ==========================================
# PAGE 2: 경고/강퇴 관리
# ==========================================
elif page == "🛡️ 경고/강퇴 관리":
    cfg = get_tbl_settings("mod_settings")
    wd_name = WEEKDAY_NAMES[cfg["cutoff_weekday"]]
    t_str = f"{cfg['cutoff_hour']:02d}:{cfg['cutoff_minute']:02d}"

    c_rooms = db.get_option_list("control_rooms", ["거래방", "거방1", "소통방", "2호점", "기타"])
    c_types = db.get_option_list("control_action_types", ["경고", "경고1회", "경고2회", "강퇴", "내풀", "재심", "기타"])
    c_reasons = db.get_option_list("control_reasons", ["내풀", "경고누적", "분탕", "미옾프", "거쿨", "가격미기재", "관리자재량", "기타"])

    st.title("🛡️ 부방 경고/강퇴 관리")
    st.caption(f"마감선: 매주 {wd_name} {t_str} | 세이프 25명 (-5점) | 35명 이상 10명당 +5점")

    active_members = db.get_members(only_active=True)
    member_nicknames = [m["nickname"] for m in active_members]

    with st.sidebar:
        st.divider()
        if is_mgr and member_nicknames:
            def_i = member_nicknames.index(current_user["nickname"]) if current_user["nickname"] in member_nicknames else 0
            rec_staff = st.selectbox("기록 담당 부방 (MS/MP 대리 선택)", member_nicknames, index=def_i)
        else:
            rec_staff = current_user["nickname"]

        now_dt = datetime.now()
        cur_cut = get_cutoff_date_str(now_dt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
        rem = datetime.strptime(f"{cur_cut} {t_str}:00", "%Y-%m-%d %H:%M:%S") - now_dt
        st.info(f"**이번 주 마감:** {cur_cut} ({wd_name[0]}) {t_str}\n\n**남은 시간:** {max(rem.days,0)}일 {max(rem.seconds//3600,0)}시간")

        if is_mgr:
            with st.expander("⚙️ 마감 기준 시간 변경 (MS/MP 전용)"):
                nw = st.selectbox("마감 요일", WEEKDAY_NAMES, index=cfg["cutoff_weekday"], key="mod_wd")
                ch1, ch2 = st.columns(2)
                nh = ch1.number_input("시", 0, 23, cfg["cutoff_hour"], key="mod_h")
                nm = ch2.number_input("분", 0, 59, cfg["cutoff_minute"], key="mod_m")
                if st.button("💾 마감 기준 적용", key="mod_save_cut", use_container_width=True):
                    w_idx = WEEKDAY_NAMES.index(nw)
                    save_tbl_setting("mod_settings", "cutoff_weekday", str(w_idx))
                    save_tbl_setting("mod_settings", "cutoff_hour", str(int(nh)))
                    save_tbl_setting("mod_settings", "cutoff_minute", str(int(nm)))
                    recalculate_cutoffs("mod_records", "action_dt", w_idx, int(nh), int(nm))
                    db.log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "마감기준변경", f"매주 {nw} {int(nh):02d}:{int(nm):02d}")
                    st.success("변경 완료!")
                    st.rerun()

    conn = db.get_conn()
    df_all = pd.read_sql_query("SELECT * FROM mod_records ORDER BY action_dt DESC, id DESC", conn)
    conn.close()

    def calc_mod_score(cnt: int) -> dict:
        if cnt < 25:
            return {"status": "😭 미달(감점)", "score": -5, "next": f"세이프(25명)까지 {25-cnt}건 남음"}
        if cnt < 35:
            return {"status": "✅ 세이프", "score": 0, "next": f"가산점(35명)까지 {35-cnt}건 남음"}
        bonus = 5 + ((cnt - 35) // 10) * 5
        nxt = 35 + (((cnt - 35) // 10) + 1) * 10
        return {"status": "✴️ 가산점 달성", "score": bonus, "next": f"다음 가점({nxt}명)까지 {nxt-cnt}건 남음"}

    t1, t2, t3, t4 = st.tabs(["📝 조치 기록 입력", "📊 주간 점수판 & 자동 반영", "🔍 대상자 검색 / 재심·내풀", "🛡️ 검수 & 백업"])

    with t1:
        c_f, c_b = st.columns(2, gap="large")
        with c_f:
            st.subheader("1️⃣ 간편 개별 입력")
            with st.form("mod_single_form", clear_on_submit=True):
                t_name = st.text_input("대상자 닉네임 *")
                a1, a2 = st.columns(2)
                a_date = a1.date_input("조치일", value=date.today())
                a_time = a2.time_input("조치시간", value=datetime.now().time().replace(second=0, microsecond=0))
                a3, a4 = st.columns(2)
                room = a3.selectbox("방 선택", c_rooms)
                a_type = a4.selectbox("조치 유형", c_types)
                r_sel = st.selectbox("사유 선택", c_reasons)
                r_cust = st.text_input("사유 직접 입력 (선택)")
                note_in = st.text_input("비고 / 재심 참고 메모")
                if st.form_submit_button("💾 기록 저장", type="primary", use_container_width=True):
                    if not t_name.strip():
                        st.error("대상자 닉네임을 입력해 주세요.")
                    else:
                        f_reason = r_cust.strip() if r_cust.strip() else r_sel
                        dt_obj = datetime.combine(a_date, a_time)
                        c_str = get_cutoff_date_str(dt_obj, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
                        conn = db.get_conn()
                        conn.execute(
                            "INSERT INTO mod_records (target_name, action_date, action_time, action_dt, cutoff_date, room, action_type, reason, staff_name, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (t_name.lstrip("@").strip(), a_date.strftime("%Y-%m-%d"), a_time.strftime("%H:%M"), dt_obj.strftime("%Y-%m-%d %H:%M"), c_str, room, a_type, f_reason, rec_staff, note_in.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        )
                        conn.commit()
                        conn.close()
                        db.log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "경고강퇴등록", f"{t_name} ({room}/{f_reason})")
                        st.success(f"저장 완료! (마감 주차: {c_str})")
                        st.rerun()

        with c_b:
            st.subheader("2️⃣ 카톡 양식 붙여넣기 (일괄 등록)")
            b_txt = st.text_area("카톡 내용 붙여넣기", height=240, placeholder="FC야구찐팬/26.09.28/23:49/거래방/내풀\n@최강삼성라이온즈 경고2회\n사유 거쿨\n261117/10:28/거방1")
            if st.button("⚡ 자동 분석 후 일괄 저장", use_container_width=True):
                lines = [l.strip() for l in b_txt.splitlines() if l.strip()]
                items, errs, i = [], [], 0
                while i < len(lines):
                    line = lines[i]
                    if line.count("/") >= 4:
                        nm, ds, ts, rm, rsn = [p.strip() for p in line.split("/", 4)]
                        try:
                            dt_obj = datetime.combine(parse_date_str(ds), parse_time_str(ts))
                            atype = "내풀" if "내풀" in rsn else ("강퇴" if "강퇴" in rsn else "경고")
                            items.append((nm.lstrip("@").strip(), dt_obj.strftime("%Y-%m-%d"), dt_obj.strftime("%H:%M"), dt_obj.strftime("%Y-%m-%d %H:%M"), get_cutoff_date_str(dt_obj, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"]), rm, atype, rsn, rec_staff, "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                        except Exception as e:
                            errs.append(f"'{line}': {e}")
                        i += 1
                        continue
                    if i + 2 < len(lines) and lines[i + 2].count("/") == 2:
                        try:
                            ch = lines[i].lstrip("@").strip()
                            m_h = re.match(r"^(\S+)\s*(.*)$", ch)
                            nm = m_h.group(1) if m_h else ch
                            atype = m_h.group(2).strip() if (m_h and m_h.group(2).strip()) else "경고"
                            rsn = re.sub(r"^사유\s*[:：]?\s*", "", lines[i + 1]).strip()
                            ds, ts, rm = [p.strip() for p in lines[i + 2].split("/", 2)]
                            dt_obj = datetime.combine(parse_date_str(ds), parse_time_str(ts))
                            items.append((nm, dt_obj.strftime("%Y-%m-%d"), dt_obj.strftime("%H:%M"), dt_obj.strftime("%Y-%m-%d %H:%M"), get_cutoff_date_str(dt_obj, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"]), rm, atype, rsn, rec_staff, "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                            i += 3
                            continue
                        except Exception as e:
                            errs.append(f"'{lines[i]}': {e}")
                            i += 1
                            continue
                    errs.append(f"건너뛴 줄: '{line}'")
                    i += 1
                if items:
                    conn = db.get_conn()
                    conn.executemany("INSERT INTO mod_records (target_name, action_date, action_time, action_dt, cutoff_date, room, action_type, reason, staff_name, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", items)
                    conn.commit()
                    conn.close()
                    db.log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "경고강퇴일괄등록", f"{len(items)}건")
                    st.success(f"총 {len(items)}건 저장 완료!")
                if errs:
                    with st.expander(f"건너뛴 줄 ({len(errs)}건)"):
                        for e in errs:
                            st.write(f"- {e}")
                if items:
                    st.rerun()

    with t2:
        if df_all.empty:
            st.info("기록이 없습니다.")
        else:
            weeks = sorted(df_all["cutoff_date"].unique().tolist(), reverse=True)
            if cur_cut not in weeks:
                weeks.insert(0, cur_cut)
            cw1, cw2 = st.columns(2)
            sel_w = cw1.selectbox("마감 주차", weeks, key="mod_w_sel")
            s_in = cw2.text_input("시즌명", value=f"{date.today().year}-Q{(date.today().month-1)//3+1}", key="mod_season")
            df_w = df_all[df_all["cutoff_date"] == sel_w]
            s_list = sorted(list(set(member_nicknames + df_w["staff_name"].unique().tolist())))
            rows = []
            for stf in s_list:
                df_s = df_w[df_w["staff_name"] == stf]
                vc = int((df_s["is_valid_score"] == 1).sum())
                ec = int((df_s["is_valid_score"] == 0).sum())
                si = calc_mod_score(vc)
                rows.append({"담당 부방": stf, "유효 건수": vc, "제외 건수": ec, "달성 상태": si["status"], "주간 점수": si["score"], "다음 목표": si["next"]})
            df_sc = pd.DataFrame(rows).sort_values(by=["주간 점수", "유효 건수"], ascending=[False, False])
            st.dataframe(df_sc, use_container_width=True, hide_index=True)

            st.divider()
            if is_mgr:
                if st.button("✅ 선택한 주차 점수 일괄 자동 반영하기 (MS/MP)", type="primary"):
                    ok_c, sk_c = 0, 0
                    for _, r in df_sc.iterrows():
                        conn = db.get_conn()
                        ex = conn.execute("SELECT status FROM mod_score_sync_log WHERE week_cutoff=? AND staff_name=?", (sel_w, r["담당 부방"])).fetchone()
                        mem = conn.execute("SELECT id FROM members WHERE nickname=?", (r["담당 부방"],)).fetchone()
                        conn.close()
                        if (ex and ex["status"] == "SUCCESS") or not mem:
                            sk_c += 1
                            continue
                        db.record_performance(mem["id"], s_in.strip() or "시즌통합", f"{sel_w} 마감", "경고/강퇴 주간실적", 0, int(r["주간 점수"]), 0, f"유효 조치 {int(r['유효 건수'])}건 ({r['달성 상태']})")
                        conn = db.get_conn()
                        conn.execute("INSERT INTO mod_score_sync_log (week_cutoff, staff_name, member_id, valid_count, score_delta, status, message, synced_at) VALUES (?,?,?,?,?,'SUCCESS','반영완료',?) ON CONFLICT(week_cutoff, staff_name) DO UPDATE SET status='SUCCESS', synced_at=excluded.synced_at", (sel_w, r["담당 부방"], mem["id"], int(r["유효 건수"]), int(r["주간 점수"]), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        conn.close()
                        ok_c += 1
                    db.log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "주간점수연동", f"{sel_w} ({ok_c}명 반영)")
                    st.success(f"반영 완료! (신규 {ok_c}명 / 건너뜀 {sk_c}명)")
            else:
                st.info("🔒 주간 점수 확정 및 포인트 자동 반영은 **MS / MP 등급**만 가능합니다.")

    with t3:
        if df_all.empty:
            st.info("검색할 기록이 없습니다.")
        else:
            sq = st.text_input("대상자 닉네임 또는 사유 검색")
            df_f = df_all[df_all["target_name"].str.contains(sq.strip(), case=False, na=False) | df_all["reason"].str.contains(sq.strip(), case=False, na=False)] if sq.strip() else df_all
            st.dataframe(df_f[["id", "target_name", "action_dt", "cutoff_date", "room", "action_type", "reason", "staff_name", "review_status", "note"]], use_container_width=True, hide_index=True)
            if is_mgr and not df_f.empty:
                st.divider()
                opts = {f"[#{r['id']}] {r['target_name']} | {r['action_dt']} | {r['room']} | {r['reason']}": r["id"] for _, r in df_f.head(50).iterrows()}
                sid = opts[st.selectbox("수정할 기록 선택 (MS/MP)", list(opts.keys()))]
                tr = df_all[df_all["id"] == sid].iloc[0]
                rc1, rc2, rc3 = st.columns([1, 1, 2])
                ns = rc1.selectbox("재심 상태", REVIEW_STATUSES, index=REVIEW_STATUSES.index(tr["review_status"]) if tr["review_status"] in REVIEW_STATUSES else 0)
                nv = rc2.checkbox("점수 인정", value=bool(tr["is_valid_score"]))
                nn = rc3.text_input("관리자 메모", value=tr["note"] or "")
                b1, b2 = st.columns([1, 4])
                if b1.button("✅ 상태 저장", type="primary"):
                    conn = db.get_conn()
                    conn.execute("UPDATE mod_records SET is_valid_score=?, review_status=?, note=? WHERE id=?", (int(nv), ns, nn, sid))
                    conn.commit()
                    conn.close()
                    db.log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "조치재심수정", f"[#{sid}] {ns}")
                    st.rerun()
                if b2.button("🗑️ 기록 삭제"):
                    conn = db.get_conn()
                    conn.execute("DELETE FROM mod_records WHERE id=?", (sid,))
                    conn.commit()
                    conn.close()
                    db.log_activity("kakao_control_manager", current_user["username"], current_user["nickname"], current_user["grade"], "조치기록삭제", f"[#{sid}]")
                    st.rerun()

    with t4:
        if not df_all.empty:
            dup = df_all.groupby(["target_name", "action_date"]).agg(당일건수=("id", "count"), 담당부방=("staff_name", lambda x: ", ".join(x))).reset_index()
            susp = dup[dup["당일건수"] >= 2]
            if susp.empty:
                st.success("동일 날짜 중복 조치 의심 건이 없습니다.")
            else:
                st.warning(f"동일 날짜 중복 기록 {len(susp)}건 발견")
                st.dataframe(susp, use_container_width=True, hide_index=True)
            st.download_button("📊 CSV 다운로드", df_all.to_csv(index=False).encode("utf-8-sig"), f"경고강퇴기록_{date.today().strftime('%Y%m%d')}.csv", "text/csv")


# ==========================================
# PAGE 3: 보이스룸(보룸) 관리
# ==========================================
elif page == "🎙️ 보이스룸(보룸) 관리":
    cfg = get_tbl_settings("voice_settings")
    wd_name = WEEKDAY_NAMES[cfg["cutoff_weekday"]]
    t_str = f"{cfg['cutoff_hour']:02d}:{cfg['cutoff_minute']:02d}"
    v_rooms = db.get_option_list("voiceroom_rooms", ["7ㅓ방", "1호점", "2호점", "소통방", "피모700방", "피모1000방", "기타"])

    st.title("🎙️ 부방 보이스룸(보룸) 관리")
    st.caption(f"마감선: 매주 {wd_name} {t_str} | 세이프 40시간 (-5점) | 50시간 이상 10시간당 +5점")

    active_members = db.get_members(only_active=True)
    member_nicknames = [m["nickname"] for m in active_members]

    with st.sidebar:
        st.divider()
        if is_mgr and member_nicknames:
            def_i = member_nicknames.index(current_user["nickname"]) if current_user["nickname"] in member_nicknames else 0
            rec_staff = st.selectbox("기록 담당 부방 (MS/MP 대리 선택)", member_nicknames, index=def_i, key="vr_stf")
        else:
            rec_staff = current_user["nickname"]

        now_dt = datetime.now()
        cur_cut = get_cutoff_date_str(now_dt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
        rem = datetime.strptime(f"{cur_cut} {t_str}:00", "%Y-%m-%d %H:%M:%S") - now_dt
        st.info(f"**이번 주 마감:** {cur_cut} ({wd_name[0]}) {t_str}\n\n**남은 시간:** {max(rem.days,0)}일 {max(rem.seconds//3600,0)}시간")

        if is_mgr:
            with st.expander("⚙️ 마감 기준 시간 변경 (MS/MP 전용)"):
                nw = st.selectbox("마감 요일", WEEKDAY_NAMES, index=cfg["cutoff_weekday"], key="vr_wd")
                ch1, ch2 = st.columns(2)
                nh = ch1.number_input("시", 0, 23, cfg["cutoff_hour"], key="vr_h")
                nm = ch2.number_input("분", 0, 59, cfg["cutoff_minute"], key="vr_m")
                if st.button("💾 마감 기준 적용", key="vr_save_cut", use_container_width=True):
                    w_idx = WEEKDAY_NAMES.index(nw)
                    save_tbl_setting("voice_settings", "cutoff_weekday", str(w_idx))
                    save_tbl_setting("voice_settings", "cutoff_hour", str(int(nh)))
                    save_tbl_setting("voice_settings", "cutoff_minute", str(int(nm)))
                    recalculate_cutoffs("voice_records", "end_dt", w_idx, int(nh), int(nm))
                    db.log_activity("kakao_voiceroom_manager", current_user["username"], current_user["nickname"], current_user["grade"], "마감기준변경", f"매주 {nw} {int(nh):02d}:{int(nm):02d}")
                    st.success("변경 완료!")
                    st.rerun()

    def sec_to_hms(sec: int) -> str:
        return f"{sec//3600}시간 {(sec%3600)//60:02d}분 {sec%60:02d}초"

    def parse_dur(ds: str) -> tuple[int, str]:
        p = [x.strip() for x in ds.strip().split(":")]
        h, m, s = (int(p[0]), int(p[1]), int(p[2])) if len(p) == 3 else (int(p[0]), int(p[1]), 0)
        if m >= 60 or s >= 60 or h < 0 or m < 0 or s < 0:
            raise ValueError("올바른 시:분:초 범위가 아닙니다.")
        return h * 3600 + m * 60 + s, f"{h}:{m:02d}:{s:02d}"

    def calc_vr_score(sec: int) -> dict:
        s40, s50 = 40 * 3600, 50 * 3600
        if sec < s40:
            return {"status": "😭 미달(감점)", "score": -5, "next": f"세이프(40h)까지 {sec_to_hms(s40-sec)} 남음"}
        if sec < s50:
            return {"status": "✅ 세이프", "score": 0, "next": f"가산점(50h)까지 {sec_to_hms(s50-sec)} 남음"}
        eh = (sec - s50) // 3600
        bonus = 5 + (eh // 10) * 5
        nt = 50 + ((eh // 10) + 1) * 10
        return {"status": "✴️ 가산점 달성", "score": bonus, "next": f"다음 가점({nt}h)까지 {sec_to_hms(nt*3600-sec)} 남음"}

    conn = db.get_conn()
    df_all = pd.read_sql_query("SELECT * FROM voice_records ORDER BY end_dt DESC, id DESC", conn)
    conn.close()

    vt1, vt2, vt3 = st.tabs(["📝 보룸 기록 입력", "📊 주간 보룸 시간 & 자동 반영", "📋 전체 이력 & 백업"])

    with vt1:
        c1, c2 = st.columns(2, gap="large")
        with c1:
            st.subheader("1️⃣ 간편 개별 입력")
            with st.form("vr_single_form", clear_on_submit=True):
                r_sel = st.selectbox("방 선택", v_rooms)
                r_cus = st.text_input("방 직접 입력 (선택)")
                d1, d2 = st.columns(2)
                sd = d1.date_input("시작일", value=date.today())
                ed = d2.date_input("종료일", value=date.today())
                d3, d4 = st.columns(2)
                et = d3.time_input("종료시간", value=datetime.now().time().replace(second=0, microsecond=0))
                dur_in = d4.text_input("진행 시간 (시:분:초) *", placeholder="10:08:32")
                note_in = st.text_input("메모 (선택)")
                if st.form_submit_button("💾 보룸 기록 저장", type="primary", use_container_width=True):
                    try:
                        ds, nd = parse_dur(dur_in)
                        frm = r_cus.strip() if r_cus.strip() else r_sel
                        edt = datetime.combine(ed, et)
                        c_str = get_cutoff_date_str(edt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"])
                        conn = db.get_conn()
                        conn.execute("INSERT INTO voice_records (staff_name, room, start_date, end_date, end_time, end_dt, cutoff_date, duration_str, duration_seconds, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (rec_staff, frm, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d"), et.strftime("%H:%M"), edt.strftime("%Y-%m-%d %H:%M"), c_str, nd, ds, note_in.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        conn.close()
                        db.log_activity("kakao_voiceroom_manager", current_user["username"], current_user["nickname"], current_user["grade"], "보룸기록등록", f"{frm} ({nd})")
                        st.success(f"저장 완료! ({nd})")
                        st.rerun()
                    except Exception as e:
                        st.error(f"오류: {e}")

        with c2:
            st.subheader("2️⃣ 카톡 양식 붙여넣기 (일괄 등록)")
            b_txt = st.text_area("카톡 보룸 내용 붙여넣기", height=240, placeholder="FC야구찐팬/7ㅓ방/26.09.30~10.01/08:32/10:08:32\n망고샤베트/소통방/11.16/19:59/1:48:44")
            if st.button("⚡ 자동 분석 후 일괄 저장", use_container_width=True):
                raw_lines = [l.strip() for l in b_txt.splitlines() if l.strip()]
                merged, idx = [], 0
                while idx < len(raw_lines):
                    cur = raw_lines[idx]
                    while cur.count("/") < 4 and cur.endswith("/") and idx + 1 < len(raw_lines):
                        idx += 1
                        cur += raw_lines[idx]
                    merged.append(cur)
                    idx += 1
                items, errs = [], []
                for line in merged:
                    if line.count("/") < 4:
                        errs.append(f"형식 불일치: '{line}'")
                        continue
                    try:
                        sn, rm, dr, ets, durs = [p.strip() for p in line.split("/", 4)]
                        def p_sd(r, ry=None, rm_n=None):
                            ns = [int(x) for x in re.findall(r"\d+", r)]
                            cy = ry if ry else date.today().year
                            if len(ns) == 3:
                                return date(2000 + ns[0] if ns[0] < 100 else ns[0], ns[1], ns[2])
                            if len(ns) == 2:
                                return date(cy, ns[0], ns[1])
                            if len(ns) == 1 and rm_n:
                                return date(cy, rm_n, ns[0])
                            raise ValueError(f"날짜 오류: {r}")
                        if "~" in dr or "-" in dr:
                            sep = "~" if "~" in dr else "-"
                            l_p, r_p = [x.strip() for x in dr.split(sep, 1)]
                            s_d = p_sd(l_p)
                            e_d = p_sd(r_p, s_d.year, s_d.month)
                        else:
                            s_d = e_d = p_sd(dr)
                        e_t = parse_time_str(ets)
                        edt = datetime.combine(e_d, e_t)
                        ds, nd = parse_dur(durs)
                        items.append((sn.lstrip("@").strip(), rm, s_d.strftime("%Y-%m-%d"), e_d.strftime("%Y-%m-%d"), e_t.strftime("%H:%M"), edt.strftime("%Y-%m-%d %H:%M"), get_cutoff_date_str(edt, cfg["cutoff_weekday"], cfg["cutoff_hour"], cfg["cutoff_minute"]), nd, ds, "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    except Exception as e:
                        errs.append(f"'{line}': {e}")
                if items:
                    conn = db.get_conn()
                    conn.executemany("INSERT INTO voice_records (staff_name, room, start_date, end_date, end_time, end_dt, cutoff_date, duration_str, duration_seconds, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", items)
                    conn.commit()
                    conn.close()
                    db.log_activity("kakao_voiceroom_manager", current_user["username"], current_user["nickname"], current_user["grade"], "보룸일괄등록", f"{len(items)}건")
                    st.success(f"총 {len(items)}건 저장 완료!")
                if errs:
                    with st.expander(f"건너뛴 줄 ({len(errs)}건)"):
                        for e in errs:
                            st.write(f"- {e}")
                if items:
                    st.rerun()

    with vt2:
        if df_all.empty:
            st.info("기록이 없습니다.")
        else:
            weeks = sorted(df_all["cutoff_date"].unique().tolist(), reverse=True)
            if cur_cut not in weeks:
                weeks.insert(0, cur_cut)
            cw1, cw2 = st.columns(2)
            sel_w = cw1.selectbox("마감 주차", weeks, key="vr_w_sel")
            s_in = cw2.text_input("시즌명", value=f"{date.today().year}-Q{(date.today().month-1)//3+1}", key="vr_season")
            df_w = df_all[df_all["cutoff_date"] == sel_w]
            s_list = sorted(list(set(member_nicknames + df_w["staff_name"].unique().tolist())))
            rows = []
            for stf in s_list:
                df_s = df_w[(df_w["staff_name"] == stf) & (df_w["is_valid_score"] == 1)]
                t_sec = int(df_s["duration_seconds"].sum()) if not df_s.empty else 0
                si = calc_vr_score(t_sec)
                rows.append({"담당 부방": stf, "누적 시간": sec_to_hms(t_sec), "달성 상태": si["status"], "주간 점수": si["score"], "다음 목표": si["next"], "_sec": t_sec})
            df_sc = pd.DataFrame(rows).sort_values(by=["주간 점수", "_sec"], ascending=[False, False])
            st.dataframe(df_sc.drop(columns=["_sec"]), use_container_width=True, hide_index=True)

            st.divider()
            if is_mgr:
                if st.button("✅ 선택한 주차 보룸 점수 일괄 자동 반영 (MS/MP)", type="primary"):
                    ok_c, sk_c = 0, 0
                    for _, r in df_sc.iterrows():
                        conn = db.get_conn()
                        ex = conn.execute("SELECT status FROM voice_score_sync_log WHERE week_cutoff=? AND staff_name=?", (sel_w, r["담당 부방"])).fetchone()
                        mem = conn.execute("SELECT id FROM members WHERE nickname=?", (r["담당 부방"],)).fetchone()
                        conn.close()
                        if (ex and ex["status"] == "SUCCESS") or not mem:
                            sk_c += 1
                            continue
                        db.record_performance(mem["id"], s_in.strip() or "시즌통합", f"{sel_w} 마감", "보이스룸 주간실적", int(r["_sec"]) // 60, int(r["주간 점수"]), 0, f"보룸 누적 {r['누적 시간']} ({r['달성 상태']})")
                        conn = db.get_conn()
                        conn.execute("INSERT INTO voice_score_sync_log (week_cutoff, staff_name, member_id, total_seconds, score_delta, status, message, synced_at) VALUES (?,?,?,?,?,'SUCCESS','반영완료',?) ON CONFLICT(week_cutoff, staff_name) DO UPDATE SET status='SUCCESS', synced_at=excluded.synced_at", (sel_w, r["담당 부방"], mem["id"], int(r["_sec"]), int(r["주간 점수"]), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                        conn.commit()
                        conn.close()
                        ok_c += 1
                    db.log_activity("kakao_voiceroom_manager", current_user["username"], current_user["nickname"], current_user["grade"], "보룸점수연동", f"{sel_w} ({ok_c}명 반영)")
                    st.success(f"반영 완료! (신규 {ok_c}명 / 건너뜀 {sk_c}명)")
            else:
                st.info("🔒 보룸 주간 점수 확정 및 포인트 자동 반영은 **MS / MP 등급**만 가능합니다.")

    with vt3:
        if not df_all.empty:
            st.dataframe(df_all[["id", "staff_name", "room", "start_date", "end_date", "end_time", "cutoff_date", "duration_str", "is_valid_score", "note"]], use_container_width=True, hide_index=True)
            if is_mgr:
                opts = {f"[#{r['id']}] {r['staff_name']} | {r['room']} | {r['end_dt']} | {r['duration_str']}": r["id"] for _, r in df_all.head(50).iterrows()}
                sid = opts[st.selectbox("수정/삭제 선택 (MS/MP)", list(opts.keys()))]
                tr = df_all[df_all["id"] == sid].iloc[0]
                c1, c2 = st.columns([1, 3])
                vc = c1.checkbox("시간 인정", value=bool(tr["is_valid_score"]))
                ne = c2.text_input("메모", value=tr["note"] or "")
                b1, b2 = st.columns([1, 4])
                if b1.button("✅ 저장", type="primary"):
                    conn = db.get_conn()
                    conn.execute("UPDATE voice_records SET is_valid_score=?, note=? WHERE id=?", (int(vc), ne, sid))
                    conn.commit()
                    conn.close()
                    st.rerun()
                if b2.button("🗑️ 삭제"):
                    conn = db.get_conn()
                    conn.execute("DELETE FROM voice_records WHERE id=?", (sid,))
                    conn.commit()
                    conn.close()
                    st.rerun()
            st.download_button("📊 CSV 다운로드", df_all.to_csv(index=False).encode("utf-8-sig"), f"보이스룸기록_{date.today().strftime('%Y%m%d')}.csv", "text/csv")


# ==========================================
# PAGE 4: 관리자 통합 대시보드 (MS/MP 전용)
# ==========================================
elif page == "📊 관리자 통합 대시보드 (MS/MP)" and is_mgr:
    st.title("📊 관리자 통합 대시보드 (MS / MP 전용)")
    admin_tab = st.selectbox("관리 기능 선택", [
        "👥 회원가입 승인/거절 & 부방 관리",
        "⚙️ 방·조치사유·항목 설정",
        "🕵️ 전체 앱 사용·접속 기록",
        "⭐ 실적 및 포인트 수동 지급",
        "📋 상세 지급 내역 히스토리 & 취소",
        "🏖️ 정기휴가 신청 관리",
        "🎁 상점 및 교환 관리",
        "🔄 월말 결산 (리셋 & 60% 내림 이월)",
    ])

    if admin_tab == "👥 회원가입 승인/거절 & 부방 관리":
        t_u, t_m = st.tabs(["📝 회원가입 승인 관리", "🎖️ 부방 명단 · 등급(MS/MP) · 삭제"])
        with t_u:
            st_opt = st.selectbox("가입 상태 필터", ["대기중", "전체", "승인완료", "거절됨"])
            for u in db.get_users(st_opt):
                c1, c2, c3, c4 = st.columns([2.2, 1.2, 0.8, 0.8])
                c1.write(f"**{u['nickname']}** (`@{u['username']}`) | 신청일: {u['created_at']}")
                c2.write(f"상태: **{u['approval_status']}**")
                if u["username"] != "admin":
                    if c3.button("✅ 승인", key=f"ua_{u['id']}", disabled=(u["approval_status"] == "승인완료")):
                        db.update_user_approval(u["id"], "승인완료")
                        db.log_activity("kakao_admin_app", current_user["username"], current_user["nickname"], current_user["grade"], "가입승인", u["nickname"])
                        st.rerun()
                    if c4.button("❌ 거절", key=f"ur_{u['id']}", disabled=(u["approval_status"] == "거절됨")):
                        db.update_user_approval(u["id"], "거절됨")
                        db.log_activity("kakao_admin_app", current_user["username"], current_user["nickname"], current_user["grade"], "가입거절", u["nickname"])
                        st.rerun()
        with t_m:
            with st.form("adm_add_m"):
                nn = st.text_input("닉네임")
                rr = st.text_input("직책", value="부방")
                gg = st.selectbox("초기 등급", ["M1", "MS", "MP"])
                if st.form_submit_button("등록") and nn.strip():
                    ok, msg = db.add_member(nn, rr, gg)
                    st.success(msg) if ok else st.error(msg)
            st.divider()
            for m in db.get_members():
                c1, c2, c3, c4 = st.columns([2, 1.3, 1.3, 0.8])
                c1.write(f"**[{m.get('grade','M1')}] {m['nickname']}** ({m['role']}) - {m['points']}P")
                ng = c2.selectbox("등급", ["MP", "MS", "M1", "M2", "M3", "M4"], index=["MP", "MS", "M1", "M2", "M3", "M4"].index(m["grade"]), key=f"mg_{m['id']}")
                if ng != m["grade"]:
                    db.update_member_grade(m["id"], ng)
                    st.rerun()
                ns = c3.selectbox("상태", ["활동중", "휴식", "해임"], index=["활동중", "휴식", "해임"].index(m["status"]), key=f"ms_{m['id']}")
                if ns != m["status"]:
                    db.update_member_status(m["id"], ns)
                    st.rerun()
                with c4:
                    st.write("")
                    if st.button("🗑️ 삭제", key=f"dm_{m['id']}"):
                        db.delete_member(m["id"])
                        st.rerun()

    elif admin_tab == "⚙️ 방·조치사유·항목 설정":
        c_rooms = db.get_option_list("control_rooms", ["거래방", "거방1", "소통방", "2호점", "기타"])
        c_types = db.get_option_list("control_action_types", ["경고", "경고1회", "경고2회", "강퇴", "내풀", "재심", "기타"])
        c_reasons = db.get_option_list("control_reasons", ["내풀", "경고누적", "분탕", "미옾프", "거쿨", "가격미기재", "관리자재량", "기타"])
        v_rooms = db.get_option_list("voiceroom_rooms", ["7ㅓ방", "1호점", "2호점", "소통방", "피모700방", "피모1000방", "기타"])
        with st.form("opt_form"):
            col1, col2 = st.columns(2)
            t_cr = col1.text_area("🛡️ 경고/강퇴 — 방 목록", value="\n".join(c_rooms), height=160)
            t_crs = col1.text_area("🛡️ 경고/강퇴 — 조치 사유 목록", value="\n".join(c_reasons), height=180)
            t_ct = col2.text_area("🛡️ 경고/강퇴 — 조치 유형 목록", value="\n".join(c_types), height=160)
            t_vr = col2.text_area("🎙️ 보이스룸 — 방 목록", value="\n".join(v_rooms), height=180)
            if st.form_submit_button("💾 모든 항목 설정 저장", type="primary", use_container_width=True):
                db.save_option_list("control_rooms", t_cr.splitlines())
                db.save_option_list("control_action_types", t_ct.splitlines())
                db.save_option_list("control_reasons", t_crs.splitlines())
                db.save_option_list("voiceroom_rooms", t_vr.splitlines())
                db.log_activity("kakao_admin_app", current_user["username"], current_user["nickname"], current_user["grade"], "항목설정변경", "방/사유 업데이트")
                st.success("저장 완료!")

    elif admin_tab == "🕵️ 전체 앱 사용·접속 기록":
        f1, f2 = st.columns([1, 2])
        af = f1.selectbox("앱 필터", ["전체", "unified_app", "kakao_user_app", "kakao_control_manager", "kakao_voiceroom_manager", "kakao_admin_app", "auth"])
        ql = f2.text_input("아이디 · 닉네임 · 활동 검색")
        alogs = db.get_activity_logs(af, limit=500)
        if alogs:
            df_a = pd.DataFrame(alogs)
            if ql.strip():
                q = ql.strip()
                df_a = df_a[df_a["username"].str.contains(q, case=False, na=False) | df_a["nickname"].str.contains(q, case=False, na=False) | df_a["action"].str.contains(q, case=False, na=False) | df_a["detail"].str.contains(q, case=False, na=False)]
            df_a = df_a[["created_at", "app_name", "username", "nickname", "grade", "action", "detail"]]
            df_a.columns = ["시간", "앱", "아이디", "닉네임", "등급", "활동", "상세 내용"]
            st.dataframe(df_a, use_container_width=True, hide_index=True)

    elif admin_tab == "⭐ 실적 및 포인트 수동 지급":
        am = db.get_members(only_active=True)
        if am:
            mo = {f"[{m.get('grade','M1')}] {m['nickname']} ({m['points']}P)": m["id"] for m in am}
            sl = st.selectbox("대상 부방", list(mo.keys()))
            with st.form("adm_perf"):
                c1, c2 = st.columns(2)
                ss = c1.text_input("시즌", value="2026-Q1")
                wk = c2.text_input("주차", value="1주차")
                ct = st.text_input("활동 항목", value="일반관리")
                p1, p2 = st.columns(2)
                pts = p1.number_input("지급 포인트 (P)", -500, 500, 10)
                vac = p2.number_input("지급 휴가 (일)", -30, 30, 0)
                mm = st.text_area("메모")
                if st.form_submit_button("반영하기", type="primary"):
                    gm = db.record_performance(mo[sl], ss, wk, ct, 0, pts, vac, mm)
                    db.log_activity("kakao_admin_app", current_user["username"], current_user["nickname"], current_user["grade"], "실적지급", f"{sl} ({pts:+d}P)")
                    st.success(f"반영 완료!{gm}")

    elif admin_tab == "📋 상세 지급 내역 히스토리 & 취소":
        logs = db.get_logs(100)
        if logs:
            st.dataframe([{"번호": l["id"], "시간": l["created_at"], "시즌": l.get("season", "-"), "주차": l.get("week", "-"), "부방": l["nickname"], "항목": l["category"], "포인트": f"{l['points_delta']:+d}P", "휴가": f"{l['vacation_delta']:+d}일", "메모": l["memo"]} for l in logs], use_container_width=True, hide_index=True)
            lo = {f"[#{l['id']}] {l['created_at']} | {l['nickname']} | {l['category']} ({l['points_delta']:+d}P)": l for l in logs}
            tl = lo[st.selectbox("취소할 지급 내역 선택", list(lo.keys()))]
            if st.button("↩️ 선택한 지급 내역 취소하기", type="primary"):
                conn = db.get_conn()
                mr = conn.execute("SELECT id, points, vacation_days, grade FROM members WHERE id=?", (tl["member_id"],)).fetchone()
                if mr:
                    np = max(0, mr["points"] - tl["points_delta"])
                    nv = max(0, mr["vacation_days"] - tl["vacation_delta"])
                    og = mr["grade"] or "M1"
                    ng = og if og in ["MP", "MS"] else db.calculate_grade(np, og)
                    conn.execute("UPDATE members SET points=?, vacation_days=?, grade=? WHERE id=?", (np, nv, ng, mr["id"]))
                ws = str(tl.get("week") or "").replace(" 마감", "").strip()
                if ws:
                    for tbl in ("mod_score_sync_log", "voice_score_sync_log"):
                        try:
                            conn.execute(f"DELETE FROM {tbl} WHERE week_cutoff=? AND staff_name=?", (ws, tl["nickname"]))
                        except Exception:
                            pass
                conn.execute("DELETE FROM logs WHERE id=?", (tl["id"],))
                conn.commit()
                conn.close()
                st.success("취소 및 원상복구 완료!")
                st.rerun()

    elif admin_tab == "🏖️ 정기휴가 신청 관리":
        for r in db.get_vacation_requests():
            c1, c2, c3 = st.columns([3, 1.5, 2])
            c1.write(f"**[{r.get('grade','M1')}] {r['nickname']}** — `{r['category']}` ({r['requested_days']}일) | {r['proof_memo']}")
            c2.write(f"상태: **{r['status']}**")
            cd = c2.number_input("지급일수", value=r["requested_days"], key=f"ad_{r['id']}") if "관리자재량" in r["category"] and r["status"] == "대기중" else r["requested_days"]
            if r["status"] == "대기중":
                b1, b2 = c3.columns(2)
                if b1.button("✅ 승인", key=f"aa_{r['id']}"):
                    db.process_vacation_request(r["id"], "승인완료", cd)
                    st.rerun()
                if b2.button("❌ 거절", key=f"ar_{r['id']}"):
                    db.process_vacation_request(r["id"], "거절됨")
                    st.rerun()
            st.divider()

    elif admin_tab == "🎁 상점 및 교환 관리":
        t1, t2 = st.tabs(["상품 등록", "교환 승인"])
        with t1:
            with st.form("adm_shop"):
                sn = st.text_input("상품명")
                sp = st.number_input("필요 포인트", value=10)
                sd = st.text_area("설명")
                ss = st.number_input("재고", value=10)
                if st.form_submit_button("등록") and sn.strip():
                    db.add_shop_item(sn, sp, sd, ss)
                    st.rerun()
            for it in db.get_shop_items():
                if st.button(f"삭제: {it['item_name']}", key=f"di_{it['id']}"):
                    db.delete_shop_item(it["id"])
                    st.rerun()
        with t2:
            for o in db.get_shop_orders():
                c1, c2, c3 = st.columns([2, 1, 1])
                c1.write(f"**{o['nickname']}** ➔ `{o['item_name']}` ({o['price']}P)")
                c2.write(o["status"])
                ns = c3.selectbox("상태", ["대기중", "처리완료", "취소됨"], index=["대기중", "처리완료", "취소됨"].index(o["status"]), key=f"ao_{o['id']}")
                if ns != o["status"]:
                    db.update_order_status(o["id"], ns)
                    st.rerun()

    elif admin_tab == "🔄 월말 결산 (리셋 & 60% 내림 이월)":
        st.warning("⚠️ 실행 시 활동중 부방 포인트가 0P로 리셋되고 기존 포인트의 60%(내림)가 휴가로 이월됩니다.")
        if st.button("🚀 월말 결산 실행", type="primary"):
            for line in db.monthly_reset_and_carryover():
                st.write(f"- {line}")
            st.success("월말 결산 완료!")