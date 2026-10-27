"""Command-line onboarding, inspection, scheduling, and reservation execution."""

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

from . import __version__
from .paths import default_config_path, default_auth_dir, prepare_config
from .browser import open_browser, origin_of, save_session
from .config import Target, example_config, load_config, save_config
from .runner import evidence, run_reservation
from .sjtu import ROOT, SjtuPage, validate_grid
from .state import Journal, account_lock, private_json


# ==========================================
# Function: Build the user-facing command line.
# Method: Share explicit config and account paths across independent subcommands.
# ==========================================
def parser():
    result = argparse.ArgumentParser(description="上海交大场馆自动预约：登录、选时段、提交订单后停止")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", default=str(default_config_path()), help="配置路径，默认程序目录/config/config.json")
    result.add_argument("--auth-dir", default=str(default_auth_dir()), help="独立账号状态目录，默认程序目录/.auth")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("desktop", help="打开原生桌面预约客户端")
    sub.add_parser("gui", help="原生客户端（desktop 的兼容别名）")
    sub.add_parser("init", help="创建示例配置，不覆盖已有文件")
    sub.add_parser("check", help="检查配置，不访问网站")
    sub.add_parser("configure", help="交互设置日期、场地、时段、启动时间和金额上限")
    sub.add_parser("login", help="手动登录并打开具体场馆，保存登录及场馆链接")
    scan = sub.add_parser("scan", help="读取指定日期的可用时段，不选择、不下单")
    scan.add_argument("--date", help="YYYY-MM-DD，留空为当前日期 tab")
    run = sub.add_parser("run", help="默认只预览选择；--submit 才真正提交")
    run.add_argument("--submit", action="store_true", help="执行勾选须知及提交订单，不付款")
    run.add_argument("--headless", action="store_true", help="隐藏浏览器；验证需要人工时停止")
    run.add_argument("--keep-open", action="store_true", help="结束后保留浏览器，按回车关闭")
    sub.add_parser("journal", help="查看持久提交记录及订单号")
    return result


# ==========================================
# Function: Collect booking preferences without editing JSON manually.
# Method: Prompt for each candidate and validate the full configuration.
# ==========================================
def configure(config, path):
    print("候选按输入顺序尝试，成功后只下一个订单；同一候选内多个时段必须全部可用。")
    config.sport = input(f"运动项目 [{config.sport}]：").strip() or config.sport
    targets = []
    while True:
        value = input("预约日期 YYYY-MM-DD（留空结束添加）：").strip()
        if not value:
            break
        court = input("场地名称（与网站一致，例如 场地1）：").strip()
        slots = input("时段，多个用逗号分隔（例如 14:00-15:00,15:00-16:00）：").strip()
        targets.append(Target(value, court, [s.strip() for s in slots.replace("，", ",").split(",")]))
    if targets:
        config.targets = targets
    value = input("定时启动 YYYY-MM-DDTHH:MM:SS+08:00（留空立即）：").strip()
    config.start_at = value or None
    config.max_total_price = input(f"订单金额上限（元）[{config.max_total_price}]：").strip() or config.max_total_price
    config.poll_interval_seconds = float(input(f"刷新间隔（秒）[{config.poll_interval_seconds}]：").strip() or config.poll_interval_seconds)
    config.max_poll_seconds = float(input(f"最长监测（秒）[{config.max_poll_seconds}]：").strip() or config.max_poll_seconds)
    config.validate()
    save_config(config, path)
    print(f"配置已保存：{Path(path).resolve()}")


# ==========================================
# Function: Capture authentication and the user's chosen venue page.
# Method: Let the user perform login, then record exact venue and sport DOM labels.
# ==========================================
def login(config, config_path, auth_dir):
    with open_browser(config, auth_dir, login=True) as (context, page):
        page.goto(config.booking_url, wait_until="domcontentloaded")
        print("请在打开的浏览器中完成 jAccount 登录，打开具体场馆的个人预约页，并选择运动项目。")
        input("看到日期和场地网格后，回到此终端按回车保存（无需点击下单）：")
        parsed = urlsplit(page.url)
        if origin_of(page.url) != "https://sports.sjtu.edu.cn" or not unquote(parsed.fragment).startswith("/apointmentDetails/1/"):
            raise RuntimeError("尚未打开具体场馆的个人预约页，未保存")
        shot = SjtuPage(page, config).snapshot()
        if not shot or not shot["venue"] or not shot["sport"]:
            raise RuntimeError("无法读取当前场馆和运动项目，请等待页面加载后重试")
        config.booking_url = page.url
        config.venue, config.sport = shot["venue"], shot["sport"]
        config.validate()
        save_session(context, page, auth_dir, origin_of(page.url))
        save_config(config, config_path)
        print(f"已保存场馆：{config.venue}；运动项目：{config.sport}")


# ==========================================
# Function: Read visible availability without selecting a slot.
# Method: Load the configured venue and print a structured scheduling snapshot.
# ==========================================
def scan(page, config, value, artifacts):
    site = SjtuPage(page, config)
    page.goto(config.booking_url, wait_until="domcontentloaded")
    site.ready()
    site.choose_sport()
    if value:
        from datetime import date
        date.fromisoformat(value)
        if not site.choose_date(value):
            raise ValueError("该日期尚未出现在网站可预约日期中")
    shot = site.loaded_snapshot(expected_sport=config.sport)
    if shot["columns"] or shot["rows"]:
        validate_grid(shot)
    print(json.dumps(shot, ensure_ascii=False, indent=2))
    private_json(artifacts / "availability.json", shot)
    page.screenshot(path=str(artifacts / "page.png"), full_page=True)


# ==========================================
# Function: Run one command with account isolation and outcome evidence.
# Method: Keep dependency-free checks separate from browser commands.
# ==========================================
def main(argv=None):
    os.umask(0o077)
    argv = sys.argv[1:] if argv is None else argv
    args = parser().parse_args(argv or ["desktop"])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = Path(args.config).resolve()
    auth_dir = Path(args.auth_dir).resolve()
    if args.command in {"desktop", "gui"}:
        from .desktop import serve_desktop
        return serve_desktop(config_path, auth_dir)
    config_path = prepare_config(config_path)
    if args.command == "init":
        if config_path.exists():
            raise FileExistsError(f"配置已存在，不覆盖：{config_path}")
        save_config(example_config(), config_path)
        print(f"配置已创建：{config_path}；下一步运行 login 和 configure")
        return 0
    with account_lock(auth_dir):
        if args.command == "journal":
            journal = Journal(auth_dir / "journal.sqlite3")
            try:
                print(json.dumps(journal.entries(), ensure_ascii=False, indent=2))
            finally:
                journal.close()
            return 0
        config = load_config(config_path)
        if args.command == "configure":
            configure(config, config_path)
            return 0
        if args.command == "check":
            config.validate(require_targets=True)
            print(f"配置有效：{config.venue} / {config.sport}，{len(config.goals)} 个目标，{len(config.targets)} 个旧版候选")
            return 0
        if args.command == "login":
            login(config, config_path, auth_dir)
            return 0
        config.validate(require_targets=args.command == "run")
        if not (auth_dir / "storage.json").exists() or not (auth_dir / "session.json").exists():
            raise RuntimeError("尚未保存登录，请先运行 login")
        if args.command == "run" and args.headless:
            config.headless = True
        if args.command == "run" and args.keep_open and config.headless:
            raise ValueError("--keep-open 需要有界面浏览器")
        artifacts = config_path.parent / "artifacts" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        artifacts.mkdir(parents=True, mode=0o700)
        handler = logging.FileHandler(artifacts / "run.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
        try:
            journal = Journal(auth_dir / "journal.sqlite3")
            try:
                if args.command == "run":
                    journal.ensure_new(config)
                with open_browser(config, auth_dir) as (context, page):
                    try:
                        if args.command == "scan":
                            scan(page, config, args.date, artifacts)
                            return 0
                        result = run_reservation(page, config, journal, artifacts, args.submit)
                        print(json.dumps(result, ensure_ascii=False, indent=2))
                        if args.keep_open:
                            input("浏览器已保留，按回车关闭：")
                        return 0 if result["status"] in {"preview", "submitted", "completed"} else 2
                    except BaseException as exc:
                        evidence(page, artifacts, {"status": "error", "error_type": type(exc).__name__, "message": str(exc)})
                        raise
                    finally:
                        if not page.is_closed() and origin_of(page.url) == origin_of(config.booking_url):
                            try:
                                save_session(context, page, auth_dir, origin_of(config.booking_url))
                            except Exception as exc:
                                logging.warning("未刷新登录文件：%s", type(exc).__name__)
            finally:
                journal.close()
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
            print(f"运行记录：{artifacts}")


# ==========================================
# Function: Present actionable terminal errors without a full traceback.
# Method: Handle user interruption and standard configuration/runtime failures.
# ==========================================
def entrypoint():
    try:
        return main()
    except KeyboardInterrupt:
        print("\n已停止；如处于提交阶段，请先核对网站“我的订单”。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
