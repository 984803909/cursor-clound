#!/usr/bin/env python3
"""
cursor_sand_min.py — Cursor Sand 核心功能的最小复现（去掉授权/租约外壳）。

Cursor Sand 真正对 Cursor 做的事只有两件，这个脚本就实现这两件：
  1) 把客户端上报给后端的 client-type 从 "ide" 改成 "sand"
     （Cursor 后端据此按内部 "Sand" 项目特权，给 auto 档路由高级模型）
  2) 同步更新 product.json 里的完整性校验值 (base64(sha256) 去掉'='),
     否则 Cursor 会因文件被篡改而报 "安装已损坏"

命令:
  python cursor_sand_min.py status  [--app-dir 路径]   # 查看当前状态
  python cursor_sand_min.py apply   [--app-dir 路径]   # 改成 sand 模式 (ide->sand)
  python cursor_sand_min.py restore [--app-dir 路径]   # 恢复默认 (sand->ide)

restore 靠反向替换 sand->ide, 不依赖备份 —— 不管是本脚本还是 Cursor Sand
本体打的补丁, 都能还原。--app-dir 指向 Cursor 的 resources/app 目录; 不填自动探测。
运行前请完全退出 Cursor。
"""
import argparse, base64, hashlib, os, re, shutil, subprocess, sys

# 需要改的 4 个文件 (相对 resources/app)
TARGETS = [
    "out/main.js",
    "out/vs/workbench/api/node/extensionHostProcess.js",
    "out/vs/workbench/api/worker/extensionHostWorkerMain.js",
    "out/vs/workbench/workbench.desktop.main.js",
]
PRODUCT = "product.json"

# Cursor Agent CLI lives outside the IDE installation and ships as a versioned
# Node.js bundle.  These anchors intentionally avoid ACP's independent "acp"
# client type and unrelated uses of the word "cli".
CLI_VERSION_RE = re.compile(
    r"^(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d{2})-(\d{2})-(\d{2}))?-[0-9a-f]+$"
)
CLI_RULES_APPLY = [
    (re.compile(r'(surface:")cli(")'), r"\1sand\2"),
    (re.compile(r'(clientType:")cli(")'), r"\1sand\2"),
    (re.compile(r'(x-cursor-client-type",")cli(")'), r"\1sand\2"),
    (re.compile(r'(x-cursor-client-type":")cli(")'), r"\1sand\2"),
]
CLI_RULES_RESTORE = [
    (re.compile(r'(surface:")sand(")'), r"\1cli\2"),
    (re.compile(r'(clientType:")sand(")'), r"\1cli\2"),
    (re.compile(r'(x-cursor-client-type",")sand(")'), r"\1cli\2"),
    (re.compile(r'(x-cursor-client-type":")sand(")'), r"\1cli\2"),
]


class PatchError(RuntimeError):
    """The installed bundle does not match the known, safe patch anchors."""

# 两条精确、对压缩变量名容忍的替换规则, 覆盖全部 client-type 位置:
#   规则H: HTTP 头  "x-cursor-client-type"...:"ide"  或  ...,VAR??"ide"
#   规则C: clientType 三元   isGlass?"glass":"ide"
# 正向 ide->sand。注意: 只改 isGlass?"glass":"ide" (发给后端的 clientType),
# 不动 configuration.glass?"glass":"ide" 这类本地用途 —— 与 Cursor Sand 行为保持一致。
RULES_APPLY = [
    (re.compile(r'("x-cursor-client-type"[^"]{0,15}")ide(")'), r"\1sand\2"),
    (re.compile(r'(isGlass\?"glass":")ide(")'), r"\1sand\2"),
]
# 反向 sand->ide (restore 用, 不依赖备份, 谁打的补丁都能还原)
RULES_RESTORE = [
    (re.compile(r'("x-cursor-client-type"[^"]{0,15}")sand(")'), r"\1ide\2"),
    (re.compile(r'(isGlass\?"glass":")sand(")'), r"\1ide\2"),
]
# 用于探测“是否已 patch”
MARK_H = re.compile(r'"x-cursor-client-type"[^"]{0,15}"sand"')
MARK_C = re.compile(r'isGlass\?"glass":"sand"')


def vscode_checksum(path):
    """VS Code/Cursor 的文件完整性校验: base64(sha256(bytes)) 去掉 '=' 补齐。"""
    with open(path, "rb") as f:
        return base64.b64encode(hashlib.sha256(f.read()).digest()).decode().rstrip("=")


def _is_valid_app_dir(path):
    return bool(path) and os.path.isfile(os.path.join(path, PRODUCT))


def _app_dir_from_exe(exe_path):
    """Cursor.exe 所在目录推导 resources/app。"""
    if not exe_path:
        return None
    exe_path = os.path.normpath(exe_path.strip().strip('"'))
    root = os.path.dirname(exe_path)
    # 便携版 / 自定义安装: <root>/resources/app
    direct = os.path.join(root, "resources", "app")
    if _is_valid_app_dir(direct):
        return direct
    # 少数安装: <root>/Cursor/resources/app
    nested = os.path.join(root, "Cursor", "resources", "app")
    if _is_valid_app_dir(nested):
        return nested
    return None


def _app_dir_from_running_process():
    """从正在运行的 Cursor.exe 进程定位安装目录。"""
    if sys.platform != "win32":
        return None
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Process Cursor -ErrorAction SilentlyContinue | "
                "Select-Object -First 1 -ExpandProperty Path)",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        return _app_dir_from_exe(result.stdout)
    except Exception:
        return None


def _app_dir_from_registry():
    """从 Windows 卸载注册表项读取 Cursor 安装位置。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    uninstall_roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    ]

    def read_value(key, name):
        try:
            value, _ = winreg.QueryValueEx(key, name)
            return value
        except OSError:
            return None

    for hive, root_path in uninstall_roots:
        try:
            with winreg.OpenKey(hive, root_path) as root:
                subkey_count = winreg.QueryInfoKey(root)[0]
                for index in range(subkey_count):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as subkey:
                            display_name = read_value(subkey, "DisplayName") or ""
                            if "cursor" not in display_name.lower():
                                continue
                            for value_name in ("InstallLocation", "DisplayIcon", "UninstallString"):
                                raw = read_value(subkey, value_name)
                                if not raw:
                                    continue
                                raw = raw.strip().strip('"')
                                if raw.lower().endswith("cursor.exe"):
                                    found = _app_dir_from_exe(raw)
                                elif raw.lower().endswith(".exe"):
                                    found = _app_dir_from_exe(raw)
                                else:
                                    found = _app_dir_from_exe(
                                        os.path.join(raw, "Cursor.exe")
                                    ) or os.path.join(raw, "resources", "app")
                                    if not _is_valid_app_dir(found):
                                        found = None
                                if found:
                                    return found
                    except OSError:
                        continue
        except OSError:
            continue
    return None


def _static_app_dir_candidates():
    """常见固定安装路径候选。"""
    home = os.path.expanduser("~")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

    cands = [
        os.path.join(local_app_data, "Programs", "cursor", "resources", "app"),
        os.path.join(local_app_data, "Programs", "Cursor", "resources", "app"),
        os.path.join(program_files, "Cursor", "resources", "app"),
        os.path.join(program_files_x86, "Cursor", "resources", "app"),
        r"C:\Apps\cursor\resources\app",
        r"C:\Program Files\Cursor\resources\app",
        # macOS / Linux
        "/Applications/Cursor.app/Contents/Resources/app",
        os.path.join(home, "Applications/Cursor.app/Contents/Resources/app"),
        "/usr/share/cursor/resources/app",
        "/opt/Cursor/resources/app",
        "/opt/cursor/resources/app",
    ]

    if sys.platform == "win32":
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{drive}:\\"
            if not os.path.exists(root):
                continue
            cands.extend(
                [
                    os.path.join(root, "cursor", "resources", "app"),
                    os.path.join(root, "Cursor", "resources", "app"),
                    os.path.join(root, "Apps", "cursor", "resources", "app"),
                    os.path.join(root, "Program Files", "Cursor", "resources", "app"),
                    os.path.join(root, "Tools", "cursor", "resources", "app"),
                    os.path.join(root, "Software", "cursor", "resources", "app"),
                ]
            )
    return cands


def detect_app_dir_candidates():
    """按优先级返回所有探测到的 Cursor resources/app 目录（去重）。"""
    found = []
    seen = set()

    def add(path, _source):
        if not _is_valid_app_dir(path):
            return
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen:
            return
        seen.add(norm)
        found.append(os.path.normpath(path))

    env = os.environ.get("CURSOR_APP_DIR")
    if env:
        add(env, "环境变量 CURSOR_APP_DIR")

    add(_app_dir_from_running_process(), "运行中的 Cursor 进程")
    add(_app_dir_from_registry(), "Windows 注册表")

    for candidate in _static_app_dir_candidates():
        add(candidate, "常见安装路径")

    return found


def detect_app_dir_with_source():
    """返回 (path, source)；找不到则 (None, '')。"""
    env = os.environ.get("CURSOR_APP_DIR")
    if env and _is_valid_app_dir(env):
        return os.path.normpath(env), "环境变量 CURSOR_APP_DIR"

    running = _app_dir_from_running_process()
    if running:
        return running, "运行中的 Cursor 进程"

    registry = _app_dir_from_registry()
    if registry:
        return registry, "Windows 注册表"

    for candidate in _static_app_dir_candidates():
        if _is_valid_app_dir(candidate):
            return os.path.normpath(candidate), "常见安装路径"

    return None, ""


def detect_app_dir():
    path, _ = detect_app_dir_with_source()
    return path


def is_patched(text):
    return bool(MARK_H.search(text) or MARK_C.search(text))


def read(path):
    with open(path, "r", encoding="utf-8", errors="strict") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _cli_version_key(name):
    match = CLI_VERSION_RE.fullmatch(name)
    if not match:
        return None
    parts = [int(value) if value is not None else 0 for value in match.groups()]
    return tuple(parts)


def detect_cli_dir():
    """Return the Cursor Agent CLI installation root, if present."""
    env = os.environ.get("CURSOR_CLI_DIR")
    if env:
        return env
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidate = os.path.join(local_app_data, "cursor-agent")
        if os.path.isdir(candidate):
            return candidate
    return None


def detect_cli_index(cli_dir=None):
    """Locate the bundle used by the newest installed Cursor Agent version."""
    root = cli_dir or detect_cli_dir()
    if not root:
        raise PatchError(
            "找不到 Cursor Agent CLI，请安装 CLI 或用 --cli-dir 指定 cursor-agent 目录。"
        )
    versions_dir = os.path.join(root, "versions")
    try:
        names = os.listdir(versions_dir)
    except OSError as exc:
        raise PatchError(f"无法读取 Cursor Agent CLI 版本目录: {versions_dir}: {exc}") from exc

    candidates = []
    for name in names:
        key = _cli_version_key(name)
        index = os.path.join(versions_dir, name, "index.js")
        if key is not None and os.path.isfile(index):
            candidates.append((key, index))
    if not candidates:
        raise PatchError(f"没有找到可修改的 Cursor Agent CLI index.js: {versions_dir}")
    return max(candidates, key=lambda item: item[0])[1]


def _apply_rules(text, rules):
    total = 0
    for regex, replacement in rules:
        text, changed = regex.subn(replacement, text)
        total += changed
    return text, total


def _cli_marker_count(text, value):
    rules = CLI_RULES_APPLY if value == "cli" else CLI_RULES_RESTORE
    return sum(len(regex.findall(text)) for regex, _ in rules)


def _write_atomic(path, text):
    temporary = f"{path}.sandtmp"
    try:
        write(temporary, text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def preflight_cli(cli_dir=None):
    index = detect_cli_index(cli_dir)
    try:
        with open(index, "r+b"):
            pass
    except PermissionError as exc:
        raise PatchError(f"Cursor Agent CLI 文件不可写: {index}") from exc
    return index


def apply_cli(cli_dir=None):
    """Patch only Cursor Agent CLI and retain an exact one-file backup."""
    index = preflight_cli(cli_dir)
    text = read(index)
    patched, changed = _apply_rules(text, CLI_RULES_APPLY)
    if changed == 0:
        if _cli_marker_count(text, "sand"):
            return 0
        raise PatchError(
            f"CLI 包中没有找到已知 client-type 锚点，未写入任何内容: {index}"
        )

    backup = f"{index}.sandbak"
    if not os.path.exists(backup):
        shutil.copy2(index, backup)
    _write_atomic(index, patched)
    return changed


def restore_cli(cli_dir=None):
    """Restore Cursor Agent CLI from backup, with a reverse-patch fallback."""
    index = preflight_cli(cli_dir)
    backup = f"{index}.sandbak"
    if os.path.isfile(backup):
        shutil.copy2(backup, index)
        os.remove(backup)
        return True

    text = read(index)
    restored, changed = _apply_rules(text, CLI_RULES_RESTORE)
    if changed:
        _write_atomic(index, restored)
        return True
    if _cli_marker_count(text, "cli"):
        return False
    raise PatchError(f"CLI 包中没有找到已知 client-type 锚点: {index}")


def cli_status(cli_dir=None):
    index = detect_cli_index(cli_dir)
    text = read(index)
    cli_count = _cli_marker_count(text, "cli")
    sand_count = _cli_marker_count(text, "sand")
    if sand_count and not cli_count:
        state = "SAND"
    elif cli_count and not sand_count:
        state = "cli (默认)"
    elif sand_count and cli_count:
        state = "MIXED (部分修改)"
    else:
        state = "UNKNOWN (版本规则可能已变化)"
    return index, state, cli_count, sand_count


def update_checksums(app):
    """按 product.json.checksums 里的条目, 用磁盘上现有文件重算并写回。"""
    pj_path = os.path.join(app, PRODUCT)
    import json
    with open(pj_path, "r", encoding="utf-8") as f:
        pj = json.load(f)
    changed = 0
    for rel, old in list(pj.get("checksums", {}).items()):
        fp = os.path.join(app, "out", *rel.split("/"))
        if not os.path.isfile(fp):
            continue
        new = vscode_checksum(fp)
        if new != old:
            pj["checksums"][rel] = new
            changed += 1
    if changed:
        # 紧凑写法, 尽量贴近 Cursor 原始 product.json 风格
        with open(pj_path, "w", encoding="utf-8", newline="") as f:
            json.dump(pj, f, ensure_ascii=False, separators=(",", ":"))
    return changed


def preflight(app):
    """改任何文件前, 先确认全部目标可写; 不可写就整体中止, 避免只改一半。"""
    bad = []
    for rel in TARGETS + [PRODUCT]:
        p = os.path.join(app, *rel.split("/"))
        if not os.path.isfile(p):
            sys.exit(f"  x 找不到文件: {p} (Cursor 版本/路径不符?)")
        try:
            with open(p, "r+b"):
                pass
        except PermissionError:
            bad.append(rel)
    if bad:
        print("  x 无法写入以下文件 (权限不足或被占用):")
        for b in bad:
            print(f"      - {b}")
        print("  解决办法:")
        print("    1) 完全退出 Cursor (含托盘/后台, 任务管理器确认没有 Cursor.exe);")
        print("    2) 用【管理员身份】打开 PowerShell 再运行 —— 该目录写入需要管理员权限")
        print("       (这也是 Cursor Sand 要弹 UAC、用 helper 提权改文件的原因)。")
        sys.exit(2)


def _transform(app, rules, verb):
    preflight(app)
    total = 0
    for rel in TARGETS:
        p = os.path.join(app, *rel.split("/"))
        text = read(p)
        n = 0
        for rx, rep in rules:
            text, c = rx.subn(rep, text)
            n += c
        if n:
            write(p, text)
        total += n
        print(f"  - {rel:55s} 替换 {n} 处")
    ck = update_checksums(app)
    print(f"  - {PRODUCT:55s} 更新 {ck} 个校验值")
    if total == 0:
        print(f"  ! 一处都没替换到 —— 可能已经是目标状态, 或 Cursor 版本变了规则需更新。")
    print(f"[{verb}] 完成, 共替换 {total} 处。请重启 Cursor 生效。")


def cmd_apply(app):
    print(f"[apply] app-dir: {app}  (ide -> sand)")
    _transform(app, RULES_APPLY, "apply")


def cmd_restore(app):
    print(f"[restore] app-dir: {app}  (sand -> ide, 恢复默认)")
    _transform(app, RULES_RESTORE, "restore")


def cmd_status(app):
    import json
    print(f"[status] app-dir: {app}")
    pj = json.load(open(os.path.join(app, PRODUCT), encoding="utf-8"))
    cks = pj.get("checksums", {})
    for rel in TARGETS:
        p = os.path.join(app, *rel.split("/"))
        if not os.path.isfile(p):
            print(f"  - {rel:55s} [缺失]")
            continue
        t = read(p)
        state = "SAND" if is_patched(t) else "ide (默认)"
        print(f"  - {rel:55s} {state}")
    print("  完整性校验:")
    for rel, val in cks.items():
        fp = os.path.join(app, "out", *rel.split("/"))
        if os.path.isfile(fp):
            ok = "OK" if vscode_checksum(fp) == val else "不匹配(会报损坏)"
            print(f"    - {rel:52s} {ok}")


def cmd_apply_cli(cli_dir=None):
    index = detect_cli_index(cli_dir)
    print(f"[apply:cli] index: {index}  (cli -> sand)")
    changed = apply_cli(cli_dir)
    print(f"[apply:cli] 完成，共替换 {changed} 处。请重启 Cursor Agent CLI 生效。")


def cmd_restore_cli(cli_dir=None):
    index = detect_cli_index(cli_dir)
    print(f"[restore:cli] index: {index}  (sand -> cli)")
    changed = restore_cli(cli_dir)
    message = "已恢复" if changed else "已经是默认状态"
    print(f"[restore:cli] {message}。")


def cmd_status_cli(cli_dir=None):
    index, state, cli_count, sand_count = cli_status(cli_dir)
    print(f"[status:cli] index: {index}")
    print(f"  - 状态: {state}")
    print(f"  - cli 锚点: {cli_count}; sand 锚点: {sand_count}")
    print(f"  - 备份: {'存在' if os.path.isfile(index + '.sandbak') else '无'}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Cursor IDE / Agent CLI 客户端类型切换工具 (ide|cli <-> sand)"
    )
    parser.add_argument("cmd", choices=["apply", "restore", "status"])
    parser.add_argument(
        "--target",
        choices=["ide", "cli", "all"],
        default="ide",
        help="操作目标；默认 ide，以兼容旧命令",
    )
    parser.add_argument("--app-dir", help="Cursor IDE 的 resources/app 目录 (默认自动探测)")
    parser.add_argument(
        "--cli-dir",
        help="Cursor Agent CLI 的 cursor-agent 根目录 (默认自动探测)",
    )
    return parser


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    wants_ide = args.target in ("ide", "all")
    wants_cli = args.target in ("cli", "all")
    app = None
    try:
        if wants_ide:
            app = args.app_dir or detect_app_dir()
            if not app or not os.path.isfile(os.path.join(app, PRODUCT)):
                raise PatchError(
                    "找不到 Cursor IDE 的 resources/app 目录，请用 --app-dir 指定。"
                )

        # For --target all, validate every destination before the first write.
        if args.cmd in ("apply", "restore"):
            if wants_ide:
                preflight(app)
            if wants_cli:
                preflight_cli(args.cli_dir)

        if wants_ide:
            {"apply": cmd_apply, "restore": cmd_restore, "status": cmd_status}[args.cmd](app)
        if wants_cli:
            {
                "apply": cmd_apply_cli,
                "restore": cmd_restore_cli,
                "status": cmd_status_cli,
            }[args.cmd](args.cli_dir)
    except PatchError as exc:
        sys.exit(f"\n  x {exc}")
    except PermissionError as e:
        sys.exit(f"\n  x 权限不足/文件被占用: {e}\n"
                 f"    请先完全退出 Cursor, 并用【管理员身份】运行 PowerShell 后重试。")


if __name__ == "__main__":
    main()
