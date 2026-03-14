## 新增

* 清理失效刮削站点：`oppai`、`v-av`、`to-satsu`、`bibian`、`nanpa`、`mko-labo`、`muku`、`befreebe`、`prestige`、`faleno`、`yesjav`、`love6`、`fc2club`、`airav`
* 继续清理失效站点：`avsex`、`avsox`
* 同步移除对应配置项、网络检测列表、站点映射与 UI 选项，避免残留引用
* 站点禁用策略统一收敛到 `DISABLED_WEBSITES`，旧配置会自动过滤禁用站点并回退到可用默认值
* 发布流程支持在手动输入更新日志时，将 `\n` 自动转换为真实换行

## 修复

* 修复旧配置中残留失效站点导致的网站设置异常、网络检测异常与配置校验失败
* 修复 Windows 发布包命名尾部附带 SHA ID 的问题（现为简洁文件名）
* 修复 Windows 发布未正确带入 changelog 的问题

<details>
<summary>Full Changelog</summary>

本次包含站点清理、配置同步、UI 同步与发布流程修复提交。

</details>
