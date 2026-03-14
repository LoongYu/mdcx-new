## 新增

* 网络检测支持并发执行（受控并发），显著降低整轮检测耗时
* 网络检测统一支持“自定义域名优先，默认域名兜底”策略
* 配置目录切换与配置恢复支持动态选择配置文件，不再固定 `config.json`
* `javday` 新增从“上傳時間/上传时间”提取发布时间（`release`）与年份（`year`）

## 变更

* 清理失效刮削站点：`oppai`、`v-av`、`to-satsu`、`bibian`、`nanpa`、`mko-labo`、`muku`、`befreebe`、`prestige`、`faleno`、`yesjav`、`love6`、`fc2club`、`airav`
* 继续清理失效站点：`avsex`、`avsox`
* 同步移除对应配置项、网络检测列表、站点映射与 UI 选项，避免残留引用
* 站点禁用策略统一收敛到 `DISABLED_WEBSITES`，旧配置会自动过滤禁用站点并回退到可用默认值
* 发布流程支持在手动输入更新日志时，将 `\n` 自动转换为真实换行

## 修复

* 修复旧配置中残留失效站点导致的网站设置异常、网络检测异常与配置校验失败
* 修复 Windows 发布包命名尾部附带 SHA ID 的问题（现为简洁文件名）
* 修复 Windows 发布未正确带入 changelog 的问题
* 修复 `javday` 直接拼接 `/videos/{number}` 导致的误匹配与超时，改为按 `/search/?wd=` 搜索再进入详情页
* 修复 `javday` 详情页封面链接提取兼容性（优先 `og:image`，保留兜底）
* 修复 `javlibrary`、`iqqtv` 等触发 Cloudflare 场景被误判为断网的问题（改为可访问但受限提示）
* 修复网络检测在代理链路异常场景下的误判，增加直连兜底判定
* 修复网站设置中自定义域名切换后易丢失、检测仍走旧默认域名的问题
* 修复配置恢复与目录切换逻辑写死 `config.json` 导致多配置文件场景异常的问题

<details>
<summary>Full Changelog</summary>

本次包含网络检测并发优化、可达性判定优化、自定义域名优先策略、多配置文件支持以及历史站点清理同步。

</details>
