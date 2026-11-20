# 反光试片的区间采集

现有 `README.md` 里的工作流已能按具名读单接收点值和明确缺样。白班现在还要接纳只能限定原码范围的原件，上限仍取开轮时冻结的整数。一次高档原据足以排除另一种判级时立即实收。只有范围跨界才补拍，保留给这次补拍的实际曝光和原据，供停源后领取。

## 公开入口

- `station.rounds.authorization.authorize_read(conn, round_id, piece, order_id, *, mode='point')` 新增 `interval` 模式。两种模式共享原读单和逐片授权的唯一性，既有读单视为 `point`。同参授权复领，改模式、异参或错绑拒绝。一个班轮可混用两种模式。
- `station.device.trigger.trigger_interval(conn, head, round_id, piece, order_id, gain)` 在开放轮为已登记区间读单发高档 `high` 或低档 `low`，返回实际 `GainExposure`。读单沿用首次发拍绑定的设备，已发同参请求认回原曝光。请求低档时须已有绑定到本单的高档物理终局，且该原件覆盖上限两侧。调用方可以先请求低档再投递高档，也可以先投递高档再请求低档。高档未完成、无读、已足以判级或请求错绑时，拒绝低档发拍。旧点值发令和实收入口不得用于区间单。
- `station.intake.accept.accept_interval(conn, head, order_id, original, *, before_commit=None)` 接纳一份 `GainOriginal`。核对整份来件、源端原件、原读单和实际档位曝光。错绑或异件报 `ValueError`，保全已接纳状态。低档到件时仍须核齐同单的真实高档。返回含 `status`、`receipt`、`effective_interval` 的字典。重复投递有效原件返回当前读单状态，签发后复领原结果。回执号为字符串，有效区间为 `(lower, upper)`，未产生对应项目时取 `None`。
- `station.lookup.sample.get_interval_sample(conn, receipt_number)` 仅凭站库返回字典，字段为 `receipt_number`、`round_id`、`piece`、`order_id`、`grade`、`effective_interval`、`originals`。`grade` 为 `pass` 或 `over_limit`。`originals` 是按高档在前排列的 `GainOriginal` 元组，包含每个实际采用原件的档位、曝光号、物理拍和原区间。未知回执仍报 `KeyError`，已知回执走错领取入口报 `ValueError`，缺失或冲突的持久证据报 `ReferenceIntegrityError`。旧点值回执继续由 `get_sample` 领取原结构。

## 判级与班账

原件区间用闭区间 `(lower, upper)` 表示，两端是整数，布尔值无效，上端 `None` 表示正无穷。整份原件的 `interval` 为 `None` 才表示设备明确无读。无读终局与曝光尚未完成是不同的状态。

| 可用的原据 | `status` | 班账与首执 |
| --- | --- | --- |
| 高档明确无读 | `missing` | 计缺样，无首执 |
| 高档全在上限及以下 | `pass` | 计合格，按高档区间签发 |
| 高档全在上限以上 | `over_limit` | 计超限，按高档区间签发 |
| 高档跨界，尚未接到合规低档 | `need_low` | 继续待办 |
| 同单两档交集为空 | `conflict` | 继续待办，无首执 |
| 低档明确无读或交集仍跨界 | `indeterminate` | 继续待办，无首执 |
| 同单两档交集足以判级 | `pass` 或 `over_limit` | 按交集签发，保留两份原区间 |

区间端点须保全，不能把边界或区间代表值作为设备点值。正式结论、实际用据和首执一次提交。`before_commit` 在首次正式结果写入后、提交前调用，异常时没有该次新成果，允许用原件重投。`close` 继续按清册顺序返回在办或原有三类计数的班账。`get_round` 可领取同一混合班账及原结论引用。班轮无需重新登记或改上限。

## 设备夹具和失答

`station.device.gain_fixture.load_head('examples/gain.json')` 提供持续存活的原始设备夹具。`head.fire(piece, gain, tag)` 是实际发拍操作。标签由调用方提供，设备照录，每次真正发拍都产生独立曝光。`head.history()` 按物理动作顺序返回全履历，曝光号在同一设备内唯一。`head.complete(exposure_id)` 由场景操作者交付曝光对应的原件，可晚于发令；`head.read(exposure_id)` 查可用原件，尚未完成时返回 `None`。

`head.lose_next_reply('before')` 让下一次原始发拍在动作前失答，`head.lose_next_reply('after')` 让该动作实际发生后失答，两种都报告 `LostExposureReply`。以低档失答作为本次推进的中断，重开站库连接后用同一存活设备重投。已实际发出的低档必须返回原曝光，未实际发出的合资格低档可以继续发拍。未完成的原件仍可按原曝光迟回。场景中由一个采集控制方串行调用，设备持有的履历和物理原件不会随站库连接关闭而清空。最终领取只用站库，设备重启与跨班补拍不在本次范围。

## 手算样例与运行

样例 `examples/gain.json` 含设备原始观察。上限 48 时，X 的高档 `[48,52]` 需要低档 `[44,48]`，签发有效区间 `[48,48]`。Y 的 `[49,+∞)` 直接判超限，P 的 `[45,48]` 直接判合格，M 高档无读按缺样封定，这三片均没有低档动作。C 补拍后为空交集，U 补拍后仍跨界，N 低档无读，三片仍在封轮待办。

初始 `.pointwise-runtime.json` 的 `verify` 和 `demo` 仍运行原回归和原样例。用 `python3 -B -m station.device.gain_fixture examples/gain.json` 检查夹具的两种失答与独立物理动作。交付时在原运行清单中接入新采集核验和示例，保留原回归和原场景。核验包含两种低档失答、错误原件拒收、无裁决的待办以及签发失败后的原件续办。演示按需补拍和停源重开，展示实际发拍次数、首执、有效区间及原件，不一致时核验返回非零退出码。
