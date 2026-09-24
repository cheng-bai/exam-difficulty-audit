# -*- coding: utf-8 -*-
"""试卷数据构造库 —— S() / U() / Q() / paper()。

试卷数据文件（`data_<id>.py`）只需：

    from paper_lib import S, U, Q, paper

    PAPER = paper(
        pid="demo", title="示例卷", total=20, expect_units=4,
        meta=[("来源", "自拟")],
        questions=[
            Q(1, "填空题", [ U(1, "1", 4, "不等式", "一元二次不等式", [
                S("s1", "因式分解 $x^2-3x+2=(x-1)(x-2)$", ty="T02",
                  dim=(1, 1, 1, 0, 0, 0), ev="……"),
            ], T=(0, 0, 0, 0, 1), T_ev=["……"]*5, B=0, B_ev="……") ]),
        ])

字段含义与完整校验规则见 docs/schema.md，这里只做最小必要的即时校验（取值越界、
长度不符），以便错误在数据文件被导入的瞬间就暴露，而不是在统计阶段产生离奇数字。
"""

DIM_KEYS = ("K", "R", "A", "V", "P", "I")


def S(sid, act, dep=None, ty="T01", dim=(0, 0, 0, 0, 0, 0), ev="",
      rep=None, stage=""):
    """构造一个评分步骤。

    参数
    ----
    sid   步骤 ID（同一小问内唯一，建议 s1、s2…）
    act   数学动作：一句可指认的话，可含 LaTeX
    dep   前置步骤 ID 列表（依赖 DAG 的边；用于关键路径搜索）
    ty    步骤类型 ID，见 src/step_types.py
    dim   六维取值元组 (K, R, A, V, P, I)，每维 0/1/2
    ev    逐维依据：必须指向具体题面条件或解析步骤，不能只写「需转化」这类空话
    rep   同型机械重复组号；同组第 1 次 r=1、第 2 次 0.7、第 3 次及以后 0.4。
          仅用于**确认属于同型机械重复**的步骤；不同推理动作不得共用组号
    stage 目标阶段标签（可选，便于按阶段聚合）
    """
    if len(dim) != 6:
        raise ValueError("步骤 {} 的 dim 必须是 6 元组 (K,R,A,V,P,I)".format(sid))
    for k, v in zip(DIM_KEYS, dim):
        if v not in (0, 1, 2):
            raise ValueError("步骤 {} 的 {} 维取值 {} 越界，只能是 0/1/2".format(sid, k, v))
    if not str(ev).strip():
        raise ValueError("步骤 {} 缺少逐维依据 ev".format(sid))
    return {"id": sid, "stage": stage, "act": act, "dep": list(dep or []),
            "type": ty, "dim": dict(zip(DIM_KEYS, dim)), "ev": ev, "rep": rep}


def U(q, sub_label, m, module, points, steps, T, T_ev, B, B_ev,
      branches=0, barrier="", status="已核验", review=None, ann=0, uid=None):
    """构造一个分析单元（= 可独立给分的一个小问）。

    参数
    ----
    q          原题号（int，需与正文 md 中的 @@Q<no> 一致）
    sub_label  小问标签，如 "12" 或 "21（3）"
    m          该小问分值；未知填 None（**不要填 0**，未知与零分不同）
    module     主模块（用于模块比较；辅助知识不要塞进这里）
    points     核心考点（一句话）
    steps      步骤列表，元素由 S() 构造
    T          入口搜索与返工风险五项 (t1,…,t5)，每项 0/1/2
               依次为：入口隐蔽程度／无效入口数量／发现延迟／回退范围／端点遗漏
    T_ev       五项各自的具体依据（5 条字符串）
    B          关键突破难度 0—3
    B_ev       B 的依据：题干提示 + 需要跨越的那一步
    branches   主解法中必须全部完成的终端情形数；无分类填 0
    barrier    关键卡点（一句话）
    status     核验状态：已核验／部分核验／待核验／答案冲突
    review     复核标记；无则 None
    ann        就地批注条数
    """
    if len(T) != 5:
        raise ValueError("单元 {} 的 T 必须是 5 项".format(sub_label))
    for v in T:
        if v not in (0, 1, 2):
            raise ValueError("单元 {} 的 T 取值 {} 越界，只能是 0/1/2".format(sub_label, v))
    if len(T_ev) != 5:
        raise ValueError("单元 {} 的 T_ev 必须给出 5 条依据".format(sub_label))
    if B not in (0, 1, 2, 3):
        raise ValueError("单元 {} 的 B 取值 {} 越界，只能是 0—3".format(sub_label, B))
    if m is not None and m <= 0:
        raise ValueError("单元 {} 的分值应为正数或 None（未知用 None，不要用 0）".format(sub_label))
    if not steps:
        raise ValueError("单元 {} 没有步骤，无法评分".format(sub_label))
    if uid is None:
        uid = "{}-{}".format(q, str(sub_label).replace("（", "").replace("）", "")
                             .replace("(", "").replace(")", ""))
    return {"id": uid, "q": q, "sub_label": sub_label, "m": m, "module": module,
            "points": points, "steps": steps, "T": list(T), "T_ev": list(T_ev),
            "B": B, "B_ev": B_ev, "branches": branches, "barrier": barrier,
            "status": status, "review": review, "ann": ann}


def Q(no, kind, units):
    """构造一道大题；kind 如 填空题／选择题／解答题。"""
    return {"no": no, "kind": kind, "units": list(units)}


def paper(pid, title, total, expect_units, meta, questions,
          source="（未填写）", review_note="（未填写）", conflict_table=None,
          out_file=None):
    """构造整卷。

    expect_units 是**声明的**分析单元数，构建时会与实际值强校验。
    这不是形式主义：口径要求「不为统一格式硬补小问」，声明一个数字可以让
    统计口径的漂移立刻暴露。
    """
    if conflict_table is None:
        conflict_table = "| 项 | 说明 |\n|---|---|\n| 无 | 本次未发现需单列的问题 |"
    p = {"pid": pid, "title": title, "total": total, "expect_units": expect_units,
         "meta": list(meta), "questions": list(questions), "source": source,
         "review_note": review_note, "conflict_table": conflict_table}
    if out_file:
        p["file"] = out_file
    return p
