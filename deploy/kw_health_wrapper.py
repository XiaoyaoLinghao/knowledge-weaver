#!/usr/bin/env python3
"""KW Health Check Wrapper - 运行脚本并创建 alert flag"""
import json, glob, os, subprocess, sys

SCRIPT_DIR = "/home/openclaw/.openclaw/knowledge/scripts"
REPORTS_DIR = "/home/openclaw/.openclaw/knowledge/health_reports"
ALERT_FLAG = "/home/openclaw/.openclaw/knowledge/.alert_pending"
WEEKLY_ALERT = "/home/openclaw/.openclaw/knowledge/.weekly_alert_pending"

def run_daily():
    subprocess.run([sys.executable, f"{SCRIPT_DIR}/kw_health_check.py"], check=False)
    # 读取最新报告
    files = sorted(glob.glob(f"{REPORTS_DIR}/*.json"))
    if not files:
        return
    with open(files[-1]) as f:
        report = json.load(f)
    status = report.get("overall_status", "ok")
    if status in ("alert", "warning"):
        alert = {
            "kind": "daily",
            "date": report["report_date"],
            "status": status,
            "alerts": [
                {"name": k, "status": v.get("status"), "message": v.get("message")}
                for k, v in report.get("checks", {}).items()
                if v.get("status") in ("alert", "warning")
            ]
        }
        with open(ALERT_FLAG, "w") as f:
            json.dump(alert, f, ensure_ascii=False, indent=2)

def run_weekly():
    subprocess.run([sys.executable, f"{SCRIPT_DIR}/kw_weekly_trend.py"], check=False)
    # 周报总是推送（不管有无异常，一周一次）
    weekly = "/home/openclaw/.openclaw/knowledge/weekly_reports"
    files = sorted(glob.glob(f"{weekly}/2026-W*.md"))
    if not files:
        return
    # 读取周报前10行作为摘要
    with open(files[-1]) as f:
        lines = f.readlines()
    summary = "".join(lines[:15])
    alert = {
        "kind": "weekly",
        "file": files[-1],
        "summary": summary
    }
    with open(WEEKLY_ALERT, "w") as f:
        json.dump(alert, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["daily", "weekly"])
    args = parser.parse_args()
    if args.mode == "daily":
        run_daily()
    else:
        run_weekly()
