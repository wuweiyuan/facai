# OneDrive 股票缓存设置

这份文档面向刚 clone 项目的电脑。目标是：

- 代码用 Git 同步。
- 股票缓存数据用 OneDrive 同步。
- 项目仍然读写 `.cache/akshare`。
- `.cache/akshare` 通过文件系统链接指向 OneDrive 里的 `stock-cache/akshare`。

## 原理

项目配置里缓存目录是：

```yaml
data_source:
  cache_dir: .cache/akshare
```

代码只读写项目里的 `.cache/akshare`。我们在系统层面把这个目录做成链接：

```text
项目/.cache/akshare  ->  OneDrive/stock-cache/akshare
```

所以程序更新 `.cache/akshare/bars/*.csv` 时，实际更新的是 OneDrive 数据目录。OneDrive 客户端再负责把数据同步到另一台电脑。

## macOS 设置

### 1. 安装并登录 OneDrive

安装 OneDrive，用同一个账号登录。登录后确认 OneDrive 目录存在：

```bash
ls "$HOME/Library/CloudStorage"
```

你的当前 Mac 路径是：

```text
/Users/wayne/Library/CloudStorage/OneDrive-个人
```

如果你的输出不是 `OneDrive-个人`，后续命令里的路径要按实际名称替换。

### 2. 克隆项目

```bash
git clone https://github.com/wuweiyuan/facai.git
cd facai
git checkout gaizao2
```

### 3. 连接 OneDrive 缓存

如果 OneDrive 里已经有从另一台电脑同步来的 `stock-cache/akshare`，执行：

```bash
OD="$HOME/Library/CloudStorage/OneDrive-个人"

mkdir -p .cache
ln -s "$OD/stock-cache/akshare" .cache/akshare
```

如果这是第一台电脑，项目里已经有本地 `.cache/akshare`，需要迁移到 OneDrive：

```bash
OD="$HOME/Library/CloudStorage/OneDrive-个人"

mkdir -p "$OD/stock-cache"
mv .cache/akshare "$OD/stock-cache/akshare"
ln -s "$OD/stock-cache/akshare" .cache/akshare
```

### 4. 验证链接

```bash
ls -la .cache
readlink .cache/akshare
test -f .cache/akshare/meta/stock_list.csv && echo "cache ok"
```

看到 `.cache/akshare -> .../OneDrive-个人/stock-cache/akshare`，并且输出 `cache ok`，说明项目已经连到 OneDrive 数据目录。

### 5. 安装依赖并运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

python3 -m app.main recommend-adaptive --date YYYY-MM-DD
```

## Windows 设置

### 1. 安装并登录 OneDrive

安装 OneDrive，用同一个账号登录。默认目录通常是：

```text
C:\Users\你的用户名\OneDrive
```

如果你改过 OneDrive 位置，后续 `$OD` 要换成实际路径。

### 2. 克隆项目

PowerShell：

```powershell
git clone https://github.com/wuweiyuan/facai.git
cd facai
git checkout gaizao2
```

### 3. 等 OneDrive 数据同步完成

确认这个目录存在：

```powershell
$OD = "$env:USERPROFILE\OneDrive"
Test-Path "$OD\stock-cache\akshare"
```

返回 `True` 才说明 OneDrive 已经同步到股票缓存目录。

如果返回 `False`，先检查：

- Windows OneDrive 是否登录了和 Mac 相同的账号。
- Mac 上 OneDrive 是否已经上传完成。
- Windows OneDrive 是否还在同步。
- OneDrive 实际目录是否不是 `$env:USERPROFILE\OneDrive`。

### 4. 连接 OneDrive 缓存

在项目根目录执行：

```powershell
$OD = "$env:USERPROFILE\OneDrive"

New-Item -ItemType Directory -Force -Path ".cache" | Out-Null
New-Item -ItemType Junction -Path ".cache\akshare" -Target "$OD\stock-cache\akshare"
```

如果 `.cache\akshare` 已经存在但不是 Junction，先确认里面没有你要保留的数据，再删除它后重建：

```powershell
Remove-Item ".cache\akshare" -Recurse
New-Item -ItemType Junction -Path ".cache\akshare" -Target "$OD\stock-cache\akshare"
```

### 5. 验证链接

```powershell
cmd /c dir .cache
Test-Path ".cache\akshare\meta\stock_list.csv"
Get-Item ".cache\akshare\meta\stock_list.csv" | Select-Object LastWriteTime
```

`Test-Path` 返回 `True`，说明项目已经能通过 `.cache\akshare` 读到 OneDrive 数据。

### 6. 安装依赖并运行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .

python -m app.main recommend-adaptive --date YYYY-MM-DD
```

如果 PowerShell 不允许激活虚拟环境，执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

然后重新执行：

```powershell
.venv\Scripts\Activate.ps1
```

## 日常使用

一台电脑更新数据后，另一台电脑能不能看到，取决于 OneDrive 是否完成同步：

```text
Mac 运行程序
-> 更新 OneDrive/stock-cache/akshare
-> OneDrive 上传
-> Windows OneDrive 下载
-> Windows 项目 .cache\akshare 看到新数据
```

反过来也是一样。

注意事项：

- 不要两台电脑同时运行会更新缓存的命令，避免 OneDrive 冲突文件。
- 运行前看一下 OneDrive 是否已登录并同步完成。
- `.cache/` 不要提交到 Git。
- 如果刚清过 Git 历史，另一台电脑建议重新 clone，或执行 `git fetch origin` 后 `git reset --hard origin/gaizao2`。

## 常见检查命令

macOS：

```bash
pgrep -fl OneDrive
readlink .cache/akshare
test -f .cache/akshare/meta/stock_list.csv && echo "cache ok"
```

Windows PowerShell：

```powershell
Get-Process OneDrive -ErrorAction SilentlyContinue
cmd /c dir .cache
Test-Path ".cache\akshare\meta\stock_list.csv"
```

