# 配电保护选择性核验 API

基于 **Python + FastAPI + SQLite** 的配电保护选择性（保护配合）核验服务。

客户端通过 HTTP API 保存**不可变场景**：单电源有向树形馈线，每条支路至多装一台断路器；
服务对每个故障沿"故障支路 → 电源"路径找到最近断路器（主保护），并逐一检验每台上级断路器
在整个故障电流区间内的配合关系 `t上级 / t主 ≥ R`（等号算合格），给出：

- 每个故障的结论：`qualified`（合格）/ `unqualified`（不合格）/ `unprotected`（无保护）；
- 每对（主保护, 上级保护）的**最小时间比**及对应电流（并列取较小电流）；
- 按两曲线折点分段求出的**所有不合格电流范围**及边界是否包含。

曲线插值在电流、时间均取对数后进行（log-log 线性插值），**禁止外推**；
非树拓扑、非法曲线/故障、路径上曲线覆盖不足的故障均被拒绝（HTTP 422）。

---

## 1. 启动方式

### Docker Compose（一键启动）

```bash
docker compose up --build
```

服务监听 `http://localhost:8000`，SQLite 数据库持久化在命名卷 `selectivity-data` 中
（容器内路径 `/data/selectivity.db`，可用环境变量 `SELECTIVITY_DB` 修改）。

### 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

## 2. 访问方式

| 地址 | 说明 |
|---|---|
| `http://localhost:8000/docs` | Swagger UI 交互式文档（可直接试调用） |
| `http://localhost:8000/openapi.json` | OpenAPI 3 规范 |
| `http://localhost:8000/health` | 健康检查，返回 `{"status":"ok"}` |

## 3. 数据模型与核验规则

### 场景（Scenario，保存后不可变）

```jsonc
{
  "name": "可选名称",
  "source": "S",                 // 电源节点（树根）
  "nodes": ["S", "A", ...],      // 节点列表
  "branches": [                  // 有向支路：from=电源侧, to=负荷侧
    {"id": "br1", "from": "S", "to": "A"}
  ],
  "breakers": [                  // 每条支路至多一台断路器
    {"id": "CB1", "branch": "br1",
     "curve": [[50, 20], [500, 1.5], [5000, 0.25]]}   // [电流, 动作时间] 折点
  ],
  "faults": [
    {"id": "F1", "branch": "br2",
     "current_min": 100, "current_max": 1000, "ratio": 2}
  ]
}
```

创建时校验（不满足返回 **422**）：

- **单电源有向树**：`支路数 = 节点数 − 1`；电源入度为 0，其余节点入度恰为 1；
  所有节点均可从电源到达（连通、无环）；
- **曲线**：至少 2 个折点；电流为正且**严格递增**；动作时间为正；
- **断路器**：所属支路存在，且每条支路至多一台；
- **故障**：支路存在；`0 < current_min ≤ current_max`（正电流闭区间）；`ratio = R > 1`。

场景不可变：只提供创建与读取，`PUT/PATCH/DELETE` 返回 **405**。

### 核验规则

对每个故障，沿"故障支路 → 电源"的有向路径依次收集断路器：

1. 路径上**没有任何断路器** → `unprotected`（无保护）；
2. 最近断路器为**主保护**；有主保护而**无上级** → `qualified`（合格）；
3. 存在上级时，对**每台上级**与主保护组成一对，在整个故障电流闭区间
   `[current_min, current_max]` 上检验 `t上级(I) / t主(I) ≥ R`：
   - 动作时间由曲线经 **log-log 线性插值**求得，**禁止外推**——任一相关曲线
     （主保护或上级）的电流范围不能完整覆盖故障区间时，该故障被**拒绝**（422）；
   - **等号算合格**（实现采用 1e-9 相对容差吸收浮点误差）；
   - 以两条曲线折点的并集分段：`log(比值)` 在每段内是 `log I` 的线性函数，
     因此最小比值必在折点/区间端点处取得，不合格集合是若干区间之并，
     区间内部边界为 `比值 = R` 的解析根；
   - **边界包含规则**：不合格区间端点落在故障区间端点且该处比值 < R 时
     **包含**（`included=true`）；落在内部根（比值恰 = R，等号合格）时
     **不包含**（`included=false`）；
   - 任一上级不合格 → 该故障 `unqualified`，全部合格 → `qualified`。

### 故障结论（FaultResult）

```jsonc
{
  "fault_id": "F3",
  "branch": "br3",
  "current_range": [100.0, 1000.0],
  "required_ratio": 2.0,
  "status": "unqualified",        // qualified | unqualified | unprotected
  "main_breaker": "CB2",          // 无保护时为 null
  "pairs": [                      // 每台上级一条
    {
      "main_breaker": "CB2",
      "upstream_breaker": "CB1",
      "required_ratio": 2.0,
      "min_ratio": 1.5,           // 区间内的最小时间比
      "min_ratio_current": 500.0, // 取得最小值的电流（并列取较小电流）
      "compliant": false,
      "violations": [             // 所有不合格电流范围
        {"current_from": 100.0, "current_to": 1000.0,
         "from_included": true, "to_included": true}
      ]
    }
  ]
}
```

## 4. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/scenarios` | 保存不可变场景（非法拓扑/曲线/故障 → 422），返回带 `id` 的完整场景 |
| `GET` | `/scenarios` | 场景摘要列表 |
| `GET` | `/scenarios/{id}` | 读取场景（不存在 → 404） |
| `POST` | `/scenarios/{id}/verify` | 核验全部故障并缓存结果；任一故障曲线覆盖不足 → 422 且不存结果 |
| `POST` | `/scenarios/{id}/faults/{fid}/verify` | 核验单个故障；覆盖不足 → 422 |
| `GET` | `/scenarios/{id}/results` | 读取已缓存的全部核验结果（未核验 → 404） |
| `GET` | `/scenarios/{id}/results/{fid}` | 读取单个故障的核验结果（未核验 → 404） |
| `GET` | `/health` | 健康检查 |

## 5. 实际请求与响应示例

以下示例均为对本服务真实调用得到的输出（场景 id 以实际返回为准）。

### 5.1 创建场景 `POST /scenarios`

馈线结构：`S ─br1→ A ─br2(CB1)→ B ─br3(CB2)→ C`（br1 上无断路器）。

```bash
curl -s -X POST http://localhost:8000/scenarios \
  -H "Content-Type: application/json" -d '{
  "name": "demo-feeder",
  "source": "S",
  "nodes": ["S", "A", "B", "C"],
  "branches": [
    {"id": "br1", "from": "S", "to": "A"},
    {"id": "br2", "from": "A", "to": "B"},
    {"id": "br3", "from": "B", "to": "C"}
  ],
  "breakers": [
    {"id": "CB1", "branch": "br2", "curve": [[50, 20], [500, 1.5], [5000, 0.25]]},
    {"id": "CB2", "branch": "br3", "curve": [[50, 10], [500, 1.0], [5000, 0.1]]}
  ],
  "faults": [
    {"id": "F1", "branch": "br1", "current_min": 100, "current_max": 1000, "ratio": 2},
    {"id": "F2", "branch": "br2", "current_min": 100, "current_max": 1000, "ratio": 2},
    {"id": "F3", "branch": "br3", "current_min": 100, "current_max": 1000, "ratio": 2},
    {"id": "F4", "branch": "br3", "current_min": 50,  "current_max": 5000, "ratio": 2},
    {"id": "F5", "branch": "br3", "current_min": 1000,"current_max": 3000, "ratio": 2},
    {"id": "F6", "branch": "br3", "current_min": 3000,"current_max": 5000, "ratio": 2},
    {"id": "F7", "branch": "br3", "current_min": 100, "current_max": 1000, "ratio": 1.5}
  ]
}'
```

响应 **201**（节选，完整场景原样返回并附加 `id` 与 `created_at`）：

```json
{
  "name": "demo-feeder",
  "source": "S",
  "nodes": ["S", "A", "B", "C"],
  "branches": [
    {"id": "br1", "from": "S", "to": "A"},
    {"id": "br2", "from": "A", "to": "B"},
    {"id": "br3", "from": "B", "to": "C"}
  ],
  "breakers": [
    {"id": "CB1", "branch": "br2",
     "curve": [[50.0, 20.0], [500.0, 1.5], [5000.0, 0.25]]},
    {"id": "CB2", "branch": "br3",
     "curve": [[50.0, 10.0], [500.0, 1.0], [5000.0, 0.1]]}
  ],
  "faults": ["... 与请求一致 ..."],
  "id": "e46922e0e5204df7a9477907f71901f9",
  "created_at": "2026-09-29T19:33:48.182129+00:00"
}
```

### 5.2 核验全部故障 `POST /scenarios/{id}/verify`

```bash
curl -s -X POST http://localhost:8000/scenarios/e46922e0e5204df7a9477907f71901f9/verify
```

响应 **200**（真实输出）：

```json
{
  "scenario_id": "e46922e0e5204df7a9477907f71901f9",
  "results": [
    {
      "fault_id": "F1",
      "branch": "br1",
      "current_range": [100.0, 1000.0],
      "required_ratio": 2.0,
      "status": "unprotected",
      "main_breaker": null,
      "pairs": []
    },
    {
      "fault_id": "F2",
      "branch": "br2",
      "current_range": [100.0, 1000.0],
      "required_ratio": 2.0,
      "status": "qualified",
      "main_breaker": "CB1",
      "pairs": []
    },
    {
      "fault_id": "F3",
      "branch": "br3",
      "current_range": [100.0, 1000.0],
      "required_ratio": 2.0,
      "status": "unqualified",
      "main_breaker": "CB2",
      "pairs": [
        {
          "main_breaker": "CB2",
          "upstream_breaker": "CB1",
          "required_ratio": 2.0,
          "min_ratio": 1.5,
          "min_ratio_current": 500.0,
          "compliant": false,
          "violations": [
            {"current_from": 100.0, "current_to": 1000.0,
             "from_included": true, "to_included": true}
          ]
        }
      ]
    },
    {
      "fault_id": "F4",
      "branch": "br3",
      "current_range": [50.0, 5000.0],
      "required_ratio": 2.0,
      "status": "unqualified",
      "main_breaker": "CB2",
      "pairs": [
        {
          "main_breaker": "CB2",
          "upstream_breaker": "CB1",
          "required_ratio": 2.0,
          "min_ratio": 1.5,
          "min_ratio_current": 500.0,
          "compliant": false,
          "violations": [
            {"current_from": 50.0, "current_to": 1828.69298523,
             "from_included": false, "to_included": false}
          ]
        }
      ]
    },
    {
      "fault_id": "F5",
      "branch": "br3",
      "current_range": [1000.0, 3000.0],
      "required_ratio": 2.0,
      "status": "unqualified",
      "main_breaker": "CB2",
      "pairs": [
        {
          "main_breaker": "CB2",
          "upstream_breaker": "CB1",
          "required_ratio": 2.0,
          "min_ratio": 1.74934064633,
          "min_ratio_current": 1000.0,
          "compliant": false,
          "violations": [
            {"current_from": 1000.0, "current_to": 1828.69298523,
             "from_included": true, "to_included": false}
          ]
        }
      ]
    },
    {
      "fault_id": "F6",
      "branch": "br3",
      "current_range": [3000.0, 5000.0],
      "required_ratio": 2.0,
      "status": "qualified",
      "main_breaker": "CB2",
      "pairs": [
        {
          "main_breaker": "CB2",
          "upstream_breaker": "CB1",
          "required_ratio": 2.0,
          "min_ratio": 2.23214879332,
          "min_ratio_current": 3000.0,
          "compliant": true,
          "violations": []
        }
      ]
    },
    {
      "fault_id": "F7",
      "branch": "br3",
      "current_range": [100.0, 1000.0],
      "required_ratio": 1.5,
      "status": "qualified",
      "main_breaker": "CB2",
      "pairs": [
        {
          "main_breaker": "CB2",
          "upstream_breaker": "CB1",
          "required_ratio": 1.5,
          "min_ratio": 1.5,
          "min_ratio_current": 500.0,
          "compliant": true,
          "violations": []
        }
      ]
    }
  ]
}
```

结果解读（CB1/CB2 时间比在 I=50 处为 2.0、I=500 处为 1.5、I=5000 处为 2.5）：

- **F1**：路径上无断路器 → `unprotected`；
- **F2**：主保护 CB1，无上级 → `qualified`；
- **F3**：整个区间比值 < 2 → 不合格范围为闭区间 `[100, 1000]`（两端包含）；
- **F4**：I=50 处比值恰为 2（等号合格）→ 下端点**不包含**；上端点为
  解析根 ≈ 1828.69（比值恰 = 2）→ 也**不包含**；
- **F5**：下端点 1000 处比值 ≈ 1.749 < 2 → **包含**；上端点为根 → 不包含；
- **F6**：全区间比值 ≥ 2 → `qualified`；
- **F7**：最小比值恰等于 R=1.5（等号合格）→ `qualified`。

### 5.3 读取缓存的核验结果

```bash
curl -s http://localhost:8000/scenarios/e46922e0e5204df7a9477907f71901f9/results/F4
```

响应 **200**（与 verify 时的对应条目一致）：

```json
{
  "fault_id": "F4",
  "branch": "br3",
  "current_range": [50.0, 5000.0],
  "required_ratio": 2.0,
  "status": "unqualified",
  "main_breaker": "CB2",
  "pairs": [
    {
      "main_breaker": "CB2",
      "upstream_breaker": "CB1",
      "required_ratio": 2.0,
      "min_ratio": 1.5,
      "min_ratio_current": 500.0,
      "compliant": false,
      "violations": [
        {"current_from": 50.0, "current_to": 1828.69298523,
         "from_included": false, "to_included": false}
      ]
    }
  ]
}
```

### 5.4 拒绝：曲线覆盖不足（422）

故障区间 `[10, 100]` 超出两台断路器曲线的电流范围 `[50, 5000]`（禁止外推）：

```bash
# 场景：S ─br1(CB1)→ A ─br2(CB2)→ B，曲线均为 [[50, ...], [5000, ...]]
# 故障：{"id": "F1", "branch": "br2", "current_min": 10, "current_max": 100, "ratio": 2}
curl -s -X POST http://localhost:8000/scenarios/<场景id>/verify
```

响应 **422**（真实输出）：

```json
{
  "detail": {
    "error": "insufficient_curve_coverage",
    "message": "fault rejected: breaker curve(s) on the path to the source do not cover the fault current interval (extrapolation is forbidden)",
    "faults": [
      {
        "fault_id": "F1",
        "required_current_range": [10.0, 100.0],
        "insufficient_breakers": [
          {"breaker_id": "CB2", "curve_current_range": [50.0, 5000.0]},
          {"breaker_id": "CB1", "curve_current_range": [50.0, 5000.0]}
        ]
      }
    ]
  }
}
```

> 注：覆盖检查在"存在上级断路器、确实需要插值比较"时强制执行；
> 无上级（合格）或无保护（无断路器）的故障不涉及曲线求值。

### 5.5 拒绝：非树拓扑（422）

```bash
curl -s -X POST http://localhost:8000/scenarios \
  -H "Content-Type: application/json" -d '{
  "source": "S",
  "nodes": ["S", "A", "B", "C"],
  "branches": [
    {"id": "br1", "from": "S", "to": "A"},
    {"id": "br2", "from": "B", "to": "C"},
    {"id": "br3", "from": "C", "to": "B"}
  ],
  "breakers": [],
  "faults": []
}'
```

响应 **422**（真实输出，B、C 构成与电源断开的环）：

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body"],
      "msg": "Value error, not a tree: node(s) 'B', 'C' are not reachable from the source 'S' (disconnected or cyclic)",
      "input": { "..." : "..." }
    }
  ]
}
```

其他会被 422 拒绝的情形：支路数 ≠ 节点数 − 1、非电源节点入度 ≠ 1、
曲线折点少于 2 个、电流非正/非严格递增、动作时间非正、
一条支路装多台断路器、`current_min > current_max`、`ratio ≤ 1` 等。

### 5.6 不可变性（405）与未找到（404）

```bash
curl -s -X PUT    http://localhost:8000/scenarios/<id>   # -> 405 Method Not Allowed
curl -s -X DELETE http://localhost:8000/scenarios/<id>   # -> 405 Method Not Allowed
curl -s http://localhost:8000/scenarios/does-not-exist   # -> 404
curl -s http://localhost:8000/scenarios/<id>/results     # 未执行 verify 时 -> 404
```

## 6. 项目结构

```
app/
  main.py         # FastAPI 路由与错误映射
  models.py       # Pydantic 模型 + 单电源有向树拓扑校验
  selectivity.py  # 核心算法：log-log 插值、分段比值分析、不合格区间求解
  verify.py       # 路径查找（主保护/上级）与故障核验编排
  db.py           # SQLite 持久化（场景不可变，结果按 (场景, 故障) 缓存）
tests/
  test_selectivity.py  # 算法单元测试（插值、边界包含、等号合格、退化区间等）
  test_api.py          # API 集成测试（拓扑拒绝、覆盖拒绝、结论与结果缓存）
Dockerfile
docker-compose.yml
requirements.txt / requirements-dev.txt
```

## 7. 实现要点

- **插值**：电流、时间均取自然对数后分段线性插值；曲线范围外不外推
  （覆盖校验先行，求值时仅对 1e-9 相对容差内的边界做钳制）。
- **分段**：候选电流 = 故障区间端点 ∪ 两曲线折点（区间内部分）；相邻候选点间
  `log(t上/t主)` 为线性，故最小比值在候选点取得，不合格区间边界为解析根
  `exp(log a + (log b − log a)·(log R − log r_a)/(log r_b − log r_a))`。
- **等号合格**：比值比较采用 1e-9 相对容差，`ratio < R·(1−1e-9)` 才判不合格；
  最小比值并列（1e-12 相对容差）时取较小电流。
- **不可变性**：场景只插不改；核验结果对同一确定场景可重复生成并覆盖缓存。
