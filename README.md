# 整卷难度量化与解析关键批注 · Exam Difficulty Audit

把一套试卷的「难在哪里」变成**可复算、可追溯、可跨卷比较**的数字，同时在原题解析旁落**有教学价值的就地批注**。

本仓库是一套**规则型**试卷难度审计工具链：给定一份试卷的题干与解析，按六维（K/R/A/V/P/I）给每个解题步骤打分，沿关键依赖路径累计，得出 H / C / T / D 四项指标与「基础—中档—难题」档位，并生成一份教师可直接阅读的 Markdown（原题 · 答案 · 润色解析 · 就地批注 · 量化明细）。

> **一句话定位**：D 是**规则型试题结构指数**，不是预期失分率。它不预测多少学生做错，只在**同一口径下**回答「这套卷的难度分布如何、难在哪个环节」。

English: A rule-based difficulty audit toolkit for exam papers. It scores each solution step on six dimensions, aggregates along the critical dependency path into H/C/T/D indices, and emits both a teacher-readable Markdown report and machine-readable scoring data. The index is a **structural difficulty index, not a predicted failure rate**.

---

## 一、它解决什么问题

常见做法是用星级或「我觉得这套卷偏难」来评价试卷，问题是不可复核、不可比较。本工具链试图把评价过程拆成**可被第三方重算**的步骤：

1. **先审题审答案，再评分** —— 缺解析则补解并标注，答案冲突则单列待复核，不静默改题。
2. **按解题步骤打分** —— 不是按题目感觉打分；每一步给出六维取值与依据。
3. **沿关键路径累计** —— 区分「单步最难」（H）与「连续负担」（C），再叠加「入口搜索与返工风险」（T）。
4. **允许并暴露不确定性** —— 答案冲突、图形缺失、配分未知时，明确标注为待核验，不计入有效均值。

---

## 二、快速开始

无第三方依赖，仅需 Python 3.9+（仅标准库）。

```bash
# 1. 跑内置自拟示例卷，验证环境
python src/build.py --demo

# 2. 查看产出
#    out/demo_a-教师阅读版.md    教师阅读版（原题+答案+解析+批注+量化）· 基础取向
#    out/demo_b-教师阅读版.md    同上 · 综合取向（含一个难题）
#    out/demo-跨卷难度总报告.md  两卷对比报告
#    out/评分明细.json           全部步骤级证据，可复算
#    out/逐小问评分.csv          一行一个小问
#    out/步骤类型词典.json       步骤类型定义
```

跑自己的试卷：

```bash
# 把你的试卷放进 papers/，每个试卷两个文件：
#   papers/data_<id>.py     评分数据（见 docs/schema.md）
#   papers/<id>.md          正文（含 @@Q1 @@Q2 … 占位符）
python src/build.py --papers papers
```

生成跨卷对比报告（可选）：

```bash
# 在 papers/papers.json 里描述试卷集，然后：
python src/build.py --papers papers
# 跨卷报告写入 out/ 下，文件名由 config 的 report_file 决定
```

命令行参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--papers DIR` | `papers` | 试卷数据目录，需含 `data_<id>.py` |
| `--body DIR` | 同 `--papers` | 正文 md 目录（与数据分开放时指定） |
| `--out DIR` | `out` | 产出目录，不存在时自动创建 |
| `--demo` | — | 等价于 `--papers examples/demo` |
| `--config FILE` | `<papers>/papers.json` | 跨卷报告配置，存在即生成 |
| `--no-cross` | — | 强制不生成跨卷报告 |
| `--list` | — | 只列出发现到的试卷，不构建 |
| 位置参数 | 全部 | 指定 paper id，只构建这些卷 |

---

## 三、工作流

```
试卷（题干 + 答案解析，使用者自备）
        │
        ├─► 审题审答案 ──► 记录核验状态 / 待复核项 / 版本冲突（不静默改题）
        │
        ├─► 拆步骤 ─────► 题面条件 → 数学动作 → 中间结论 → 后续用途
        │                 每步六维打分 K/R/A/V/P/I ∈ {0,1,2} + 逐维依据
        │
        ├─► 复算 ───────► s_i → 关键路径 Σe → H / C / T / D → 档位
        │
        └─► 产出 ───────► 教师阅读版.md（含就地批注）
                          评分明细.json / 逐小问评分.csv / 步骤类型词典.json
                          （可选）跨卷难度对比报告
```

方法论全文见 [`docs/methodology.md`](docs/methodology.md)；公式与档位定义见 [`docs/scoring.md`](docs/scoring.md)；数据结构见 [`docs/schema.md`](docs/schema.md)。

---

## 四、指标体系速览

每个有意义的步骤在六个维度上取 0/1/2 分：

| 维度 | 含义 |
|---|---|
| **K** 知识与方法识别 | 题面直连公式 ⇄ 需识别未显露的关系 |
| **R** 条件转换与推理 | 单步直推 ⇄ 隐含条件 / 逆向构造 / 长依赖 |
| **A** 代数运算负荷 | 一两步常规运算 ⇄ 长符号链 / 高错误敏感 |
| **V** 表征转换 | 不需要 ⇄ 多次或双向转换 |
| **P** 参数、新定义与边界 | 无额外负担 ⇄ 多分支 / 复杂量词 |
| **I** 知识交汇 | 单模块 ⇄ 多模块深度耦合 |

权重 `K .20 / R .25 / A .15 / V .15 / P .15 / I .10`。由此：

```
s_i = Σ_k w_k · dim_k / 2                      步骤负荷
e_j = s_j · r_j · d_j,  d_j = min(1+0.1(j-1), 1.3)   路径第 j 步有效负荷
H   = round(100 · max s_i)                      最难单步
C   = round(100 · (1 - exp(-Σe/3)))             关键路径连续负担
T   = 10 · (t1+…+t5)                            入口搜索与返工风险 ×10
D   = round(0.45H + 0.35C + 0.20T)              综合
```

档位：**0—34 基础｜35—64 中档｜65—100 难题**，严格先评分再定档。

另设两个**不并入 D** 的独立标记：`B`（关键突破难度 0—3，结构判断、未经校准）与`必做分支数`（主解法中必须全部完成的终端情形数）。

---

## 五、目录结构

```
exam-difficulty-audit/
├─ README.md
├─ LICENSE                  MIT
├─ CONTRIBUTING.md
├─ CHANGELOG.md
├─ docs/
│   ├─ methodology.md       方法论全文（评分与批注的完整规则）
│   ├─ scoring.md           公式、权重、档位、折扣与独立标记
│   ├─ schema.md            PAPER / 单元 / 步骤 数据结构与输出字段规范
│   └─ copyright.md         版权与真题使用声明（使用前请读）
├─ src/
│   ├─ build.py             引擎：复算 + 排版 + 机器可读输出
│   ├─ cross_paper.py       跨卷对比报告（配置驱动，可选）
│   ├─ paper_lib.py         数据构造库 S()/U()/Q()/paper()，含取值即时校验
│   ├─ step_types.py        步骤类型词典（起始版，可自行扩展）
│   └─ check_syntax.py      数据文件语法体检（分块手写易漏括号）
├─ examples/demo/           两份自拟示例卷（不含任何真题原文）
│   ├─ data_demo_a.py / demo_a.md    基础取向
│   ├─ data_demo_b.py / demo_b.md    综合取向（含一个难题）
│   ├─ papers.json                   跨卷报告配置
│   └─ demo_cross_extra.md           跨卷报告的手写附加段
├─ papers/                  使用者自备试卷（已 gitignore）
└─ out/                     产出（已 gitignore）
```

---

## 六、重要限制（务必先读）

1. **D 不是难度实测值。** 权重与档位阈值属**未实测校准的规则设定**。`D=70` 不代表 70% 学生做错；相差一两分不足以作实质难度判断。请只在**同口径跨卷比较**范围内使用。
2. **不收集、不推测学生背景。** 全部评分只依据题目与解法证据，不按班级水平、熟练度或训练经历调整。
3. **贡献者偏差。** 步骤拆分粒度与六维打分带有判断成分。建议由第二位复核者对关键难题与异常分数做独立复算；未复核的结论应标明是单方结果。
4. **分值加权的前提。** 只有当小问分值明确时才做分值加权；仅有得分点而无明确配分时**不自行推算权重**，改用小问等权并说明覆盖率。
5. **待核验单元不计入均值**，不按 0 分处理；报告须写明分母与覆盖率。

---

## 七、版权声明

**本仓库不包含任何第三方试题、答案或解析原文。** `examples/demo/` 中的示例卷为本项目自拟，仅用于演示数据格式与产出形态。

使用本工具处理高考真题、教辅试题等第三方材料时，**请自行确认你拥有相应使用权**；公开发布含第三方试题原文的产出可能构成侵权。详见 [`docs/copyright.md`](docs/copyright.md)。

---

## 八、许可

代码与文档采用 [MIT License](LICENSE)。

引用本项目或方法论时，建议注明：`整卷难度量化与解析关键批注（Exam Difficulty Audit）`。

---

## 九、如何贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。欢迎贡献：新的步骤类型、六维打分边界案例、跨卷报告模板、其他学科的适配。**请勿提交任何含版权试题原文的数据文件。**
