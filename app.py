# app.py
# AOV戰略助手（含 Google Sheets 同步／一鍵測試／自動載入）
# 功能：查詢/新增/更新/刪除/雙向修補/Ban Pick/英雄庫/Tier 排行/體系陣容
# 特色：
# 1) 側邊欄「檢查 Secrets」「測試 Google Sheets 連線」
# 2) 可「保存到 Google Sheet」「從 Google Sheet 載入」
# 3) 勾選「自動同步到 Sheet」→ 每次本地保存（含體系操作）會自動同步
# 4) 重新開機（reboot）時：若勾「啟動時優先從 Sheet 載入」，會先讀雲端資料

import json, os, re, time
from typing import Dict, List, Tuple, Union
import streamlit as st

# ---- 這兩個外部套件用於 Google Sheets 連線 ----
# pip 安裝：gspread、google-auth
import gspread
from google.oauth2.service_account import Credentials

# ---------- 常數與檔案 ----------
DATA_FILE = "aov_heroes.json"
IMAGES_DIR = "hero_images"
os.makedirs(IMAGES_DIR, exist_ok=True)

ROLE_CHOICES = ["坦克", "戰士", "刺客", "法師", "射手", "輔助"]
LANE_CHOICES = ["凱撒路", "中路", "打野", "魔龍路", "游走"]
TIER_CHOICES = ["", "T0", "T1", "T2", "T3", "特殊"]
TIER_ORDER  = ["T0", "T1", "T2", "T3", "特殊", ""]
TIER_WEIGHT = {t:i for i,t in enumerate(TIER_ORDER)}
ALLOWED_IMAGE_TYPES = ["png","jpg","jpeg","webp"]

def tier_rank(t:str)->int:
    return TIER_WEIGHT.get(t, len(TIER_ORDER))

# ---------- I/O ----------
def load_data()->Dict[str,Dict]:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_data(data:Dict[str,Dict])->None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------- 全局 BAN（總 Ban） ----------
def get_global_bans(d: Dict[str, Dict]) -> List[str]:
    v = d.get("__ban_list__", [])
    return list(v) if isinstance(v, list) else []

def set_global_bans(d: Dict[str, Dict], bans: List[str]) -> None:
    d["__ban_list__"] = sorted(dedupe([b for b in bans if b]))

# ---------- 各分路 BAN ----------
def get_lane_bans(d: Dict[str, Dict]) -> Dict[str, List[str]]:
    v = d.get("__lane_bans__", {})
    if not isinstance(v, dict):
        v = {}
    out = {}
    for lane in LANE_CHOICES:
        lst = v.get(lane, [])
        out[lane] = [x for x in lst if isinstance(x, str)]
    return out

def set_lane_bans(d: Dict[str, Dict], lane_bans: Dict[str, List[str]]) -> None:
    clean = {}
    for lane in LANE_CHOICES:
        lst = lane_bans.get(lane, [])
        clean[lane] = sorted(dedupe([x for x in lst if x]))
    d["__lane_bans__"] = clean

# ---------- 體系陣容（相容新版/舊版） ----------
CompMembers = List[str]
CompData = Dict[str, Union[str, List[str]]]
Compositions = Dict[str, Dict[str, Union[str, List[str]]]]

def _normalize_comp_entry(entry: Union[CompMembers, CompData]) -> Dict[str, Union[str, List[str]]]:
    if isinstance(entry, list):
        return {"members": sorted(dedupe(entry)), "core": "", "counters": []}
    elif isinstance(entry, dict):
        members = entry.get("members", []) or []
        core = entry.get("core", "") or ""
        counters = entry.get("counters", []) or []
        return {
            "members": sorted(dedupe([m for m in members if m])),
            "core": core,
            "counters": sorted(dedupe([c for c in counters if c]))
        }
    else:
        return {"members": [], "core": "", "counters": []}

def get_compositions(d: Dict[str, Dict]) -> Compositions:
    raw = d.get("__compositions__", {})
    if not isinstance(raw, dict):
        return {}
    out: Compositions = {}
    for name, entry in raw.items():
        if not name: 
            continue
        out[name] = _normalize_comp_entry(entry)
    return out

def set_compositions(d: Dict[str, Dict], comps: Compositions) -> None:
    clean: Compositions = {}
    for name, entry in comps.items():
        if not name:
            continue
        norm = _normalize_comp_entry(entry)
        clean[name] = norm
    d["__compositions__"] = clean

# ---------- 小工具 ----------
def dedupe(xs:List[str])->List[str]:
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out

def norm_list(s:str)->List[str]:
    if not s: return []
    s = s.replace(",", " ")
    parts = [p.strip() for p in s.split() if p.strip()]
    return dedupe(parts)

def ensure_fields(h: Dict) -> Dict:
    lane_tiers = h.get("lane_tiers", {}) or {}
    for lane in LANE_CHOICES:
        lane_tiers.setdefault(lane, "")
    lanes_list = list(h.get("lanes", []))
    main_lane = h.get("main_lane", "")
    if not main_lane and lanes_list:
        main_lane = lanes_list[0] if lanes_list[0] in LANE_CHOICES else ""
    return {
        "tier": h.get("tier", ""),
        "roles": list(h.get("roles", [])),
        "lanes": lanes_list,
        "main_lane": main_lane,
        "synergy": list(h.get("synergy", [])),
        "counters": list(h.get("counters", [])),
        "countered_by": list(h.get("countered_by", [])),
        "ban_targets": list(h.get("ban_targets", [])),
        "image": h.get("image", ""),
        "lane_tiers": lane_tiers,
    }

def ensure_bidirectional_relationships(data: Dict[str, Dict]) -> int:
    changes = 0
    for k,v in list(data.items()):
        if k.startswith("__"):  # 略過全局鍵
            continue
        data[k] = ensure_fields(v)
    names = {n for n in data.keys() if not n.startswith("__")}

    for name, h in list(data.items()):
        if name.startswith("__"):  # 跳過全局鍵
            continue
        for key in ["counters","countered_by","ban_targets","synergy"]:
            before = len(h.get(key, []))
            h[key] = [x for x in h.get(key, []) if x in names and x != name]
            h[key] = dedupe(h[key])
            if len(h[key]) != before: changes += 1

    for a, ha in list(data.items()):
        if a.startswith("__"):
            continue
        for b in ha["counters"]:
            if b in data and a not in data[b]["countered_by"]:
                data[b]["countered_by"].append(a)
                data[b]["countered_by"] = dedupe(data[b]["countered_by"])
                changes += 1
        for b in ha["countered_by"]:
            if b in data and a not in data[b]["counters"]:
                data[b]["counters"].append(a)
                data[b]["counters"] = dedupe(data[b]["counters"])
                changes += 1
    return changes

def safe_slug(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")

def save_uploaded_image(hero_name: str, uploaded_file) -> str:
    if uploaded_file is None: return ""
    ext = uploaded_file.name.split(".")[-1].lower()
    if ext not in ALLOWED_IMAGE_TYPES:
        st.error("只接受 png/jpg/jpeg/webp 圖片格式")
        return ""
    filename = f"{safe_slug(hero_name)}.{ext}"
    path = os.path.join(IMAGES_DIR, filename)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path

def get_hero_image_path(data: Dict[str, Dict], name: str) -> str:
    h = data.get(name) or {}
    p = h.get("image", "")
    if p:
        candidate = p
        if not os.path.isabs(candidate) and not candidate.startswith(IMAGES_DIR):
            candidate = os.path.join(IMAGES_DIR, os.path.basename(candidate))
        if os.path.exists(candidate):
            return candidate
    base = safe_slug(name)
    for ext in ALLOWED_IMAGE_TYPES:
        candidate = os.path.join(IMAGES_DIR, f"{base}.{ext}")
        if os.path.exists(candidate):
            return candidate
    for ext in ALLOWED_IMAGE_TYPES:
        candidate = os.path.join(IMAGES_DIR, f"{name}.{ext}")
        if os.path.exists(candidate):
            return candidate
    return ""

# ---------- Google Sheets 連線 ----------
def _secrets_ok() -> Tuple[bool, str]:
    try:
        root_keys = list(st.secrets.keys())
    except Exception as e:
        return False, f"讀 st.secrets 失敗：{e}"
    for must in ("gcp_service_account", "GSHEET_ID", "GSHEET_TAB"):
        if must not in st.secrets:
            return False, f"st.secrets 沒有 {must}。"
    return True, "OK"

def _gsheet_init():
    ok, msg = _secrets_ok()
    if not ok: raise KeyError(msg)
    info = dict(st.secrets["gcp_service_account"])
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(st.secrets["GSHEET_ID"])
    tab = st.secrets["GSHEET_TAB"]
    try:
        ws = sh.worksheet(tab)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=1000, cols=20)
        # 初始化表頭
        ws.update("A1:D1", [["composition", "core", "members", "counters"]])
    return ws

def gsheet_push_compositions(data: Dict[str, Dict]) -> int:
    """把 __compositions__ 寫到 Google Sheet (清掉舊資料後重寫)。回傳寫入列數。"""
    ws = _gsheet_init()
    comps = get_compositions(data)
    rows = [["composition", "core", "members", "counters"]]
    for cname, entry in sorted(comps.items()):
        members = " ".join(entry.get("members", []) or [])
        core = entry.get("core", "") or ""
        counters = " ".join(entry.get("counters", []) or [])
        rows.append([cname, core, members, counters])

    # 清空舊資料（保留表頭）
    ws.resize(rows=1)
    if len(rows) > 1:
        ws.update(f"A1:D{len(rows)}", rows)
    else:
        ws.update("A1:D1", rows)

    return len(rows) - 1

def gsheet_pull_compositions(data: Dict[str, Dict]) -> int:
    """從 Google Sheet 讀取 compositions 覆蓋回 data。回傳讀到的體系數。"""
    ws = _gsheet_init()
    vals = ws.get_all_values()
    if not vals:
        set_compositions(data, {})
        return 0
    header = [h.strip().lower() for h in vals[0]]
    try:
        c_idx = header.index("composition")
        core_idx = header.index("core")
        members_idx = header.index("members")
        counters_idx = header.index("counters")
    except ValueError:
        # 表頭不完整
        set_compositions(data, {})
        return 0

    comps: Compositions = {}
    for row in vals[1:]:
        if not row or all(not c.strip() for c in row):
            continue
        cname = (row[c_idx] if c_idx < len(row) else "").strip()
        if not cname:
            continue
        core = (row[core_idx] if core_idx < len(row) else "").strip()
        members = norm_list(row[members_idx] if members_idx < len(row) else "")
        counters = norm_list(row[counters_idx] if counters_idx < len(row) else "")
        comps[cname] = {"members": members, "core": core, "counters": counters}

    set_compositions(data, comps)
    return len(comps)

# ---------- 縮圖網格（純展示；全域外觀設定） ----------
def render_image_grid(names: List[str], data: Dict[str, Dict], size:int, cols:int, show_names:bool):
    for i in range(0, len(names), cols):
        row = st.columns(cols)
        for j, nm in enumerate(names[i:i+cols]):
            with row[j]:
                p = get_hero_image_path(data, nm)
                if p and os.path.exists(p):
                    st.image(p, width=size)
                else:
                    st.markdown(
                        f"<div style='width:{size}px;height:{size}px;border:1px dashed #999;border-radius:8px;"
                        "display:flex;align-items:center;justify-content:center;font-size:11px;color:#999;'>No Img</div>",
                        unsafe_allow_html=True,
                    )
                if show_names:
                    st.caption(nm)

def lane_tier_lines(h: Dict) -> List[str]:
    h = ensure_fields(h)
    main = h.get("main_lane", "")
    lines = []
    for lane in LANE_CHOICES:
        if lane == main:
            continue
        lt = h["lane_tiers"].get(lane, "")
        if lt:
            lines.append(f"{lane}：{lt}")
    return lines

# ---------- 快速編輯面板 ----------
def quick_edit_panel(name: str):
    data = st.session_state.data
    if name not in data:
        return
    h = ensure_fields(data[name])
    st.markdown("### ✏️ 快速編輯：" + name)
    cols = st.columns(2)

    with cols[0]:
        p_main = get_hero_image_path(data, name)
        if p_main:
            st.image(p_main, caption=name, use_container_width=False)

    with cols[1]:
        tier = st.selectbox("T 度（可留白）", TIER_CHOICES,
                            index=TIER_CHOICES.index(h.get("tier", "")),
                            key=f"qe_tier_{name}")
        roles = st.multiselect("職業（可複選）", ROLE_CHOICES, default=h["roles"],
                               key=f"qe_roles_{name}")

        main_lane = st.selectbox("主路線", [""] + LANE_CHOICES,
                                 index=([""] + LANE_CHOICES).index(h.get("main_lane","")),
                                 key=f"qe_main_lane_{name}")
        other_default = [l for l in h["lanes"] if l and l != main_lane]
        others = st.multiselect("其他路線（可複選）",
                                [l for l in LANE_CHOICES if l != main_lane],
                                default=other_default,
                                key=f"qe_other_lanes_{name}")
        lanes = [l for l in [main_lane] + others if l]

        st.markdown("**各路線 T 度（編輯用）**")
        lane_tiers = h["lane_tiers"].copy()
        for lane in LANE_CHOICES:
            lane_tiers[lane] = st.selectbox(
                f"{lane}", TIER_CHOICES,
                index=TIER_CHOICES.index(h["lane_tiers"].get(lane, "")),
                key=f"qe_lt_{name}_{lane}"
            )

        counters = st.text_input("克制（逗號或空白分隔）",
                                 " ".join(h["counters"]),
                                 key=f"qe_counters_{name}")
        st.text_input("被克制（逗號或空白分隔）",
                      " ".join(h["countered_by"]),
                      key=f"qe_countered_by_{name}")

        img_file = st.file_uploader("🖼️ 更新圖片（可選）",
                                    type=ALLOWED_IMAGE_TYPES,
                                    key=f"qe_img_{name}")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("💾 保存這位英雄", key=f"qe_save_{name}"):
                data[name] = {
                    "tier": tier,
                    "roles": roles,
                    "lanes": lanes,
                    "main_lane": main_lane,
                    "counters": norm_list(counters),
                    "countered_by": norm_list(st.session_state.get(f"qe_countered_by_{name}", "")),
                    "ban_targets": h.get("ban_targets", []),
                    "image": h.get("image", ""),
                    "lane_tiers": lane_tiers,
                    "synergy": h.get("synergy", []),
                }
                if img_file is not None:
                    path = save_uploaded_image(name, img_file)
                    if path: 
                        data[name]["image"] = path
                c = ensure_bidirectional_relationships(data)
                save_data(data)
                st.success(f"已保存『{name}』，修補 {c} 項")
                _maybe_auto_sync()
        with b2:
            if st.button("❌ 關閉快速編輯", key=f"qe_close_{name}"):
                st.session_state.quick_edit_name = ""
                st.experimental_rerun()

# ---------- UI ----------
st.set_page_config(page_title="AOV戰略助手", page_icon="🛡️", layout="wide")
st.title("🛡️ AOV戰略助手")

# 側邊欄：雲端同步與除錯
with st.sidebar:
    st.markdown("### ☁️ 雲端同步（Google Sheets）")
    auto_sync = st.checkbox("自動同步到 Sheet", value=True, help="每次保存本地資料時，自動覆蓋到 Google Sheet")
    startup_pull = st.checkbox("啟動時優先從 Sheet 載入", value=True, help="重啟後先讀雲端的 __compositions__ 回本地")
    st.session_state.setdefault("auto_sync", auto_sync)
    st.session_state["auto_sync"] = auto_sync
    st.session_state.setdefault("startup_pull", startup_pull)
    st.session_state["startup_pull"] = startup_pull

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔍 檢查 Secrets"):
            _ok, _msg = _secrets_ok()
            if _ok:
                st.success("Secrets OK（含 gcp_service_account、GSHEET_ID、GSHEET_TAB）")
            else:
                st.error(_msg)
    with col_b:
        if st.button("🧪 測試 Google Sheets 連線"):
            try:
                ws = _gsheet_init()
                st.success(f"✅ 連線成功，使用工作表：{ws.title}")
            except Exception as e:
                st.error(f"❌ 連線失敗：{e}")

    st.divider()
    st.markdown("### 快速操作")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("⬆️ 保存到 Google Sheet"):
            try:
                n = gsheet_push_compositions(st.session_state.data)
                st.success(f"已寫入 {n} 個體系到 Sheet")
            except Exception as e:
                st.error(f"寫入失敗：{e}")
    with col2:
        if st.button("⬇️ 從 Google Sheet 載入"):
            try:
                n = gsheet_pull_compositions(st.session_state.data)
                save_data(st.session_state.data)
                st.success(f"已從 Sheet 載入 {n} 個體系並保存到本地")
            except Exception as e:
                st.error(f"讀取失敗：{e}")

# 狀態
if "data" not in st.session_state:
    st.session_state.data = load_data()
if "picked_name" not in st.session_state:
    st.session_state.picked_name = "（請選擇）"
if "quick_edit_name" not in st.session_state:
    st.session_state.quick_edit_name = ""

data: Dict[str, Dict] = st.session_state.data

# 啟動時：若勾選，先從 Sheet 拉體系
if st.session_state.get("startup_pull", False):
    try:
        n = gsheet_pull_compositions(data)
        if n:
            save_data(data)
            st.info(f"（啟動自動）已從 Sheet 載入 {n} 個體系")
    except Exception as e:
        st.warning(f"啟動自動載入失敗：{e}")

# Toolbar
colA, colB, colC, colD = st.columns([1,1,1,2])
with colA:
    if st.button("💾 保存到 aov_heroes.json"):
        save_data(data); st.success("已保存")
        _maybe_auto_sync()
with colB:
    if st.button("🧩 修正雙向關係"):
        c = ensure_bidirectional_relationships(data); save_data(data)
        st.success(f"已修正 {c} 項")
        _maybe_auto_sync()
with colC:
    uploaded = st.file_uploader("⬆️ 匯入 JSON（覆蓋現有資料）", type=["json"], label_visibility="collapsed", key="import_json")
    if uploaded:
        try:
            st.session_state.data = json.load(uploaded)
            data = st.session_state.data
            save_data(data)
            st.success("匯入成功！")
            _maybe_auto_sync()
        except Exception as e:
            st.error(f"匯入失敗：{e}")
with colD:
    st.download_button("⬇️ 下載目前資料",
                       data=json.dumps(data, ensure_ascii=False, indent=2),
                       file_name="aov_heroes.json")

st.divider()

# 分頁
(tab1, tab2, tabComp, tabBan, tabLib, tabTier) = st.tabs([
    "🔎 查詢 / 編輯",
    "➕ 新增英雄",
    "🏹 體系陣容",
    "🛑 Ban Pick",
    "🖼️ 英雄庫",
    "⚔️ Tier 排行"
])

# --------- 查詢/編輯 ---------
with tab1:
    left, right = st.columns([1,1])
    with left:
        q = st.text_input("搜尋英雄（輸入關鍵字）", "", key="search_query")
        default_index = 0
        pending = st.session_state.pop("pending_pick", None)

        names = sorted([n for n in data.keys()
                        if (not n.startswith("__")) and (q.strip() in n if q else True)])

        if pending and pending in names:
            default_index = names.index(pending) + 1

        picked = st.selectbox("選擇英雄",
                              ["（請選擇）"] + names,
                              index=default_index,
                              key="picked_name")

        if picked != "（請選擇）" and picked in data:
            h = ensure_fields(data[picked])
            st.subheader(f"📄 {picked}")
            p_main = get_hero_image_path(data, picked)
            if p_main:
                st.image(p_main, caption=picked, use_container_width=False)

            main = h.get("main_lane","")
            t_main = (h["lane_tiers"].get(main, "") if main else "") or "—"
            st.write(f"**T 度**：{t_main}")

            st.write(f"**職業**：{', '.join(h['roles']) or '—'}")
            st.write(f"**主路線**：{main or '—'}")
            others_txt = ', '.join([l for l in h['lanes'] if l and l != main]) or '—'
            st.write(f"**其他路線**：{others_txt}")

            st.markdown("**其他路線 T 度**")
            for line in lane_tier_lines(h):
                st.write("• " + line)

            st.markdown("**克制（counters）**")
            if h["counters"]:
                render_image_grid(h["counters"], data, size=72, cols=10, show_names=False)
            else:
                st.caption("—")

            st.markdown("**被克制（countered_by）**")
            if h["countered_by"]:
                render_image_grid(h["countered_by"], data, size=72, cols=10, show_names=False)
            else:
                st.caption("—")

            # 所屬體系
            comps = get_compositions(data)
            belong = [cname for cname, cdata in comps.items() if picked in (cdata.get("members") or [])]
            if belong:
                st.write("**所屬體系**：" + "、".join(belong))

    with right:
        st.markdown("#### ✏️ 即時編輯")
        picked = st.session_state.picked_name
        if picked and picked in data and picked != "（請選擇）":
            h = ensure_fields(data[picked])
            tier = st.selectbox("T 度（可留白）", TIER_CHOICES,
                                index=TIER_CHOICES.index(h.get("tier","")),
                                key=f"edit_tier_{picked}")
            roles = st.multiselect("職業（可複選）", ROLE_CHOICES, default=h["roles"],
                                   key=f"edit_roles_{picked}")

            main_lane = st.selectbox("主路線", [""] + LANE_CHOICES,
                                     index=([""] + LANE_CHOICES).index(h.get("main_lane","")),
                                     key=f"edit_main_lane_{picked}")
            other_default = [l for l in h["lanes"] if l and l != main_lane]
            others = st.multiselect("其他路線（可複選）",
                                    [l for l in LANE_CHOICES if l != main_lane],
                                    default=other_default,
                                    key=f"edit_other_lanes_{picked}")
            lanes = [l for l in [main_lane] + others if l]

            st.markdown("**各路線 T 度（僅顯示已選路線，其他可展開）**")
            lane_tiers = h["lane_tiers"].copy()
            for lane in lanes:
                lane_tiers[lane] = st.selectbox(
                    f"{lane}", TIER_CHOICES,
                    index=TIER_CHOICES.index(h["lane_tiers"].get(lane, "")),
                    key=f"edit_lt_{picked}_{lane}"
                )
            with st.expander("進階：顯示所有路線的 T 度"):
                for lane in LANE_CHOICES:
                    if lane not in lanes:
                        lane_tiers[lane] = st.selectbox(
                            f"{lane}", TIER_CHOICES,
                            index=TIER_CHOICES.index(h["lane_tiers"].get(lane, "")),
                            key=f"edit_lt_extra_{picked}_{lane}"
                        )

            counters = st.text_input("克制（以逗號或空白分隔）",
                                     " ".join(h["counters"]),
                                     key=f"edit_counters_{picked}")
            st.text_input("被克制（以逗號或空白分隔）",
                          " ".join(h["countered_by"]),
                          key=f"edit_countered_by_{picked}")

            img_file = st.file_uploader("🖼️ 上傳或更換英雄圖片",
                                        type=ALLOWED_IMAGE_TYPES,
                                        key=f"edit_img_{picked}")
            colx, coly, colz = st.columns(3)
            with colx:
                if st.button("✅ 更新", key=f"btn_update_{picked}"):
                    data[picked] = {
                        "tier": tier, "roles": roles, "lanes": lanes,
                        "main_lane": main_lane,
                        "counters": norm_list(counters),
                        "countered_by": norm_list(st.session_state.get(f"edit_countered_by_{picked}", "")),
                        "ban_targets": h.get("ban_targets", []),
                        "image": h.get("image", ""),
                        "lane_tiers": lane_tiers,
                        "synergy": h.get("synergy", []),
                    }
                    if img_file is not None:
                        path = save_uploaded_image(picked, img_file)
                        if path:
                            data[picked]["image"] = path
                    c = ensure_bidirectional_relationships(data); save_data(data)
                    st.success(f"已更新『{picked}』，修補 {c} 項")
                    _maybe_auto_sync()
            with coly:
                if st.button("🗑️ 刪除該英雄", key=f"btn_delete_{picked}"):
                    if h.get("image") and os.path.exists(h["image"]):
                        try: os.remove(h["image"])
                        except Exception: pass
                    del data[picked]
                    for hh in data.values():
                        if isinstance(hh, dict) and "counters" in hh:
                            for key in ["counters","countered_by","ban_targets","synergy"]:
                                hh[key] = [x for x in hh[key] if x != picked]
                    comps = get_compositions(data)
                    changed = False
                    for cname in list(comps.keys()):
                        members = comps[cname].get("members", [])
                        if picked in members:
                            comps[cname]["members"] = [x for x in members if x != picked]
                            changed = True
                        if comps[cname].get("core","") == picked:
                            comps[cname]["core"] = ""
                            changed = True
                        counters_c = comps[cname].get("counters", [])
                        if picked in counters_c:
                            comps[cname]["counters"] = [x for x in counters_c if x != picked]
                            changed = True
                    if changed:
                        set_compositions(data, comps)
                    c = ensure_bidirectional_relationships(data); save_data(data)
                    st.success(f"已刪除『{picked}』，並修補 {c} 項")
                    _maybe_auto_sync()
            with colz:
                if st.button("🖼️ 只更新圖片", key=f"btn_img_only_{picked}"):
                    if img_file is None:
                        st.warning("請先選擇圖片檔")
                    else:
                        path = save_uploaded_image(picked, img_file)
                        if path:
                            data[picked]["image"] = path
                            save_data(data)
                            st.success("圖片已更新！")
                            _maybe_auto_sync()
                st.download_button("⬇️ 下載目前資料(JSON)",
                                   data=json.dumps(data, ensure_ascii=False, indent=2),
                                   file_name="aov_heroes.json",
                                   key=f"download_json_{picked}")

# --------- 新增英雄 ---------
with tab2:
    st.subheader("新增英雄")
    name = st.text_input("英雄名稱 *", key="new_name")
    tier = st.selectbox("T 度（可留白）", TIER_CHOICES, key="new_tier")
    roles = st.multiselect("職業（可複選）", ROLE_CHOICES, key="new_roles")

    main_lane = st.selectbox("主路線", [""] + LANE_CHOICES, key="new_main_lane")
    others = st.multiselect("其他路線（可複選）",
                            [l for l in LANE_CHOICES if l != main_lane],
                            key="new_other_lanes")
    lanes = [l for l in [main_lane] + others if l]

    st.markdown("**各路線 T 度（依你選的路線顯示）**")
    lane_tiers = {lane: "" for lane in LANE_CHOICES}
    for lane in lanes:
        lane_tiers[lane] = st.selectbox(lane, TIER_CHOICES, key=f"new_lt_{lane}")
    with st.expander("進階：顯示所有路線的 T 度（可選填）"):
        for lane in LANE_CHOICES:
            if lane not in lanes:
                lane_tiers[lane] = st.selectbox(lane, TIER_CHOICES, key=f"new_lt_extra_{lane}")

    counters = st.text_input("克制（以逗號或空白分隔）", key="new_counters")
    countered_by = st.text_input("被克制（以逗號或空白分隔）", key="new_countered_by")
    img_new = st.file_uploader("🖼️（可選）上傳英雄圖片", type=ALLOWED_IMAGE_TYPES, key="uploader_new")

    if st.button("➕ 新增", key="btn_new"):
        if not name.strip():
            st.error("英雄名稱不可為空"); st.stop()
        image_path = ""
        if img_new is not None:
            p = save_uploaded_image(name, img_new)
            if p: image_path = p
        data[name] = {
            "tier": tier,
            "roles": roles,
            "lanes": lanes,
            "main_lane": main_lane,
            "counters": norm_list(counters),
            "countered_by": norm_list(countered_by),
            "ban_targets": [],
            "image": image_path,
            "lane_tiers": lane_tiers,
            "synergy": [],
        }
        c = ensure_bidirectional_relationships(data); save_data(data)
        st.success(f"已新增『{name}』，修補 {c} 項")
        _maybe_auto_sync()

# --------- 體系陣容 ---------
with tabComp:
    st.subheader("🏹 體系陣容（__compositions__）")
    comps = get_compositions(data)

    with st.expander("➕ 新增體系", expanded=False):
        new_comp = st.text_input("體系名稱（如：消耗陣、開戰陣、POKE 陣）", key="comp_new_name")
        if st.button("建立體系", key="comp_btn_add"):
            if not new_comp.strip():
                st.warning("請輸入體系名稱")
            elif new_comp in comps:
                st.warning("已有同名體系")
            else:
                comps[new_comp] = {"members": [], "core": "", "counters": []}
                set_compositions(data, comps); save_data(data)
                st.success(f"已建立體系：{new_comp}")
                _maybe_auto_sync()

    st.divider()

    if not comps:
        st.info("目前沒有體系。先在上方展開新增吧！")
    else:
        for cname in sorted(comps.keys()):
            st.markdown(f"### {cname}")
            entry = comps.get(cname, {"members": [], "core": "", "counters": []})
            members: List[str] = entry.get("members", []) or []
            core: str = entry.get("core", "") or ""
            ctrs: List[str] = entry.get("counters", []) or []

            st.markdown("**核心英雄（Core）**")
            if core:
                render_image_grid([core], data, size=64, cols=1, show_names=False)
            else:
                st.caption("（未設定）")

            st.markdown("**成員（Members）**")
            if members:
                render_image_grid(members, data, size=64, cols=12, show_names=False)
            else:
                st.caption("（尚無成員）")

            st.markdown("**被克制（這個體系怕誰）**")
            if ctrs:
                render_image_grid(ctrs, data, size=64, cols=12, show_names=False)
            else:
                st.caption("（尚未指定）")

            with st.expander("⚙️ 管理這個體系", expanded=False):
                col1, col2, col3 = st.columns(3)
                with col1:
                    add_free = st.text_input("加入成員（逗號/空白分隔）", key=f"comp_free_{cname}")
                    if st.button("加入", key=f"comp_btn_join_{cname}"):
                        new_members = sorted(dedupe(members + norm_list(add_free)))
                        comps[cname]["members"] = new_members
                        set_compositions(data, comps); save_data(data)
                        st.success("已加入！")
                        _maybe_auto_sync()
                    rm_free = st.text_input("移除成員（逗號/空白分隔）", key=f"comp_rm_free_{cname}")
                    if st.button("移除成員", key=f"comp_btn_rm_{cname}"):
                        rm_list = set(norm_list(rm_free))
                        comps[cname]["members"] = [x for x in members if x not in rm_list]
                        set_compositions(data, comps); save_data(data)
                        st.success("已移除！")
                        _maybe_auto_sync()

                with col2:
                    core_free = st.text_input("核心英雄（輸入名字）", value=core, key=f"comp_core_free_{cname}")
                    if st.button("套用核心", key=f"comp_btn_core_{cname}"):
                        comps[cname]["core"] = core_free.strip()
                        set_compositions(data, comps); save_data(data)
                        st.success("核心已更新！")
                        _maybe_auto_sync()

                with col3:
                    ctr_free = st.text_input("被哪些英雄克制（逗號/空白分隔）", value=" ".join(ctrs), key=f"comp_ctr_free_{cname}")
                    if st.button("套用被克制", key=f"comp_btn_ctr_{cname}"):
                        comps[cname]["counters"] = sorted(dedupe(norm_list(ctr_free)))
                        set_compositions(data, comps); save_data(data)
                        st.success("被克制清單已更新！")
                        _maybe_auto_sync()

                st.markdown("---")
                if st.button("🗑️ 刪除這個體系", key=f"comp_btn_del_{cname}"):
                    comps.pop(cname, None)
                    set_compositions(data, comps); save_data(data)
                    st.success("已刪除體系")
                    _maybe_auto_sync()

            st.divider()

# --------- Ban Pick ---------
with tabBan:
    st.subheader("🛑 Ban Pick")
    mode = st.radio("顯示模式", ["總 Ban", "各分路 Ban"], horizontal=True, key="ban_mode")

    if mode == "總 Ban":
        st.markdown("### Ban")
        current_bans = get_global_bans(data)
        if not current_bans:
            st.info("目前沒有任何 Ban。你可以在下方新增。")
        else:
            render_image_grid(current_bans, data, size=64, cols=12, show_names=False)

        with st.expander("➕ 新增或移除（總 Ban）", expanded=False):
            st.markdown("**新增**")
            extra = st.text_input("輸入名字（逗號或空白分隔）", key="ban_extra")
            if st.button("加入 Ban", key="ban_add"):
                new_list = current_bans + norm_list(extra)
                set_global_bans(data, new_list); save_data(data)
                st.success("已加入 Ban！")
                _maybe_auto_sync()

            st.markdown("**移除**")
            remove_text = st.text_input("輸入要移除的名字（逗號/空白分隔）", key="ban_remove_text")
            if st.button("移除選取", key="ban_remove_btn"):
                to_remove = set(norm_list(remove_text))
                remain = [b for b in current_bans if b not in to_remove]
                set_global_bans(data, remain); save_data(data)
                st.success("已更新 Ban！")
                _maybe_auto_sync()

    else:
        st.markdown("### Ban")
        lane_bans = get_lane_bans(data)
        lane_sel = st.selectbox("選擇路線", LANE_CHOICES, index=2, key="ban_lane_sel")
        lst = lane_bans.get(lane_sel, [])

        if not lst:
            st.info(f"「{lane_sel}」目前沒有 Ban。下方可新增。")
        else:
            render_image_grid(lst, data, size=64, cols=12, show_names=False)

        with st.expander(f"➕ 新增或移除（{lane_sel}）", expanded=False):
            st.markdown("**新增**")
            extra = st.text_input("輸入名字（逗號或空白分隔）", key=f"lane_ban_extra_{lane_sel}")
            if st.button("加入 Ban（此路線）", key=f"lane_ban_add_{lane_sel}"):
                lane_bans[lane_sel] = sorted(dedupe(lst + norm_list(extra)))
                set_lane_bans(data, lane_bans); save_data(data)
                st.success(f"已加入 {lane_sel} 的 Ban！")
                _maybe_auto_sync()

            st.markdown("**移除**")
            rm_text = st.text_input("輸入要移除的名字（逗號/空白分隔）", key=f"lane_ban_remove_txt_{lane_sel}")
            if st.button("移除選取（此路線）", key=f"lane_ban_remove_btn_{lane_sel}"):
                to_remove = set(norm_list(rm_text))
                lane_bans[lane_sel] = [b for b in lst if b not in to_remove]
                set_lane_bans(data, lane_bans); save_data(data)
                st.success("已更新！")
                _maybe_auto_sync()

# --------- 英雄庫 ---------
with tabLib:
    st.subheader("🖼️ 英雄庫")
    colf1, colf2, colf3 = st.columns(3)
    with colf1:
        lane_filter = st.selectbox("路線篩選", ["全部"] + LANE_CHOICES, key="gallery_lane")
    with colf2:
        role_filter = st.selectbox("職業篩選", ["全部"] + ROLE_CHOICES, key="gallery_role")
    with colf3:
        if lane_filter != "全部":
            tier_filter = st.selectbox("T 度（此路線）", ["全部"] + TIER_CHOICES[1:], key="gallery_tier_lane")
        else:
            tier_filter = "（未選路線）"
            st.selectbox("T 度（此路線）", ["請先選擇路線"], index=0, key="gallery_tier_disabled")

    items: List[Tuple[str, Dict]] = []
    for name in sorted(data.keys()):
        if name.startswith("__"): 
            continue
        h = ensure_fields(data[name])
        if lane_filter != "全部" and lane_filter not in h["lanes"]:
            continue
        if role_filter != "全部" and role_filter not in h["roles"]:
            continue
        if lane_filter != "全部" and tier_filter != "全部":
            if h["lane_tiers"].get(lane_filter, "") != tier_filter:
                continue
        items.append((name, h))

    if not items:
        st.info("沒有符合條件的英雄。請調整篩選器。")
    else:
        render_image_grid([nm for nm,_ in items], data, size=64, cols=12, show_names=False)

# --------- Tier 排行 ---------
with tabTier:
    st.subheader("⚔️ 英雄 Tier 排行")
    target_lane = st.selectbox("選擇路線", LANE_CHOICES, index=2, key="tier_lane_view")

    lists = {"T0":[],"T1":[],"T2":[],"T3":[],"特殊":[]}
    for n, h in sorted(data.items()):
        if n.startswith("__"): 
            continue
        t = ensure_fields(h)["lane_tiers"].get(target_lane, "")
        if t in lists:
            lists[t].append(n)

    for tname in ["T0","T1","T2","T3","特殊"]:
        st.markdown(f"#### {tname}")
        lst = lists[tname]
        if not lst:
            st.caption("（目前空白）")
        else:
            render_image_grid(lst, data, size=64, cols=12, show_names=False)
        st.divider()


# ---------- 共用：自動同步 ----------
def _maybe_auto_sync():
    if st.session_state.get("auto_sync", False):
        try:
            n = gsheet_push_compositions(st.session_state.data)
            st.info(f"（自動）已同步 {n} 個體系到 Google Sheet")
        except Exception as e:
            st.warning(f"（自動同步失敗）{e}")
