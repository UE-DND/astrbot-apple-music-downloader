# AstrBot Apple Music Downloader

> ⚠️ 为避免服务器过载和封号风险，AstrBot 方式下仅允许下载单曲，不支持专辑、播放列表等批量下载功能。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://gh-proxy.com/https://github.com/UE-DND/astrbot_apple_music_downloader.git
```

重启 AstrBot 以自动识别插件

## 通过 AstrBot 框架使用

### 1. 检查服务状态

```bot
/am_status
```

### 2. 登录 Apple Music 账户

```bot
/am_login 你的AppleID 密码
```

如需 2FA 验证，输入收到的 6 位验证码：

```bot
/am_2fa 123456
```

### 3. 下载音乐

```bot
/am https://music.apple.com/cn/album/xxx/123?i=456
```

指定下载音质

```bot
/am https://music.apple.com/cn/album/xxx/123?i=456 aac
```

## 指令概览

| 指令 | 说明 |
|:-----|:-----|
| `/am <链接> [音质]` | 下载单曲 |
| `/am_login <账号> <密码>` | 登录账户 |
| `/am_2fa <验证码>` | 输入 2FA 验证码 |
| `/am_logout <账号>` | 登出账户 |
| `/am_accounts` | 查看已登录账户 |
| `/am_queue` | 查看下载队列 |
| `/am_cancel <ID>` | 取消任务 |
| `/am_status` | 服务状态 |
| `/am_help` | 显示帮助 |

## 通过 CLI 使用

在仓库根目录执行：

```bash
python -m core status
python -m core login --u example@gamil.com --p pwd
python -m core download --l "https://music.apple.com/cn/album/xxx/123?i=456" --q alac
python -m core accounts
python -m core logout --u example@gmail.com
```

CLI 方式会自动读取 `_conf_schema.json` 默认值

使用方式：

```bash
python -m core status --config "./config.json"
```

CLI 方式支持专辑、歌单、艺术家批量下载：

```bash
python -m core download --l "https://music.apple.com/cn/album/xxx/123"
python -m core download --l "https://music.apple.com/cn/playlist/xxx/pl.u-xxxxx"
python -m core download --l "https://music.apple.com/cn/artist/xxx/123"
python -m core download --l "https://music.apple.com/cn/artist/xxx/123" --include-participate-songs
```

## 配置文件选项

### 音质选项

| 参数 | 说明 |
|:-----|:-----|
| `alac` | 无损（默认）|
| `aac` | AAC |

> 插件仅支持 `alac` 与 `aac` 音质。

### CLI 音质选项

| 参数 | 说明 |
|:-----|:-----|
| `alac` | 无损（默认） |
| `ec3` | 杜比全景声 |
| `ac3` | 杜比数字 |
| `aac` | AAC |
| `aac-binaural` | AAC Binaural |
| `aac-downmix` | AAC Downmix |
| `aac-legacy` | AAC Legacy |

### Native 模式（推荐，默认）

> 💡 Native 模式需要登录 AppleMusic 账户

1. 默认配置即可使用，无需修改
2. 或在 WebUI 中设置 `wrapper_mode` 为 `native`

### Remote 模式（公共实例）

> 💡 使用公共实例时无需登录账户

1. 在 AstrBot WebUI 中修改配置：
   - `wrapper_mode`
   - `wrapper_url`
   - `wrapper_secure`

2. 热重启插件

用于测试的包装器管理器实例：

```toml
[instance] # 由 @WorldObservationLog 维护
url = "wm.wol.moe"
secure = true
# 或
[instance] # 由 @itouakira 维护
url = "wm1.wol.moe"
secure = true
```

## ⚠️ 注意

- Native 模式需要有效的 Apple Music 订阅
- 部分曲目可能因地区限制不可用
- 此项目仅供技术交流，下载文件默认 24 小时后自动删除

## 感谢所有上游开发者的贡献
