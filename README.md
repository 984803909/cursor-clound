# Cursor Sand Tool - macOS GitHub Build

用 GitHub Actions 在云端打包 macOS 版（Intel x86_64 + Apple Silicon arm64 双架构）。

## 推送步骤（一次搞定）

```bash
cd github-build
git init
git add -A
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

推送后 GitHub 自动跑 Actions（约 3-5 分钟）。

## 下载 mac 版

1. 打开仓库 → **Actions** 标签 → 最新一次运行
2. 底部 **Artifacts** 下载：
   - `CursorSandTool-x86_64.zip` → Intel Mac
   - `CursorSandTool-arm64.zip` → Apple Silicon (M1/M2/M3/M4)
3. 解压得到 `CursorSandTool.app`（或可执行文件），双击运行

## 使用

- 首次启动弹激活框 → 粘贴 license_web.html 生成的密钥
- 到期后锁定退出
- **修改 Cursor 需要管理员权限**：终端 `sudo ./CursorSandTool` 或右键 app 选"打开"（绕过 Gatekeeper 首次提示）

## 发卡（作者）

本地打开 `license_web.html` → 加载 `private_ed25519.pem`（私钥，勿上传仓库）→ 生成密钥发客户。

## 手动触发

没改动代码也想重新打包：仓库 **Actions** → Build macOS app → **Run workflow** 按钮。

## 文件说明

| 文件 | 作用 |
|---|---|
| `.github/workflows/build-mac.yml` | GitHub Actions 打包脚本（双架构矩阵） |
| `cursor_sand_gui.py` | 主程序（tkinter GUI） |
| `cursor_sand_min.py` | SAND 切换核心逻辑 |
| `license_gate.py` | 授权校验（内嵌公钥，验签+过期锁定） |
| `ed25519_pure.py` | 纯 Python Ed25519（无依赖） |
| `license_web.html` | 发卡网页（需配私钥，仅作者本地用） |
