# -*- coding: utf-8 -*-
"""整卷难度量化与解析关键批注 —— 计算与排版引擎。

用法示例：
    python src/build.py --demo
    python src/build.py --papers papers
    python src/build.py --papers papers mock1 mock2
    python src/build.py --list --papers papers

职责：
    1. 从 <papers>/data_<id>.py 读取试卷评分数据（PAPER 字典，见 docs/schema.md）；
    2. 复算每步负荷 s_i、关键路径 Σe，以及 H / C / T / D 与规则档位；
    3. 把量化块替换进 <body>/<id>.md 的 @@Q<no> 占位符，生成教师阅读版 Markdown；
    4. 导出机器可读产出：评分明细.json / 逐小问评分.csv / 步骤类型词典.json；
    5. 若存在跨卷配置，调用 cross_paper 生成跨卷难度对比报告。

设计原则：
    - 只依赖标准库；
    - 所有分数可复算：中间量保留精度，仅在公式标明 round 处取整；
    - 不静默改题：核验状态与待复核项一律照实输出；
    - 不伪造权重：小问分值未给出时不做分值加权。
"""
import argparse
import csv
import importlib
import json
import math
import os
import re
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_types  # noqa: E402

TOOL_VERSION = "0.1.0"

F0, F1 = Fraction(0), Fraction(1)

# 六维权重（与 docs/scoring.md 保持一致，改动属不兼容变更）
#
# 用 Fraction 而非 float：权重与维度取值都是有限小数，s_i 与 Σe 因此都是**精确的有理数**。
# 这一点很重要——若用 float，100*s 在 .5 边界上会因浮点表示而漂移
# （例如 100*0.575 得到 57.49999999999999，四舍五入得 57 而非 58），
# 使「可复算」这个核心承诺失效。
DIMW = {"K": Fraction(20, 100), "R": Fraction(25, 100), "A": Fraction(15, 100),
        "V": Fraction(15, 100), "P": Fraction(15, 100), "I": Fraction(10, 100)}
DIMN = {"K": "K知识识别", "R": "R条件推理", "A": "A运算负荷",
        "V": "V表征转换", "P": "P参数边界", "I": "I知识交汇"}
DIMORDER = ("K", "R", "A", "V", "P", "I")

BAND_LO, BAND_HI = 34, 64            # 0—34 基础｜35—64 中档｜65—100 难题
W_H, W_C, W_T = Fraction(45, 100), Fraction(35, 100), Fraction(20, 100)

# 个人信息体检：命中即告警（不阻断构建，但会写入日志）
PRIVACY_PATTERNS = [
    (re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s\"']+"), "Windows 用户目录绝对路径"),
    (re.compile(r"/(?:Users|home)/[^/\s\"']+"), "Unix 用户目录绝对路径"),
]


def half_up(x, eps=1e-9):
    """非负数四舍五入（round half up），返回 int。

    口径要求「统一采用非负数四舍五入」，而 Python 内置 round() 是**银行家舍入**
    （round(2.5)==2），两者在 .5 边界上结果不同。本函数显式实现 half-up：

    - 传入 Fraction 时精确判断（不做任何浮点近似）；
    - 传入 float 时把「与 .5 相差小于 eps」视为恰好 .5，以吸收 exp/log 等
      超越函数的浮点误差。
    """
    if isinstance(x, Fraction):
        p, q = x.numerator, x.denominator
        return (2 * p + q) // (2 * q)
    fl = math.floor(x)
    frac = x - fl
    if abs(frac - 0.5) < eps:
        return int(fl) + 1
    return int(fl) + 1 if frac > 0.5 else int(fl)


# ---------- 基础计算 ----------
def s_of(dim):
    """步骤负荷 s_i（精确有理数，不取整）。"""
    total = F0
    for k in DIMORDER:
        total += DIMW[k] * dim[k] / 2
    return total


def r_of(steps):
    """同型机械重复折扣：同组第 1 次 r=1，第 2 次 0.7，第 3 次及以后 0.4。

    仅当步骤显式标注同一个 rep 组号时才折扣；不同推理动作不折扣。
    """
    seen, out = {}, []
    for st in steps:
        g = st.get("rep")
        if not g:
            out.append(F1)
            continue
        n = seen.get(g, 0) + 1
        seen[g] = n
        out.append(F1 if n == 1 else (Fraction(7, 10) if n == 2 else Fraction(4, 10)))
    return out


def _d_of(j):
    """路径上第 j 步的距离因子：min(1 + 0.1*(j-1), 1.3)，精确有理数。"""
    return min(F1 + Fraction(j - 1, 10), Fraction(13, 10))


def longest_path(steps, rs):
    """依赖 DAG 上有效负荷之和最大的路径。

    路径上第 j 步：e_j = s_j * r_j * d_j，d_j = min(1 + 0.1*(j-1), 1.3)。
    返回 (Σe, 路径 ID 列表, 逐步明细)。Σe 为精确有理数。
    """
    idx = {st["id"]: i for i, st in enumerate(steps)}
    n = len(steps)
    W = [s_of(st["dim"]) * rs[i] for i, st in enumerate(steps)]
    # best[i][L]：以第 i 步结尾、路径长度为 L 的最大有效负荷
    best = [dict() for _ in range(n)]
    prev = [dict() for _ in range(n)]
    for i in range(n):
        best[i][1] = W[i]
    for i in range(n):
        deps = [idx[d] for d in steps[i].get("dep", []) if d in idx]
        for p in deps:
            for ln, val in best[p].items():
                if ln + 1 > 64:
                    continue
                cand = val + W[i] * _d_of(ln + 1)
                if cand > best[i].get(ln + 1, F0 - 1):
                    best[i][ln + 1] = cand
                    prev[i][ln + 1] = p
    bi, bl, bv = None, None, None
    for i in range(n):
        for ln, val in best[i].items():
            if bv is None or val > bv:
                bi, bl, bv = i, ln, val
    path, cur, ln = [], bi, bl
    while cur is not None:
        path.append(steps[cur]["id"])
        cur = prev[cur].get(ln)
        ln -= 1
    path.reverse()
    detail = []
    for j, sid in enumerate(path, start=1):
        st = steps[idx[sid]]
        s = s_of(st["dim"])
        detail.append({"j": j, "id": sid, "s": round(float(s), 4),
                       "r": float(rs[idx[sid]]), "d": round(float(_d_of(j)), 2),
                       "e": round(float(s * rs[idx[sid]] * _d_of(j)), 4)})
    return bv, path, detail


def calc(unit):
    """复算一个分析单元的 H / C / T / D 与档位。"""
    steps = unit["steps"]
    if not steps:
        raise ValueError("单元 {} 没有步骤，无法评分".format(unit.get("id")))
    rs = r_of(steps)
    for i, st in enumerate(steps):
        st["_s"] = s_of(st["dim"])
        st["_r"] = rs[i]
    H = half_up(100 * max(st["_s"] for st in steps))
    se, path, detail = longest_path(steps, rs)
    C = half_up(100 * (1 - math.exp(-float(se) / 3.0)))
    tv = unit["T"]
    if len(tv) != 5:
        raise ValueError("单元 {} 的 T 必须为 5 项（各自 0/1/2）".format(unit.get("id")))
    T = 10 * sum(tv)
    D = half_up(W_H * H + W_C * C + W_T * T)
    band = "基础" if D <= BAND_LO else ("中档" if D <= BAND_HI else "难题")
    return {"H": H, "C": C, "T": T, "D": D, "band": band, "se": round(float(se), 6),
            "path": path, "path_detail": detail}


def peak_six(unit):
    return {k: max(st["dim"][k] for st in unit["steps"]) for k in DIMORDER}


# ---------- 统计 ----------
def stats(vals):
    if not vals:
        return {}
    n = len(vals)
    m = sum(vals) / n
    srt = sorted(vals)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / n)
    return {"n": n, "mean": round(m, 2), "median": med, "max": max(vals),
            "min": min(vals), "sd": round(sd, 2)}


def analyse(paper):
    """对整卷做统计。待核验单元保留在 units 中，但不计入有效均值。"""
    units = []
    for q in paper["questions"]:
        for u in q["units"]:
            u["_q"] = q
            u["_res"] = calc(u)
            u["_peak"] = peak_six(u)
            units.append(u)
    valid = [u for u in units if u["status"] != "待核验"]
    if not valid:
        raise ValueError("试卷 {} 没有有效分析单元".format(paper.get("pid")))
    st = stats([u["_res"]["D"] for u in valid])
    known = [u for u in valid if u.get("m")]
    wsum = sum(u["m"] for u in known)
    wD = round(sum(u["m"] * u["_res"]["D"] for u in known) / wsum, 2) if wsum else None
    bands = {"基础": [], "中档": [], "难题": []}
    for u in valid:
        bands[u["_res"]["band"]].append(u)
    dist = {}
    for b, us in bands.items():
        dist[b] = {"count": len(us),
                   "cnt_pct": round(100.0 * len(us) / len(valid), 1),
                   "m": sum(u["m"] for u in us if u.get("m"))}
    six = {k: round(sum(u["_peak"][k] for u in valid) / len(valid), 2) for k in DIMORDER}
    return {"units": units, "valid": valid, "st": st, "wD": wD, "wsum": wsum,
            "allknown": all(u.get("m") for u in valid), "dist": dist, "six": six,
            "kn_m": sum(u["m"] for u in valid if u.get("m")),
            "H": round(sum(u["_res"]["H"] for u in valid) / len(valid), 1),
            "C": round(sum(u["_res"]["C"] for u in valid) / len(valid), 1),
            "T": round(sum(u["_res"]["T"] for u in valid) / len(valid), 1)}


# ---------- 排版 ----------
def _esc(x):
    return str(x).replace("|", "/").replace("\n", " ")


def qsum_table(paper, A):
    L = ["| 题号 | 小问 | 分值 | 主模块 | 核心考点 | H | C | T | D | 规则档位 | B | 必做分支 | 关键卡点 | 核验状态 | 复核 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for u in A["units"]:
        r = u["_res"]
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            u["q"], u["sub_label"], u.get("m") or "未定", u["module"], _esc(u["points"]),
            r["H"], r["C"], r["T"], r["D"], r["band"], u["B"], u.get("branches", 0),
            _esc(u["barrier"]), u["status"], _esc(u.get("review") or "—")))
    return "\n".join(L)


def step_table(unit):
    L = ["| 步骤 | 数学动作 | 前置 | 类型 | K | R | A | V | P | I | s | 依据 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for st in unit["steps"]:
        d = st["dim"]
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            st["id"], _esc(st["act"]), ",".join(st.get("dep", [])) or "—", st["type"],
            d["K"], d["R"], d["A"], d["V"], d["P"], d["I"],
            round(float(st["_s"]), 4), _esc(st["ev"])))
    return "\n".join(L)


def question_block(paper, q, A):
    """生成某一题（可含多个小问）的量化块，用于替换 @@Q<no> 占位符。"""
    us = [u for u in A["units"] if u["q"] == q["no"]]
    L = []
    for u in us:
        r = u["_res"]
        L.append("**{}　量化**　D={}（{}）｜H={}　C={}　T={}　B={}"
                 "｜必做分支 {}｜核验：{}｜复核：{}".format(
                     u["sub_label"], r["D"], r["band"], r["H"], r["C"], r["T"], u["B"],
                     u.get("branches", 0), u["status"], u.get("review") or "—"))
        L.append("")
        L.append("主模块：{}　核心考点：{}　关键卡点：{}".format(u["module"], u["points"], u["barrier"]))
        L.append("")
        L.append("<details><summary>步骤六维明细（{} 步，路径 {}，Σe={}）</summary>".format(
            len(u["steps"]), "→".join(r["path"]) or "—", r["se"]))
        L.append("")
        L.append(step_table(u))
        L.append("")
        L.append("关键路径复算：{}".format("；".join(
            "第{}步 {}：s={} r={} d={} e={}".format(
                x["j"], x["id"], x["s"], x["r"], x["d"], x["e"]) for x in r["path_detail"])))
        L.append("")
        L.append("T 五项（入口隐蔽／无效入口／发现延迟／回退范围／端点遗漏）：{}".format(
            "／".join(str(x) for x in u["T"]) + "　" + "；".join(u["T_ev"])))
        L.append("")
        L.append("B 依据：{}".format(u.get("B_ev") or "—"))
        L.append("")
        L.append("</details>")
        L.append("")
    return "\n".join(L)


def overview(paper, A):
    st = A["st"]
    L = ["| 指标 | 数值 | 口径 |", "|---|---|---|"]
    L.append("| 有效小问数 | {} | 分析单元总数 {}，其中待核验 {} 不计入 |".format(
        st["n"], len(A["units"]), len(A["units"]) - st["n"]))
    L.append("| D 均值（小问等权） | {} | 有效小问等权平均 |".format(st["mean"]))
    L.append("| D 中位数 | {} | — |".format(st["median"]))
    L.append("| D 最高值 | {} | — |".format(st["max"]))
    L.append("| D 标准差 | {} | 有效小问等权总体标准差 |".format(st["sd"]))
    if A["wD"] is not None:
        cov = round(100.0 * A["wsum"] / paper["total"], 1) if paper.get("total") else 0
        L.append("| 分值加权规则指数 | {} | 分母 {} 分（仅含分值明确的 {} 个小问，分值覆盖率 {}%） |".format(
            A["wD"], A["wsum"], sum(1 for u in A["valid"] if u.get("m")), cov))
        if not A["allknown"]:
            L.append("| └ 说明 | 部分小问分值未知，按规则不伪造权重 | 未计入分值加权的分子与分母 |")
    else:
        L.append("| 分值加权规则指数 | 无法给出 | 全部分值未知，按规则不伪造权重 |")
    L.append("| H／C／T 均值 | {}／{}／{} | 有效小问等权 |".format(A["H"], A["C"], A["T"]))
    L.append("")
    L.append("难度分布（有效小问）：")
    L.append("")
    L.append("| 档位 | 小问数 | 小问占比 | 已知分值合计 |")
    L.append("|---|---|---|---|")
    for b in ("基础", "中档", "难题"):
        d = A["dist"][b]
        L.append("| {} | {} | {}% | {} |".format(b, d["count"], d["cnt_pct"], d["m"] or "—"))
    L.append("")
    L.append("六维峰值画像（各小问步骤取最大值后在有效小问间平均）：")
    L.append("")
    L.append("| " + " | ".join(DIMN.values()) + " |")
    L.append("|" + "---|" * 6)
    L.append("| " + " | ".join(str(A["six"][k]) for k in DIMORDER) + " |")
    return "\n".join(L)


def module_stats(paper, A):
    L = ["| 主模块 | 小问数 | 已知分值 | D 均值 | 最高 D |", "|---|---|---|---|---|"]
    agg = {}
    for u in A["valid"]:
        a = agg.setdefault(u["module"], {"n": 0, "m": 0, "ds": []})
        a["n"] += 1
        a["m"] += u["m"] or 0
        a["ds"].append(u["_res"]["D"])
    for k, v in sorted(agg.items(), key=lambda x: -sum(x[1]["ds"]) / len(x[1]["ds"])):
        L.append("| {} | {} | {} | {} | {} |".format(
            k, v["n"], v["m"] or "—", round(sum(v["ds"]) / len(v["ds"]), 1), max(v["ds"])))
    return "\n".join(L)


def seq_line(A):
    return "　".join("{}:{}".format(u["sub_label"], u["_res"]["D"]) for u in A["units"])


def appendix(paper, A):
    L = ["### 六维评分证据全表（附录）", ""]
    for u in A["units"]:
        r = u["_res"]
        L.append("**{}**　分值：{}　状态：{}　D={}（{}）　B={}　必做分支={}".format(
            u["sub_label"], u.get("m") or "未定", u["status"], r["D"], r["band"],
            u["B"], u.get("branches", 0)))
        L.append("")
        L.append(step_table(u))
        L.append("")
    return "\n".join(L)


# ---------- 输出 ----------
def write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def build_paper(pid, papers_dir, body_dir, out_dir):
    """构建一份试卷，返回 (paper, A, 输出路径)。"""
    mod = _import_data(pid, papers_dir)
    paper = mod.PAPER
    paper.setdefault("pid", pid)
    paper.setdefault("file", "{}-教师阅读版.md".format(pid))
    paper.setdefault("source", "（未填写）")
    paper.setdefault("meta", [])
    paper.setdefault("review_note", "（未填写）")
    paper.setdefault("conflict_table", "| 说明 |\n|---|\n| 无 |")
    paper.setdefault("questions", [])

    A = analyse(paper)
    n_units = len(A["units"])
    expect = paper.get("expect_units")
    if expect is not None and n_units != expect:
        raise AssertionError(
            "{} 分析单元数 {} != 声明的 expect_units {}。"
            "请核对 data_{}.py（未为凑数硬补小问）。".format(pid, n_units, expect, pid))

    body_path = _find_body(pid, body_dir, papers_dir)
    with open(body_path, encoding="utf-8") as f:
        body = f.read()
    missing = [q["no"] for q in paper["questions"] if "@@Q{}".format(q["no"]) not in body]
    if missing:
        raise AssertionError(
            "{} 的正文缺少占位符：{}".format(body_path, "、".join("@@Q{}".format(x) for x in missing)))
    for q in paper["questions"]:
        body = body.replace("@@Q{}".format(q["no"]), question_block(paper, q, A))

    head = ["# {}｜教师阅读版（整卷难度量化与解析关键批注）".format(paper["title"]), "",
            "> 生成规则：六维—H/C/T 规则分，**非实测难度**。"
            "完整评分证据见 `评分明细.json`，口径见 `docs/scoring.md`。", "",
            "## 一、口径与材料核验", ""]
    head.append("| 项 | 内容 |")
    head.append("|---|---|")
    for k, v in paper["meta"]:
        head.append("| {} | {} |".format(k, _esc(v)))
    head.append("")
    head.append("## 二、整卷量化概览")
    head.append("")
    head.append(overview(paper, A))
    head.append("")
    head.append("### 主模块比较")
    head.append("")
    head.append(module_stats(paper, A))
    head.append("")
    head.append("### 题序难度变化（D 值，按原卷顺序）")
    head.append("")
    head.append(seq_line(A))
    head.append("")
    head.append("## 三、逐小问量化总表")
    head.append("")
    head.append(qsum_table(paper, A))
    head.append("")
    # 第四节（正文）标题由 <id>.md 自带，此处不重复输出
    tail = ["", "## 六、复核清单与评分附录", "", paper["review_note"], "",
            "### 待复核／版本冲突清单", "", paper["conflict_table"], "",
            appendix(paper, A)]
    out_path = os.path.join(out_dir, paper["file"])
    # 注意 head 末元素为空串：此处额外补一个换行，确保第三节表格与第四节标题之间有空行
    write(out_path, "\n".join(head) + "\n" + body + "\n".join(tail))
    return paper, A, out_path


# ---------- 数据装载 ----------
def _papers_dir_on_path(papers_dir):
    ap = os.path.abspath(papers_dir)
    if ap not in sys.path:
        sys.path.insert(0, ap)


def _import_data(pid, papers_dir):
    _papers_dir_on_path(papers_dir)
    name = "data_" + pid
    if name in sys.modules:
        return importlib.reload(sys.modules[name])
    try:
        return importlib.import_module(name)
    except ImportError as e:
        raise SystemExit(
            "找不到 {}/data_{}.py（{}）。请确认文件存在且为合法 Python 模块。".format(
                papers_dir, pid, e))


def discover(papers_dir):
    """自动发现 <papers_dir>/data_*.py，返回按文件名排序的 id 列表。"""
    if not os.path.isdir(papers_dir):
        return []
    ids = []
    for fn in sorted(os.listdir(papers_dir)):
        m = re.fullmatch(r"data_(.+)\.py", fn)
        if m and not m.group(1).startswith("_"):
            ids.append(m.group(1))
    return ids


def _find_body(pid, body_dir, papers_dir):
    for d in (body_dir, papers_dir):
        p = os.path.join(d, pid + ".md")
        if os.path.isfile(p):
            return p
    raise SystemExit(
        "找不到正文 {}.md（已查找 {} 与 {}）。正文需含 @@Q<no> 占位符，见 docs/schema.md。".format(
            pid, body_dir, papers_dir))


# ---------- 隐私体检 ----------
def privacy_scan(papers_res):
    hits = []
    for paper, A, _ in papers_res:
        blob = json.dumps({"meta": paper.get("meta"), "source": paper.get("source"),
                           "review_note": paper.get("review_note")}, ensure_ascii=False)
        for pat, label in PRIVACY_PATTERNS:
            for m in pat.finditer(blob):
                hits.append("{}：{}「{}」".format(paper["pid"], label, m.group(0)))
        for u in A["units"]:
            for st in u["steps"]:
                for pat, label in PRIVACY_PATTERNS:
                    if pat.search(str(st.get("act", "")) + str(st.get("ev", ""))):
                        hits.append("{}：步骤 {} 含{}".format(paper["pid"], st["id"], label))
    return hits


# ---------- 机器可读产出 ----------
def dump_machine(papers_res, out_dir, cross_meta=None):
    detail = {"工具版本": TOOL_VERSION,
              "词典版本": step_types.VERSION,
              "取整约定": "非负数四舍五入（round half up）；s/e 用精确有理数（fractions.Fraction）计算，"
                        "仅在公式标明取整处取整",
              "档位阈值": {"基础": "0—{}".format(BAND_LO),
                        "中档": "{}—{}".format(BAND_LO + 1, BAND_HI),
                        "难题": "{}—100".format(BAND_HI + 1)},
              "D 权重": {"H": float(W_H), "C": float(W_C), "T": float(W_T)},
              "试卷": []}
    csv_rows = []
    for paper, A, _ in papers_res:
        pj = {"试卷ID": paper["pid"], "标题": paper["title"],
              "来源定位": paper["source"], "满分": paper.get("total"),
              "分析单元数": len(A["units"]), "有效单元数": A["st"]["n"],
              "题目": []}
        for q in paper["questions"]:
            qj = {"原题号": q["no"], "题型": q.get("kind", ""), "小问": []}
            for u in [x for x in A["units"] if x["q"] == q["no"]]:
                r = u["_res"]
                qj["小问"].append({
                    "小问ID": u["id"], "标签": u["sub_label"], "分值": u.get("m"),
                    "主模块": u["module"], "核心考点": u["points"],
                    "核验状态": u["status"], "复核标记": u.get("review") or "",
                    "步骤": [{"步骤": s["id"], "阶段": s.get("stage", ""),
                              "动作": s["act"], "依赖": s.get("dep", []),
                              "类型ID": s["type"], "六维": s["dim"], "逐维证据": s["ev"],
                              "s": round(float(s["_s"]), 4), "r": float(s["_r"]),
                              "同型重复组": s.get("rep")} for s in u["steps"]],
                    "关键路径": {"路径": r["path"], "各步": r["path_detail"],
                                 "Σe": r["se"], "C": r["C"]},
                    "T五项": u["T"], "T依据": u["T_ev"], "H": r["H"], "C": r["C"],
                    "T": r["T"], "D": r["D"], "档位": r["band"],
                    "B": u["B"], "B依据": u.get("B_ev", ""),
                    "必做分支数": u.get("branches", 0), "关键卡点": u["barrier"],
                    "批注数": u.get("ann", 0)})
            pj["题目"].append(qj)
        detail["试卷"].append(pj)
        for u in A["units"]:
            r = u["_res"]
            row = {"试卷ID": paper["pid"], "小问ID": u["id"], "原题号": u["q"],
                   "小问": u["sub_label"], "分值": u.get("m") or "",
                   "状态": u["status"], "H": r["H"], "C": r["C"], "T": r["T"], "D": r["D"],
                   "B": u["B"], "档位": r["band"]}
            for k in DIMORDER:
                row["六维峰值" + k] = u["_peak"][k]
            row["复核标记"] = u.get("review") or ""
            csv_rows.append(row)

    write(os.path.join(out_dir, "评分明细.json"),
          json.dumps(detail, ensure_ascii=False, indent=1))
    if csv_rows:
        with open(os.path.join(out_dir, "逐小问评分.csv"), "w",
                  encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)
    write(os.path.join(out_dir, "步骤类型词典.json"),
          json.dumps(step_types.as_dict(), ensure_ascii=False, indent=1))
    return detail


# ---------- 命令行 ----------
def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="build.py",
        description="整卷难度量化与解析关键批注 —— 计算与排版引擎")
    p.add_argument("ids", nargs="*", help="试卷 id（对应 data_<id>.py）；不填则处理全部")
    p.add_argument("--papers", default="papers", help="试卷数据目录（默认 papers）")
    p.add_argument("--body", default=None, help="正文 md 目录（默认与 --papers 相同）")
    p.add_argument("--out", default="out", help="产出目录（默认 out）")
    p.add_argument("--demo", action="store_true", help="使用内置自拟示例（examples/demo）")
    p.add_argument("--config", default=None, help="跨卷报告配置；默认 <papers>/papers.json")
    p.add_argument("--no-cross", action="store_true", help="不生成跨卷报告")
    p.add_argument("--list", action="store_true", help="只列出现到的试卷，不构建")
    return p.parse_args(argv)


def resolve_dirs(args):
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    if args.demo:
        papers_dir = os.path.join(root, "examples", "demo")
    else:
        papers_dir = args.papers if os.path.isabs(args.papers) else os.path.join(root, args.papers)
    body_dir = args.body or papers_dir
    if not os.path.isabs(body_dir):
        body_dir = os.path.join(root, body_dir)
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    return papers_dir, body_dir, out_dir


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    papers_dir, body_dir, out_dir = resolve_dirs(args)
    ids = args.ids or discover(papers_dir)

    if args.list:
        print("试卷目录：{}\n发现 {} 份：{}".format(
            papers_dir, len(ids), "、".join(ids) or "（无）"))
        return ""

    if not ids:
        raise SystemExit(
            "在 {} 未发现任何 data_*.py。请先放入试卷数据（见 papers/README.md），"
            "或运行 python src/build.py --demo 看示例。".format(papers_dir))

    os.makedirs(out_dir, exist_ok=True)
    res = [build_paper(i, papers_dir, body_dir, out_dir) for i in ids]
    dump_machine(res, out_dir)

    log = ["工具版本 {}　词典版本 {}".format(TOOL_VERSION, step_types.VERSION),
           "试卷目录 {}　正文目录 {}　产出目录 {}".format(papers_dir, body_dir, out_dir), ""]
    for paper, A, out_path in res:
        log.append("== {} ==".format(paper["pid"]))
        log.append("有效小问 {}　D均值 {}　中位数 {}　最高 {}　标准差 {}".format(
            A["st"]["n"], A["st"]["mean"], A["st"]["median"], A["st"]["max"], A["st"]["sd"]))
        log.append("分值加权 {}（分母 {} 分）".format(A["wD"], A["wsum"]))
        log.append("分布 {}".format({k: v["count"] for k, v in A["dist"].items()}))
        log.append("产出 {}".format(out_path))
        for u in A["units"]:
            log.append("  {} m={} H={} C={} T={} D={} {}".format(
                u["sub_label"], u.get("m"), u["_res"]["H"], u["_res"]["C"],
                u["_res"]["T"], u["_res"]["D"], u["_res"]["band"]))
        log.append("")

    hits = privacy_scan(res)
    if hits:
        log.append("== 隐私体检告警 ==")
        log.extend("  " + h for h in hits)
        log.append("")
    else:
        log.append("隐私体检：未发现用户目录绝对路径。")
        log.append("")

    # 跨卷报告（可选）
    cfg_path = args.config
    if cfg_path is None:
        cand = os.path.join(papers_dir, "papers.json")
        cfg_path = cand if os.path.isfile(cand) else None
    if cfg_path and not args.no_cross and os.path.isfile(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        import cross_paper
        msg = cross_paper.build(res, cfg, out_dir, papers_dir)
        log.append(msg)
        log.append("")
    elif cfg_path and not os.path.isfile(cfg_path) and args.config:
        raise SystemExit("找不到跨卷配置：{}".format(cfg_path))

    write(os.path.join(out_dir, "build_log.txt"), "\n".join(log))
    print("\n".join(log))
    return "\n".join(log)


if __name__ == "__main__":
    main()
