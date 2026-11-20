# 澄湾输送线光学复检站

白班操作员按复检工单为具名试片开轮。值班员用合格、超限和已结束缺样三类结果安排放行、隔离或补测。夜班停用光学源后，凭原回执复核单片的原码和物理拍，凭班账号核对整轮结果。

## 运行

需要 Python 3.11 和标准库。在项目根目录执行完整回归。

```sh
python3 -B check_project.py
```

控制台在物化时提供标准检查入口。也可以直接运行同一测试包。

```sh
python3 -B -m unittest discover -s tests -t . -v
python3 -B -m station.rounds.workstation demo examples/shift.json examples/retry.json
```

`demo` 为每份脚本使用独立临时库，经过发令、实收和封轮后关闭原连接，再从同一数据库领取。它核对脚本中的手算期望并输出班账号和样本回执。未预期错误和样例差异返回非零退出码。

```sh
python3 -B -m station.rounds.workstation init shift.sqlite3
python3 -B -m station.rounds.workstation run shift.sqlite3 examples/shift.json
python3 -B -m station.rounds.workstation sample shift.sqlite3 sample:1
python3 -B -m station.rounds.workstation round shift.sqlite3 round:shift-01
```

末两条使用本次 `run` 输出的实际编号。上例编号适用于从空库运行第一份脚本。`init` 可反复执行，保留原班轮、原读单、成果和编号。`run` 执行带手算期望的新班轮样例。修改样例数据时给班轮和读单分配新号，并更新手算期望。原班轮由公开开轮和领取接口按原号复领。

## 班次流程

1. `station.rounds.opening.open(conn, round_id, roster, limit)` 冻结有序唯一的试片清册和整数上限。清册可为空。班轮号、试片名和读单号是非空白字符串，并按原样比较。同参返回原班轮及当前阶段，异参报错。
2. `station.rounds.authorization.authorize_read(conn, round_id, piece, order_id)` 为开放轮的清册成员绑定全站唯一的原读单号。同参复领，试片或读单被其他绑定使用时报错。
3. `station.device.trigger.trigger(conn, round_id, piece, order_id)` 校验完整授权，返回固定的单头曝光记录。仅开放轮可发令。`station.device.optical.apply_observation(conn, exposure, observation)` 消费左头物理观察，出样固定原码及原拍，空拍返回 `None`，无样直至显式结束才固定缺样。曝光有样终局形成后，输入适配器停止向该录制推进。
4. `station.intake.accept.accept(conn, order_id, terminal)` 核对设备持久终局和请求读单。有样原码以无换算的整数原码单位直接与上限比较，严格大于上限判 `over_limit`，其余判 `pass`。原码 0 有效。明确缺样判 `missing`，没有样本回执。提交成功后返回含 `conclusion` 和可选 `receipt` 的结果。同件复领首执。提交失败时，可用 `DeviceRecording(conn).get_terminal(trigger_id)` 取原件续办。
5. `station.closing.seal.close(conn, round_id)` 返回 `CloseResult`。在办时 `pending` 按清册排列且 `report` 为空。齐全时 `report` 含固定班账号、清册顺序的逐片结论和三个计数，另行调用沿用原账。设备已结束但尚未实收的试片仍在办。
6. `station.lookup.sample.get_sample(conn, receipt_number)` 返回原判级、原码、物理拍、原触发和读单。`station.lookup.round.get_round(conn, report_number)` 返回原班账和固定逐片结论。领取只需要数据库连接。

用 `station.rounds.workstation.initialize_station(conn)` 初始化可写连接。公开写入口负责各自提交，领取在同一读取事务内核对相关引用。调用公开入口时连接应无活动事务。输入冲突或不合法操作报 `ValueError`，未知号报 `KeyError`，损坏的领取引用报 `ReferenceIntegrityError`，SQLite 写入及提交错误向调用方报告。

## 两份手算样例

- `shift.json` 的清册是 C、A、B，上限 48。A 第 10 拍读 49，B 只空拍后结束，C 第 12 拍读 0。按 A、B、C 送达，A 实收后在办是 C、B。最后仍按 C、A、B 封轮，合格、超限、缺样各一。
- `retry.json` 的上限也是 48。D 第 10 拍读 48，送达时点 31。E 第 11 拍读 49，先到。D 首次提交前发生一次可控失败，随后沿原终局续办。班账为一合格、一超限、零缺样，按原回执领取 D 仍为第 10 拍。

物理拍与送达时点是分别指定的整数。脚本按头保存原始的出样、空拍和结束观察，调度按送达时点及入队次序交付。原读码、物理拍、判级和清册位置可逐项复核。编号以本库首次成功提交所得到的编号为准。
