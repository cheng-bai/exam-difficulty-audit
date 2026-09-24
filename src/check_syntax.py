# -*- coding: utf-8 -*-
"""数据文件语法体检。

为什么需要它：试卷评分数据（`data_<id>.py`）通常很长，且是分块写入的
（先写填空题块、再写选择题块、最后拼接），**很容易出现多余或缺失的括号**。
这种错误在跑主程序时只会表现为一句 `SyntaxError`，定位成本高。
本工具逐文件 `compile()`，把出错行号、列号与上下文直接写进报告。

用法：
    python src/check_syntax.py                 # 体检 src/ 、examples/demo/ 、papers/
    python src/check_syntax.py papers foo      # 只体检指定目录
    python src/check_syntax.py --out r.txt     # 指定报告路径

退出码：有语法错误时为 1，否则为 0（便于接入 CI）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_DIRS = [os.path.join(HERE), os.path.join(ROOT, "examples", "demo"),
                os.path.join(ROOT, "papers")]


def check_file(path, fn):
    """返回 (是否通过, 报告行列表)。"""
    lines = []
    with open(path, "rb") as f:
        raw = f.read()
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, ["{}: 非 UTF-8 编码：{}".format(fn, e)]
    try:
        compile(src, path, "exec")
    except SyntaxError as e:
        lines.append("{}: 语法错误 第 {} 行 第 {} 列：{}".format(fn, e.lineno, e.offset, e.msg))
        body = src.split("\n")
        ln = e.lineno or 1
        lo, hi = max(0, ln - 4), min(len(body), ln + 2)
        for i in range(lo, hi):
            mark = ">>" if i == ln - 1 else "  "
            lines.append("{} {:5d}| {}".format(mark, i + 1, body[i]))
        if e.offset:
            lines.append("        | " + " " * (e.offset - 1) + "^")
        lines.append("  提示：分块拼接的数据文件最常见的原因是多余的 `]` 或 `}`，"
                     "请核对上一个块的结尾。")
        return False, lines
    return True, ["{}: OK（{} 字节）".format(fn, len(raw))]


def scan(dirs):
    report, ok, bad, total = [], 0, 0, 0
    for d in dirs:
        if not os.path.isdir(d):
            report.append("（跳过，目录不存在）{}".format(d))
            report.append("")
            continue
        report.append("== {} ==".format(d))
        files = sorted(fn for fn in os.listdir(d)
                       if fn.endswith(".py") and fn != "check_syntax.py")
        if not files:
            report.append("（目录内没有 .py 文件）")
        for fn in files:
            total += 1
            passed, lines = check_file(os.path.join(d, fn), fn)
            if passed:
                ok += 1
            else:
                bad += 1
            report.extend(lines)
        report.append("")
    report.append("合计 {} 个文件：通过 {}，失败 {}".format(total, ok, bad))
    return report, bad


def main(argv=None):
    p = argparse.ArgumentParser(prog="check_syntax.py", description="数据文件语法体检")
    p.add_argument("dirs", nargs="*", help="要体检的目录（默认 src/ examples/demo/ papers/）")
    p.add_argument("--out", default=None, help="报告输出路径；默认 out/syntax_report.txt")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    dirs = [d if os.path.isabs(d) else os.path.join(ROOT, d) for d in args.dirs] or DEFAULT_DIRS
    report, bad = scan(dirs)
    text = "\n".join(report)

    out = args.out or os.path.join(ROOT, "out", "syntax_report.txt")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(text)
    print("\n报告已写入：{}".format(out))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
