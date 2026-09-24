# -*- coding: utf-8 -*-
"""跨卷难度对比报告（配置驱动）。

设计取舍：**可计算的部分自动生成，需要判断的部分由使用者自己写。**

原先的实践中，跨卷报告里的「教学重点」「复习建议」「材料问题汇总」等内容是逐字手写的。
这些无法由分数推出，硬编码进代码只会把它变成只能用于某一组试卷的一次性脚本。
因此本模块：

    - 自动生成：口径表、难度总览、分布、六维峰值画像、题序曲线、压轴与高负担区、模块比较、分段对比；
    - 不生成：教学结论、复习建议、材料问题定性——请写进 config 的 "extra_md" 指向的 Markdown，
      或写在 "notes" 数组里，逐条插入「材料与统计口径」之后。

配置示例（放在 <papers>/papers.json，或由 --config 指定）：

    {
      "title": "某市 2024—2026 高三模拟卷｜跨卷难度量化报告",
      "report_file": "跨卷难度总报告.md",
      "order": ["mock2024", "mock2025", "mock2026"],
      "labels": {"mock2024": "2024", "mock2025": "2025", "mock2026": "2026"},
      "segments": {"填空题": [1, 12], "选择题": [13, 16], "解答题": [17, 9999]},
      "notes": ["三卷均由同一命题组命制，分值结构一致。"],
      "extra_md": "cross_extra.md"
    }

注意：跨卷比较只在**同口径**下有意义。若不同试卷使用了不同的算法版本或不同权重，
本模块不做自动合并，需要使用者自行分开报告。
"""
import json
import os

DIMORDER = ("K", "R", "A", "V", "P", "I")
BAND_ORDER = ("基础", "中档", "难题")


def _lab(cfg, pid):
    return cfg.get("labels", {}).get(pid, pid)


def _ordered(res, cfg):
    by_id = {p["pid"]: (p, a, o) for p, a, o in res}
    order = cfg.get("order") or [p["pid"] for p, _, _ in res]
    out = []
    for pid in order:
        if pid not in by_id:
            raise SystemExit("跨卷配置 order 中的 {} 不在本次构建的试卷内".format(pid))
        out.append((pid, by_id[pid]))
    return out


def _mean(vals):
    return round(sum(vals) / len(vals), 1) if vals else None


def build(res, cfg, out_dir, papers_dir):
    """生成跨卷报告，返回一行日志说明。"""
    items = _ordered(res, cfg)
    labels = [_lab(cfg, pid) for pid, _ in items]
    ncol = len(labels)
    L = []

    title = cfg.get("title", "跨卷难度量化对比报告")
    L.append("# {}".format(title))
    L.append("")
    L.append("> 口径：六维—$H/C/T\\to D$ 规则分。D 为**规则型试题结构指数**，"
             "不是预期失分率，不代表一定比例的学生做错。各卷同口径复算：全局统一"
             "**非负数四舍五入**（round half up），$s$ 与 $\\Sigma e$ 用精确有理数计算，"
             "仅在公式标明取整处取整。")
    L.append("")

    # 一、材料与统计口径
    L.append("## 一、材料与统计口径")
    L.append("")
    L.append("| 试卷 | 分析单元 | 有效单元 | 分值加权分母 | 分值覆盖率 | 核验状态分布 |")
    L.append("|---|---|---|---|---|---|")
    for (pid, (p, a, _)), lab in zip(items, labels):
        st = {}
        for u in a["units"]:
            st[u["status"]] = st.get(u["status"], 0) + 1
        cov = round(100.0 * a["wsum"] / p["total"], 1) if p.get("total") else 0
        L.append("| {} | {} | {} | {} 分 | {}% | {} |".format(
            lab, len(a["units"]), a["st"]["n"], a["wsum"], cov,
            "、".join("{}×{}".format(k, v) for k, v in sorted(st.items()))))
    L.append("")
    for n in cfg.get("notes", []):
        L.append("- {}".format(n))
    L.append("")

    # 二、难度总览
    L.append("## 二、难度总览（小问等权）")
    L.append("")
    L.append("| 试卷 | 小问数 | D 均值 | D 中位数 | D 最高 | D 标准差 | H 均值 | C 均值 | T 均值 | 分值加权指数 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for (pid, (p, a, _)), lab in zip(items, labels):
        L.append("| {} | {} | **{}** | {} | {} | {} | {} | {} | {} | {} |".format(
            lab, a["st"]["n"], a["st"]["mean"], a["st"]["median"], a["st"]["max"],
            a["st"]["sd"], a["H"], a["C"], a["T"],
            a["wD"] if a["wD"] is not None else "无法给出"))
    L.append("")
    L.append("难度分布（小问数／占比）：")
    L.append("")
    L.append("| 试卷 | 基础 0—34 | 中档 35—64 | 难题 65—100 |")
    L.append("|---|---|---|---|")
    for (pid, (p, a, _)), lab in zip(items, labels):
        d = a["dist"]
        L.append("| {} | {}（{}%） | {}（{}%） | {}（{}%） |".format(
            lab, d["基础"]["count"], d["基础"]["cnt_pct"], d["中档"]["count"],
            d["中档"]["cnt_pct"], d["难题"]["count"], d["难题"]["cnt_pct"]))
    L.append("")
    L.append("六维峰值画像（各小问取步骤最大值后在有效小问间平均）：")
    L.append("")
    L.append("| 试卷 | " + " | ".join(DIMORDER) + " |")
    L.append("|" + "---|" * (len(DIMORDER) + 1))
    for (pid, (p, a, _)), lab in zip(items, labels):
        L.append("| {} | {} |".format(lab, " | ".join(str(a["six"][k]) for k in DIMORDER)))
    L.append("")

    # 三、题序难度曲线
    L.append("## 三、题序难度变化")
    L.append("")
    L.append("**题序难度曲线（D 值，按原卷顺序）**")
    L.append("")
    for (pid, (p, a, _)), lab in zip(items, labels):
        pts = "　".join("{}:{}".format(u["sub_label"], u["_res"]["D"]) for u in a["units"])
        L.append("- **{}**：{}".format(lab, pts))
    L.append("")

    # 四、分段对比（可选）
    segs = cfg.get("segments")
    if segs:
        L.append("### 分段对比")
        L.append("")
        L.append("| 试卷 | " + " | ".join(segs.keys()) + " |")
        L.append("|" + "---|" * (len(segs) + 1))
        for (pid, (p, a, _)), lab in zip(items, labels):
            cells = []
            for _, (lo, hi) in segs.items():
                ds = [u["_res"]["D"] for u in a["valid"] if lo <= u["q"] <= hi]
                m = _mean(ds)
                cells.append(str(m) if m is not None else "—")
            L.append("| {} | {} |".format(lab, " | ".join(cells)))
        L.append("")

    # 五、压轴与高负担区
    L.append("## 四、压轴与高负担区")
    L.append("")
    L.append("| 试卷 | 最高 D 小问 | 次高 | 第三 |")
    L.append("|---|---|---|---|")
    for (pid, (p, a, _)), lab in zip(items, labels):
        top = sorted(a["valid"], key=lambda u: -u["_res"]["D"])[:3]
        cells = ["{}（{}）".format(u["sub_label"], u["_res"]["D"]) for u in top]
        while len(cells) < 3:
            cells.append("—")
        L.append("| {} | {} |".format(lab, " | ".join(cells)))
    L.append("")
    L.append("| 试卷 | B=3 的小问 | 必做分支≥2 的小问 | 复核标记小问 |")
    L.append("|---|---|---|---|")
    for (pid, (p, a, _)), lab in zip(items, labels):
        b3 = [u["sub_label"] for u in a["units"] if u["B"] == 3]
        br = [u["sub_label"] for u in a["units"] if u.get("branches", 0) >= 2]
        rv = [u["sub_label"] for u in a["units"] if u.get("review")]
        L.append("| {} | {} | {} | {} |".format(
            lab, "、".join(b3) or "—", "、".join(br) or "—", "、".join(rv) or "—"))
    L.append("")

    # 六、模块比较
    L.append("## 五、模块比较：跨卷平均规则难度")
    L.append("")
    L.append("| 主模块 | " + " | ".join(labels) + " | 小问数合计 |")
    L.append("|" + "---|" * (ncol + 2))
    mods = []
    for pid, (p, a, _) in items:
        for u in a["valid"]:
            if u["module"] not in mods:
                mods.append(u["module"])
    rows = []
    for m in mods:
        cells, n = [], 0
        for pid, (p, a, _) in items:
            v = [u["_res"]["D"] for u in a["valid"] if u["module"] == m]
            n += len(v)
            cells.append(str(round(sum(v) / len(v), 1)) if v else "—")
        score = sum(float(c) for c in cells if c != "—")
        rows.append((score, m, cells, n))
    for _, m, cells, n in sorted(rows, key=lambda x: -x[0]):
        L.append("| {} | {} | {} |".format(m, " | ".join(cells), n))
    L.append("")

    # 七、方法与限制
    L.append("## 六、方法与限制（必读）")
    L.append("")
    L.append("- **D 不是难度实测值。** 权重与档位阈值属未经实测校准的规则设定；"
             "相差一两分不足以作实质难度判断，本报告只在**同口径跨卷比较**范围内使用它。")
    L.append("- **不收集学生信息**：全部评分只依据题目与解法证据。")
    L.append("- **不伪造权重**：小问分值未给出时不做分值加权，改用小问等权并报告覆盖率。")
    L.append("- **待核验单元不计入均值**，不按 0 分处理。")
    L.append("- **单方核验风险**：未引入独立复核者时，分数属单方结果，关键难题与异常分数建议二次复算。")
    L.append("- **同口径前提**：本报告只合并相同算法版本与相同权重的结果；混合口径需分开报告。")
    L.append("")

    # 附加手写段
    extra = cfg.get("extra_md")
    if extra:
        p = extra if os.path.isabs(extra) else os.path.join(papers_dir, extra)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                L.append(f.read().rstrip())
            L.append("")
        else:
            raise SystemExit("跨卷配置 extra_md 指向的文件不存在：{}".format(p))

    report_file = cfg.get("report_file", "跨卷难度对比报告.md")
    out_path = os.path.join(out_dir, report_file)
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    return "跨卷报告已生成：{}（{} 行）".format(report_file, len(L))
