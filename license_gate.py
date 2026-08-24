#!/usr/bin/env python3
"""license_gate.py — Cursor SAND 工具的本地授权校验（离线 ed25519 + 过期锁定）。

配合 license_web.html 生成的 SANDLIC1 token 使用：
  * 启动时调用 check_and_gate(root) 做一次校验；
  * 无密钥 / 密钥无效 / 已过期 -> 弹窗说明并退出（软件打不开）；
  * 密钥有效 -> 返回 LicenseState，界面显示剩余天数。
纯标准库 + ed25519_pure.py，可被 PyInstaller 打包（无外部依赖）。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# 内置公钥（由 license-server/keys/client_public_key.py 生成，勿改）
LICENSE_PUBLIC_KEY_RAW = bytes.fromhex(
    "c43b00712f30c319abc615b8e4ad016e2332d8d6d3faa060f74ab6e6cbbeecb9"
)
TOKEN_PREFIX = "SANDLIC1"
PRODUCT = "cursor-sand-tool"
KEY_FILE_NAME = "license.key"
APP_DIR_NAME = "cursor-sand-tool"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ed25519_pure  # noqa: E402


# --------------------------------------------------------------------------- #
# token 编解码
# --------------------------------------------------------------------------- #
def b64u_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse_token(token: str) -> dict:
    """解析并验证 SANDLIC1.<payload>.<sig>，返回 payload；任何失败抛 ValueError。"""
    parts = (token or "").strip().split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise ValueError("密钥格式错误（应为 SANDLIC1.payload.signature）")
    try:
        payload_bytes = b64u_decode(parts[1])
        signature = b64u_decode(parts[2])
    except Exception as exc:
        raise ValueError(f"密钥编码错误: {exc}") from exc
    if not ed25519_pure.verify(LICENSE_PUBLIC_KEY_RAW, payload_bytes, signature):
        raise ValueError("签名校验失败（密钥不是本产品签发的）")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"密钥内容错误: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("密钥内容不是有效对象")
    return payload


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
@dataclass
class LicenseState:
    allowed: bool
    reason: str
    code: str = ""
    expires_at: int | None = None
    days_left: int | None = None
    customer: str = ""
    payload: dict | None = None


def evaluate_token(token: str) -> LicenseState:
    try:
        payload = parse_token(token)
    except ValueError as exc:
        return LicenseState(False, str(exc), "TOKEN_INVALID")

    if payload.get("product") != PRODUCT:
        return LicenseState(False, "密钥不属于本产品。", "TOKEN_INVALID")

    now = int(time.time())
    exp = int(payload.get("exp", 0))
    if exp and now >= exp:
        return LicenseState(False, "授权已过期。", "LICENSE_EXPIRED")

    return LicenseState(
        allowed=True,
        reason="ok",
        code="OK",
        expires_at=exp or None,
        days_left=max(0, (exp - now) // 86400) if exp else None,
        customer=str(payload.get("customer", "")),
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# 密钥文件存取
# --------------------------------------------------------------------------- #
def key_file_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / APP_DIR_NAME / KEY_FILE_NAME


def load_key() -> str:
    try:
        return key_file_path().read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_key(token: str) -> None:
    p = key_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token.strip() + "\n", encoding="utf-8")


def clear_key() -> None:
    try:
        key_file_path().unlink()
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- #
# tkinter 激活对话框（无 tk 环境时降级为控制台）
# --------------------------------------------------------------------------- #
def show_activation_dialog(parent) -> str | None:
    """弹窗让用户粘贴密钥；返回有效 token 或 None。"""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("激活需要图形界面。请把 license_web.html 生成的密钥粘贴到这里：")
        key = input("License Key: ").strip()
        return key if key else None

    dialog = tk.Toplevel()
    dialog.title("激活 Cursor SAND 工具")
    dialog.resizable(False, False)

    outer = tk.Frame(dialog, bg="#F5F6FA")
    outer.pack(fill="both", expand=True, padx=24, pady=20)

    tk.Label(outer, text="激活 Cursor SAND 工具", font=("Microsoft YaHei UI", 14, "bold"),
             fg="#1F2328", bg="#F5F6FA").pack(anchor="w")
    tk.Label(outer, text="请输入授权密钥（由 license_web.html 生成，含到期日期）：",
             font=("Microsoft YaHei UI", 10), fg="#57606A", bg="#F5F6FA",
             justify="left", wraplength=430).pack(anchor="w", pady=(6, 10))

    entry = tk.Text(outer, width=52, height=3, bg="#FFFFFF", fg="#000000",
                    insertbackground="#0969DA", relief="solid", borderwidth=1,
                    highlightbackground="#D0D7DE", highlightcolor="#0969DA",
                    font=("Consolas", 10))
    entry.pack(fill="x")

    status = tk.Label(outer, text="", font=("Microsoft YaHei UI", 9), bg="#F5F6FA", fg="#CF222E")
    status.pack(anchor="w", pady=(8, 0))

    result: dict = {"token": None}

    def on_ok():
        token = entry.get("1.0", "end").strip()
        state = evaluate_token(token)
        if state.allowed:
            save_key(token)
            result["token"] = token
            dialog.destroy()
        else:
            status.configure(text="✕ " + state.reason, fg="#FF6B63")

    def on_close():
        dialog.destroy()

    row = tk.Frame(outer, bg="#F5F6FA")
    row.pack(fill="x", pady=(16, 0))
    tk.Button(row, text="激活", command=on_ok, bg="#0969DA", fg="#FFFFFF", relief="flat",
              padx=18, pady=6, font=("Microsoft YaHei UI", 10, "bold")).pack(side="right")
    tk.Button(row, text="退出", command=on_close, bg="#EFF1F3", fg="#1F2328", relief="flat",
              padx=18, pady=6, font=("Microsoft YaHei UI", 10)).pack(side="right", padx=(0, 8))

    dialog.protocol("WM_DELETE_WINDOW", on_close)
    dialog.bind("<Escape>", lambda _e: on_close())
    try:
        dialog.update_idletasks()
        dw, dh = dialog.winfo_reqwidth(), dialog.winfo_reqheight()
        sw, sh = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
        dialog.geometry(f"+{(sw-dw)//2}+{(sh-dh)//3}")
    except Exception:
        pass
    dialog.deiconify()
    dialog.lift()
    dialog.attributes("-topmost", True)
    dialog.after(300, lambda: dialog.attributes("-topmost", False))
    dialog.focus_force()
    dialog.grab_set()
    dialog.wait_window()
    return result["token"]


def show_blocked(parent, state: LicenseState) -> None:
    """显示拦截原因并退出（过期/无效时调用）。"""
    try:
        import tkinter as tk
        from tkinter import messagebox

        title = "授权已过期" if state.code == "LICENSE_EXPIRED" else "授权无效"
        body = f"{state.reason}\n\n请联系作者获取新的授权密钥。"
        parent.withdraw()
        parent.update_idletasks()
        messagebox.showerror(title, body, parent=parent)
    except Exception:
        print(f"授权拦截: {state.reason}")
    try:
        parent.destroy()
    except Exception:
        pass


def show_welcome(parent, state: LicenseState) -> None:
    """激活成功提示（显示到期信息）。"""
    try:
        import tkinter as tk
        from tkinter import messagebox

        exp_txt = ""
        if state.expires_at:
            from datetime import datetime
            exp_txt = datetime.fromtimestamp(state.expires_at).strftime("%Y-%m-%d %H:%M")
        days_txt = f"（剩余 {state.days_left} 天）" if state.days_left is not None else ""
        msg = f"授权有效{exp_txt and f'，到期 {exp_txt} '}{days_txt}"
        if state.customer:
            msg += f"\n客户: {state.customer}"
        parent.withdraw()
        parent.update_idletasks()
        messagebox.showinfo("激活成功", msg, parent=parent)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 总入口：启动时调用
# --------------------------------------------------------------------------- #
def check_and_gate(parent) -> LicenseState:
    """启动授权检查。未激活 -> 弹激活框；无效/过期 -> 拦截退出；有效 -> 返回状态。"""
    token = load_key()
    if not token:
        state = LicenseState(False, "尚未激活", "NO_LICENSE")
    else:
        state = evaluate_token(token)

    if state.allowed:
        return state

    # 未激活 / 无效：弹激活框重试一次（也可在激活框内继续输入）
    if state.code == "NO_LICENSE" or state.code == "TOKEN_INVALID":
        new_token = show_activation_dialog(parent)
        if new_token:
            retry = evaluate_token(new_token)
            if retry.allowed:
                save_key(new_token)
                return retry
    # 过期或仍未通过 -> 锁定退出
    if state.code == "LICENSE_EXPIRED":
        show_blocked(parent, state)
    else:
        final = evaluate_token(load_key()) if load_key() else state
        if not final.allowed:
            show_blocked(parent, final)
        else:
            return final
    raise SystemExit(0)


if __name__ == "__main__":
    # 命令行自测：python license_gate.py <token>
    if len(sys.argv) > 1:
        st = evaluate_token(sys.argv[1])
        print(f"allowed={st.allowed} code={st.code} reason={st.reason} "
              f"exp={st.expires_at} days={st.days_left} customer={st.customer}")
    else:
        print("用法: python license_gate.py <SANDLIC1 token>")
