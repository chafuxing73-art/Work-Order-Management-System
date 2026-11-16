#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在线备份工单数据库与现场照片，保留最近 30 份。
可加入 crontab：0 2 * * * /usr/bin/python3 /opt/工单管理/deploy/backup.py
"""
import datetime
import sqlite3
import tarfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
OUT = BASE / "backups"
DB = DATA / "tickets.db"
KEEP = 30


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if not DB.exists():
        print("还没有数据库，跳过备份")
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot = OUT / (".snapshot_" + stamp + ".db")
    src = sqlite3.connect(str(DB))
    dst = sqlite3.connect(str(snapshot))
    try:
        src.backup(dst)          # 在线一致性快照，不需要停服务
    finally:
        dst.close()
        src.close()

    target = OUT / ("工单备份_" + stamp + ".tar.gz")
    with tarfile.open(target, "w:gz") as tar:
        tar.add(snapshot, arcname="tickets.db")
        uploads = DATA / "uploads"
        if uploads.exists():
            tar.add(uploads, arcname="uploads")
        tar.add(BASE / "config.json", arcname="config.json")
    snapshot.unlink(missing_ok=True)

    files = sorted(OUT.glob("工单备份_*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        old.unlink()
    print("备份完成: %s (%.1f MB)，当前保留 %d 份" %
          (target, target.stat().st_size / 1024 / 1024, min(len(files), KEEP)))


if __name__ == "__main__":
    main()
