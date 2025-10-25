# -*- coding: utf-8 -*-
"""
AOV 戰略助手（單檔版）
- 搜尋/編輯、英雄新增
- 體系陣容（只輸入名字、核心、被克制）
- Ban Pick（總 Ban / 各分路 Ban；只輸入名字）
- 英雄庫（職業 + 路線 篩選，T 度篩選會跟著目前條件）
- Tier 排行（移除拖曳與編輯，新增英雄時即反映）
- 圖片顯示：自動在 hero_images/ 找到最接近的檔名
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Any
import streamlit as st

APP_TITLE = "AOV 戰略助手"

# 路徑設定
ROOT = Path(__file__).parent
DATA_JSON = ROOT / "aov_heroes.json"
IMAGES_DIR = ROOT / "hero_images"
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# ------------------------------
# 資料層：IO 與預設資料
# ------------------------------
DEFAULT_DB: Dict[str, Any] = {
    "heroes": {},          # { name: {roles:[], lanes:[], tier:"T1", counters:[], countered_by:[], notes:""} }
    "bans": {              # Ban Pick 儲存
        "total": [],       # 總 Ban
        "lanes": {         # 各分路 Ban
            "凱撒路": [],
            "魔龍路": [],
            "中路": [],
            "打野": [],
            "輔助": [],
        }
    },
    "team_comps": []       # [{name, members:[...], core:"", countered_by:[...]}]
}

ROLES_ALL = ["凱撒", "射手", "法師", "刺客", "戰士", "輔助", "坦克"]
LANES_ALL = ["凱撒路", "魔龍路", "中路", "打野", "輔助"]
TIERS_ALL = ["T0", "T1", "T2", "T3", "T4"]

def load_db() -> Dict[str, Any]:
    if not DATA_JSON.exists():
        save_db(DEFAULT_DB)
        return json.loads(json.dumps(DEFAULT_DB, ensure_ascii=False))
    try:
        with DATA_JSON.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # 若檔案損壞，備援成預設
        return json.loads(json.dumps(DEFAULT_DB, ensure_ascii=False))

def save_db(db: Dict[str, Any]) -> None:
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    with DATA_JSON.open("w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

# ------------------------------
# 工具：名稱標準化與圖片搜尋
# ------------------------------
def norm(s: str) -> str:
    return "".join(s.split()).lower()

def find_hero_image(name: str) -> str | None:
    """
    在 hero_images/ 找圖片：
      1) 完全同名：蘇.png
      2) 去空白小寫：蘇 -> su.png / 蘇.jpg
      3) 模糊包含：檔名去空白小寫含有英雄名
    """
    if not name:
        return None
    key = norm(name)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 同名 + 常見副檔
    for ext in IMG_EXTS:
        p1 = IMAGES_DIR / f"{name}{ext}"
        if p1.exists():
            return str(p1)
        p2 = IMAGES_DIR / f"{key}{ext}"
        if p2.exists():
            return str(p2)

    # 2) 模糊掃一遍
    for p in IMAGES_DIR.iterdir():
        if p.suffix.lower() in IMG_EXTS:
            stem = norm(p.stem)
            if stem == key or key in stem:
                return str(p)
    return None

# ------------------------------
# UI 元件
# ------------------------------
def pill(text: str, color: str = "blue"):
    st.markdown(
        f"<span style='display:inline-block;padding:2px 8px;border-radius:999px;"
        f"background:{color};color:white;font-size:12px;margin-right:6px;'>{text}</span>",
        unsafe_allow_html=True
    )

def show_hero_row(db, name: str, size: int = 64):
    p = find_hero_image(name)
    cols = st.columns([0.18, 0.82]) if p else st.columns([0.01, 0.99])
    with cols[0]:
        if p: st.image(p, width=size)
    with cols[1]:
        st.markdown(f"**{name}**")
        info = db["heroes"].get(name, {})
        if roles := info.get("roles"): st.write("職業：", "／".join(roles))
        if lanes := info.get("lanes"): st.write("路線：", "／".join(lanes))
        if t := info.get("tier"): pill(tier_color(t), color=tier_color(t))
        if cts := info.get("counters"): st.write("克制：", "、".join(cts))
        if cted := info.get("countered_by"): st.write("被克制：", "、".join(cted))

def tier_color(tier: str) -> str:
    # 同時當做色碼用（簡易）
    mapping = {
        "T0": "#d97706", "T1": "#2563eb", "T2": "#0d9488",
        "T3": "#7c3aed", "T4": "#6b7280"
    }
    return mapping.get(tier, "#2563eb")

# ------------------------------
# 區塊：搜尋 / 編輯
# ------------------------------
def page_search_edit(db):
    st.subheader("🔎 查詢 / 編輯")
    q = st.text_input("搜尋英雄（輸入關鍵字）", "")
    candidates = [n for n in db["heroes"].keys() if q and (q in n or norm(q) in norm(n))]
    picked = st.selectbox("選擇英雄", ["（請選擇）"] + sorted(candidates))
    st.divider()

    if picked and picked != "（請選擇）":
        # 顯示主圖
        p_main = find_hero_image(picked)
        if p_main:
            st.image(p_main, caption=picked, width=240)
        else:
            st.info("找不到圖片，請將圖片放入 `hero_images/`，檔名建議與英雄名一致。")

        info = db["heroes"].get(picked, {})
        st.write(f"**T 度**：{info.get('tier','(未設定)')}")
        st.write("職業：", "／".join(info.get("roles", [])) or "(未設定)")
        st.write("主路線 / 其他路線：", "／".join(info.get("lanes", [])) or "(未設定)")
        st.write("克制（counters）：", "、".join(info.get("counters", [])) or "(未設定)")
        st.write("被克制（countered_by）：", "、".join(info.get("countered_by", [])) or "(未設定)")

        st.markdown("#### ✏️ 即時編輯")
        # 編輯欄位（維持輸入模式，不用下拉）
        t = st.text_input("T 度（例如：T1）", info.get("tier", "T1"))
        roles_str = st.text_input("職業（可留白；多個用空白分隔）", " ".join(info.get("roles", [])))
        lanes_str = st.text_input("路線（可留白；多個用空白分隔）", " ".join(info.get("lanes", [])))
        counters_str = st.text_input("克制（多個用空白分隔）", " ".join(info.get("counters", [])))
        cted_str = st.text_input("被克制（多個用空白分隔）", " ".join(info.get("countered_by", [])))

        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 儲存變更", use_container_width=True):
                db["heroes"][picked] = {
                    "tier": t.strip() if t.strip() else "T1",
                    "roles": [r for r in roles_str.split() if r],
                    "lanes": [l for l in lanes_str.split() if l],
                    "counters": [c for c in counters_str.split() if c],
                    "countered_by": [c for c in cted_str.split() if c],
                }
                save_db(db)
                st.success("已儲存。")
        with col2:
            if st.button("🗑️ 刪除英雄", type="secondary", use_container_width=True):
                if picked in db["heroes"]:
                    del db["heroes"][picked]
                    save_db(db)
                    st.success("已刪除。請重新選擇。")
                    st.stop()

# ------------------------------
# 區塊：新增英雄（新增即反映到 Tier）
# ------------------------------
def page_add_hero(db):
    st.subheader("➕ 新增英雄")
    name = st.text_input("英雄名稱", "")
    t = st.text_input("T 度（預設 T1）", "T1")
    roles = st.text_input("職業（可多個，用空白分隔）", "")
    lanes = st.text_input("路線（可多個，用空白分隔）", "")
    counters = st.text_input("克制（可多個，用空白分隔）", "")
    cted = st.text_input("被克制（可多個，用空白分隔）", "")

    if st.button("新增", use_container_width=True):
        name = name.strip()
        if not name:
            st.error("請輸入英雄名稱")
            return
        db["heroes"][name] = {
            "tier": t.strip() if t.strip() else "T1",
            "roles": [r for r in roles.split() if r],
            "lanes": [l for l in lanes.split() if l],
            "counters": [c for c in counters.split() if c],
            "countered_by": [c for c in cted.split() if c],
        }
        save_db(db)
        st.success(f"已新增：{name}")

# ------------------------------
# 區塊：體系陣容
# ------------------------------
def page_team_comp(db):
    st.subheader("📦 體系陣容")
    st.caption("說明：只輸入名字即可。可設定『核心』與『被哪些英雄克制』")
    name = st.text_input("陣容名稱", "")
    members_str = st.text_input("加入英雄（多個用空白分隔）", "")
    core = st.text_input("核心英雄（單一名字）", "")
    countered_by_str = st.text_input("被哪些英雄克制（多個用空白分隔）", "")

    if st.button("新增 / 更新 陣容", use_container_width=True):
        if not name.strip():
            st.error("請輸入陣容名稱")
            return
        entry = {
            "name": name.strip(),
            "members": [m for m in members_str.split() if m],
            "core": core.strip(),
            "countered_by": [c for c in countered_by_str.split() if c]
        }
        # 若同名則覆蓋
        replaced = False
        for i, e in enumerate(db["team_comps"]):
            if e["name"] == entry["name"]:
                db["team_comps"][i] = entry
                replaced = True
                break
        if not replaced:
            db["team_comps"].append(entry)
        save_db(db)
        st.success("已儲存。")

    st.markdown("---")
    if not db["team_comps"]:
        st.info("目前沒有陣容。")
    else:
        for comp in db["team_comps"]:
            with st.expander(f"🧩 {comp['name']}"):
                st.write("核心：", comp.get("core") or "(未設定)")
                st.write("成員：", "、".join(comp.get("members", [])) or "(未設定)")
                st.write("被克制：", "、".join(comp.get("countered_by", [])) or "(未設定)")
                # 縮圖列
                cols = st.columns(6)
                for idx, m in enumerate(comp.get("members", [])[:6]):
                    with cols[idx]:
                        p = find_hero_image(m)
                        if p: st.image(p, caption=m, use_container_width=True)

# ------------------------------
# 區塊：Ban Pick（總 Ban / 各分路 Ban）
# ------------------------------
def page_ban_pick(db):
    st.subheader("⛔ Ban Pick")
    mode = st.radio("顯示模式", ["總 Ban", "各分路 Ban"], horizontal=True)
    st.markdown("### Ban")

    if mode == "總 Ban":
        st.caption("以空白分隔輸入名字")
        s = st.text_input("新增或移除（總 Ban）", "")
        if st.button("套用（總 Ban）", use_container_width=True):
            names = [x for x in s.split() if x]
            # 覆蓋（你可改成合併）
            db["bans"]["total"] = names
            save_db(db)
            st.success("已更新 總 Ban")
        show_ban_list(db["bans"]["total"])

    else:
        lane = st.selectbox("選擇分路", LANES_ALL, index=0)
        s = st.text_input(f"新增或移除（{lane}）", "")
        if st.button("套用（各分路 Ban）", use_container_width=True):
            names = [x for x in s.split() if x]
            db["bans"]["lanes"][lane] = names
            save_db(db)
            st.success(f"已更新 {lane} Ban")
        show_ban_list(db["bans"]["lanes"].get(lane, []))

def show_ban_list(names: List[str]):
    if not names:
        st.info("目前空白")
        return
    cols = st.columns(8)
    for i, n in enumerate(names[:32]):
        with cols[i % 8]:
            p = find_hero_image(n)
            if p: st.image(p, caption=n, use_container_width=True)
            else: st.markdown(f"**{n}**")

# ------------------------------
# 區塊：英雄庫（職業 + 路線 + 連動的 T 度）
# ------------------------------
def page_gallery(db):
    st.subheader("🖼️ 英雄庫")
    col1, col2, col3 = st.columns(3)
    with col1:
        role = st.selectbox("職業篩選", ["（全部）"] + ROLES_ALL)
    with col2:
        lane = st.selectbox("路線篩選", ["（全部）"] + LANES_ALL)
    with col3:
        # T 度篩選（跟隨前兩個條件）
        tier = st.selectbox("T 度篩選", ["（全部）"] + TIERS_ALL)

    # 篩選
    result = []
    for name, info in db["heroes"].items():
        if role != "（全部）" and role not in info.get("roles", []):
            continue
        if lane != "（全部）" and lane not in info.get("lanes", []):
            continue
        if tier != "（全部）" and tier != info.get("tier", ""):
            continue
        result.append(name)

    st.caption(f"共 {len(result)} 位")
    # 縮圖設定
    sz = st.slider("縮圖大小", 48, 128, 64)
    per_row = st.slider("每列數量", 4, 10, 7)

    if not result:
        st.info("沒有符合的英雄")
    else:
        rows = (len(result) + per_row - 1) // per_row
        for r in range(rows):
            cols = st.columns(per_row)
            for c in range(per_row):
                idx = r * per_row + c
                if idx >= len(result): break
                n = result[idx]
                with cols[c]:
                    p = find_hero_image(n)
                    if p: st.image(p, width=sz, caption=n)
                    else: st.markdown(f"**{n}**")

# ------------------------------
# 區塊：Tier 排行（僅顯示；新增英雄即反映）
# ------------------------------
def page_tier(db):
    st.subheader("⚔️ Tier 排行")
    tiers = {t: [] for t in TIERS_ALL}
    for n, info in db["heroes"].items():
        t = info.get("tier", "T1")
        tiers.setdefault(t, [])
        tiers[t].append(n)

    for t in TIERS_ALL:
        st.markdown(f"### {t}")
        names = sorted(tiers.get(t, []))
        if not names:
            st.write("（無）")
            continue
        cols = st.columns(8)
        for i, n in enumerate(names):
            with cols[i % 8]:
                p = find_hero_image(n)
                if p: st.image(p, caption=n, use_container_width=True)
                else: st.markdown(f"**{n}**")

# ------------------------------
# 主程式
# ------------------------------
def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)

    db = load_db()

    tabs = st.tabs(["🔍 查詢 / 編輯", "➕ 新增英雄", "📦 體系陣容", "⛔ Ban Pick", "🖼️ 英雄庫", "⚔️ Tier 排行"])
    with tabs[0]:
        page_search_edit(db)
    with tabs[1]:
        page_add_hero(db)
    with tabs[2]:
        page_team_comp(db)
    with tabs[3]:
        page_ban_pick(db)
    with tabs[4]:
        page_gallery(db)
    with tabs[5]:
        page_tier(db)

if __name__ == "__main__":
    main()
