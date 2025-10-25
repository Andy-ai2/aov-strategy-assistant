# app.py
# AOV 英雄戰略助手（穩定版）
# - 修復 KeyError: db["heroes"]
# - 圖庫牆可點圖片直接進入編輯
# - 友善搜尋、向後相容資料結構、佔位圖保底

from __future__ import annotations
import os, json, unicodedata, urllib.parse
from typing import Dict, Any, List
from PIL import Image
import streamlit as st

# ========== 基本設定 ==========
DB_PATH = os.environ.get("AOV_DB_PATH", "db.json")
IMAGE_DIR = os.environ.get("AOV_IMAGE_DIR", "hero_images")
PLACEHOLDER = os.path.join(IMAGE_DIR, "_placeholder.png")

# ========== 資料層：載入 / 儲存 / 正規化 ==========
def load_db(path: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    # 舊版相容：若最外層就是 list[hero]，轉為 {"heroes": {name: hero}}
    if isinstance(data, list):
        data = {"heroes": {h.get("name", "").strip(): h for h in data if h.get("name")}}

    # 保底：一定要有 heroes 且為 dict
    if "heroes" not in data or not isinstance(data["heroes"], dict):
        data["heroes"] = {}

    return data

def save_db(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ========== 工具：正規化搜尋 ==========
def norm(s: str) -> str:
    s = s or ""
    s = unicodedata.normalize("NFKC", s)
    return s.lower().replace(" ", "")

# ========== 圖片工具 ==========
def ensure_image_path(p: str | None) -> str:
    """回傳可用圖片路徑：優先 hero 指定，其次 name.jpg/png，否則佔位圖。"""
    if p and os.path.exists(p):
        return p
    # 嘗試用常見副檔名
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if os.path.exists(os.path.join(IMAGE_DIR, f"{p}{ext}" if p and os.path.splitext(p)[1]=="" else "")):
            return os.path.join(IMAGE_DIR, f"{p}{ext}")
    # 如果 p 是名字而不是檔名
    if p:
        name = os.path.splitext(os.path.basename(p))[0]
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            cand = os.path.join(IMAGE_DIR, f"{name}{ext}")
            if os.path.exists(cand):
                return cand
    # 退而求其次：用 hero name 配對
    # 這一步在渲染時會傳入 hero["name"]
    return PLACEHOLDER if os.path.exists(PLACEHOLDER) else ""

def hero_image(hero: Dict[str, Any]) -> str:
    # 1) 調 hero["image"]；2) hero["name"].*
    p = hero.get("image")
    if p and os.path.exists(p):
        return p
    name = hero.get("name", "")
    # 嘗試 name 副檔名
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        cand = os.path.join(IMAGE_DIR, f"{name}{ext}")
        if os.path.exists(cand):
            return cand
    return PLACEHOLDER if os.path.exists(PLACEHOLDER) else ""

# ========== Domain API ==========
def get_heroes(db: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return (db or {}).get("heroes") or {}

def get_hero_names(db: Dict[str, Any]) -> List[str]:
    return sorted(get_heroes(db).keys())

def upsert_hero(db: Dict[str, Any], hero: Dict[str, Any]) -> None:
    name = hero.get("name", "").strip()
    if not name:
        raise ValueError("英雄需要有名稱")
    db.setdefault("heroes", {})
    db["heroes"][name] = hero

def delete_hero(db: Dict[str, Any], name: str) -> None:
    heroes = get_heroes(db)
    if name in heroes:
        del heroes[name]

# ========== 介面：共用 ==========
def goto(page: str, hero_name: str | None = None):
    qp = {"page": page}
    if hero_name:
        qp["hero"] = hero_name
    st.query_params.clear()
    st.query_params.update(qp)

def current_page() -> str:
    return st.query_params.get("page", "gallery")

def current_hero_param() -> str | None:
    return st.query_params.get("hero")

# ========== 頁面：圖庫牆 ==========
def page_gallery(db: Dict[str, Any]):
    st.header("圖庫牆（點圖即編輯）")
    heroes = get_heroes(db)
    names = sorted(heroes.keys())

    if not names:
        st.warning("目前資料庫沒有任何英雄，請先到「新增英雄」頁建立。")
        return

    cols = st.slider("每列顯示張數", 4, 10, 6)
    grid = st.columns(cols, gap="small")

    # 以 HTML <a> 包裹 <img>：點圖片即跳到 ?page=edit&hero=名字
    for i, name in enumerate(names):
        hero = heroes[name]
        img_path = hero_image(hero) or PLACEHOLDER
        with grid[i % cols]:
            encoded = urllib.parse.quote(name)
            href = f"?page=edit&hero={encoded}"
            if os.path.exists(img_path):
                st.markdown(
                    f"""
                    <div style="text-align:center">
                      <a href="{href}">
                        <img src="app://{img_path}" style="width:100%; border-radius:14px;">
                      </a>
                      <div style="margin-top:6px; font-weight:600">{name}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.link_button(name, href)

# ========== 頁面：查詢/編輯 ==========
def page_search_edit(db: Dict[str, Any]):
    st.header("查詢 / 編輯")
    heroes = get_heroes(db)
    names = sorted(heroes.keys())

    # 若從圖庫牆點進來，直接鎖定該英雄
    incoming = current_hero_param()

    q = st.text_input("搜尋英雄（空白顯示全部）", value=incoming or "")
    nq = norm(q)
    if not q:
        candidates = names
    else:
        candidates = [n for n in names if q.lower() in n.lower() or nq in norm(n)]

    if not candidates:
        st.info("找不到相符的英雄。")
        return

    sel = st.selectbox("選擇英雄", candidates, index=candidates.index(incoming) if incoming in candidates else 0)
    render_edit_form(db, sel)

def render_edit_form(db: Dict[str, Any], name: str):
    st.subheader(f"編輯：{name}")
    heroes = get_heroes(db)
    hero = dict(heroes.get(name, {"name": name}))

    cols = st.columns(2)
    with cols[0]:
        new_name = st.text_input("名稱", value=hero.get("name", name))
        lane = st.text_input("主要路線（例：中路/凱薩/打野）", value=hero.get("lane", ""))
        tier = st.text_input("強度分層（例：T0/T1）", value=hero.get("tier", ""))
        special = st.checkbox("是否特殊英雄（顯示在特殊區）", value=bool(hero.get("special", False)))
        notes = st.text_area("備註 / 策略", value=hero.get("notes", ""), height=120)

    with cols[1]:
        img_hint = st.caption(f"圖片路徑（相對專案）預設會找 {IMAGE_DIR}/<名稱>.png|jpg|jpeg|webp")
        image_path = st.text_input("自訂圖片檔路徑（可留空）", value=hero.get("image", ""))
        preview_path = hero_image({"name": new_name, "image": image_path})
        if preview_path and os.path.exists(preview_path):
            st.image(preview_path, use_column_width=True, caption="預覽")
        else:
            st.warning("找不到圖片，將使用佔位圖（或請放圖到 hero_images/）")

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        if st.button("💾 儲存", type="primary"):
            if not new_name.strip():
                st.error("名稱不可為空")
            else:
                updated = {
                    "name": new_name.strip(),
                    "lane": lane.strip(),
                    "tier": tier.strip(),
                    "special": special,
                    "notes": notes.strip(),
                    "image": image_path.strip(),
                }
                # 名稱變更：需處理舊索引
                if new_name != name and name in db["heroes"]:
                    del db["heroes"][name]
                upsert_hero(db, updated)
                save_db(DB_PATH, db)
                st.success("已儲存")
                goto("edit", updated["name"])
                st.rerun()
    with c2:
        if st.button("🗑️ 刪除", help="不可復原，請小心"):
            delete_hero(db, name)
            save_db(DB_PATH, db)
            st.warning(f"已刪除 {name}")
            goto("search")
            st.rerun()
    with c3:
        if st.button("↩️ 回圖庫牆"):
            goto("gallery")
            st.rerun()

# ========== 頁面：新增 ==========
def page_add(db: Dict[str, Any]):
    st.header("新增英雄")
    name = st.text_input("名稱")
    lane = st.text_input("主要路線")
    tier = st.text_input("強度分層（T0/T1…）")
    special = st.checkbox("是否特殊英雄")
    notes = st.text_area("備註 / 策略", height=100)
    image_path = st.text_input("圖片路徑（可留空）")

    if st.button("新增", type="primary"):
        if not name.strip():
            st.error("名稱不可為空")
            return
        hero = {
            "name": name.strip(),
            "lane": lane.strip(),
            "tier": tier.strip(),
            "special": special,
            "notes": notes.strip(),
            "image": image_path.strip(),
        }
        upsert_hero(db, hero)
        save_db(DB_PATH, db)
        st.success("已新增")
        goto("edit", name.strip())
        st.rerun()

# ========== 頁面：資料體檢 ==========
def page_health(db: Dict[str, Any]):
    st.header("資料體檢 / 修復")
    heroes = get_heroes(db)
    issues = []

    # 缺名或重複
    seen = set()
    for n, h in list(heroes.items()):
        if not n.strip():
            issues.append(f"發現空白名稱的條目：{h}")
        if n in seen:
            issues.append(f"重複名稱：{n}")
        seen.add(n)

    # 圖片缺失
    missing_imgs = [n for n, h in heroes.items() if not hero_image(h)]

    if not heroes:
        st.info("目前沒有任何英雄資料。")
    st.write(f"共有 {len(heroes)} 位英雄。")
    if issues:
        st.error("問題：")
        for i in issues:
            st.write("- " + i)
    else:
        st.success("未發現名稱相關問題。")

    if missing_imgs:
        st.warning(f"{len(missing_imgs)} 位英雄缺圖片（或佔位圖）。")
        st.write(", ".join(missing_imgs))
    else:
        st.success("所有英雄皆可取得圖片預覽（或已使用佔位圖）。")

    if st.button("修復：建立基本結構並去除空名"):
        # 建立結構
        db.setdefault("heroes", {})
        # 移除空名
        for n in list(db["heroes"].keys()):
            if not n.strip():
                del db["heroes"][n]
        save_db(DB_PATH, db)
        st.success("已修復結構，並移除空名。")

# ========== 主程式 ==========
def main():
    st.set_page_config(page_title="AOV 英雄戰略助手", page_icon="🎯", layout="wide")
    os.makedirs(IMAGE_DIR, exist_ok=True)

    db = load_db(DB_PATH)

    with st.sidebar:
        st.title("🎯 AOV 英雄戰略助手")
        st.caption("點選頁面或在圖庫牆直接點英雄圖片進入編輯")
        page = st.radio(
            "頁面",
            options=["圖庫牆", "查詢/編輯", "新增英雄", "資料體檢"],
            index=["gallery", "search", "add", "health"].index(current_page()) if current_page() in ["gallery","search","add","health"] else 0,
            key="sidebar_page",
        )
        mapping = {"圖庫牆": "gallery", "查詢/編輯": "search", "新增英雄": "add", "資料體檢": "health"}
        goto(mapping[page])

        st.divider()
        st.write("📁 圖片資料夾：", IMAGE_DIR)
        if PLACEHOLDER and os.path.exists(PLACEHOLDER):
            st.image(PLACEHOLDER, caption="目前佔位圖", use_column_width=True)

    # 根據 query params 渲染
    p = current_page()
    if p == "gallery":
        page_gallery(db)
    elif p == "search" or p == "edit":  # edit 仍用同一頁呈現表單
        page_search_edit(db)
    elif p == "add":
        page_add(db)
    elif p == "health":
        page_health(db)
    else:
        page_gallery(db)

if __name__ == "__main__":
    main()
