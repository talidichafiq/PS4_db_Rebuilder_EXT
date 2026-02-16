#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function
from ftplib import FTP
import sqlite3
import appinfo
import io
import os
import re
from sfo.sfo import SfoFile as SfoFile
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("PS4_IP", help="PS4 ftp ip address")
parser.add_argument('--fw', default="6.72", help='currently support 5.05, 6.72 (12.50 use 6.72 format)')
parser.add_argument('--port', default="2121", help='PS4 FTP Port Number')
args = parser.parse_args()

app_db = "tmp/app.db"
PS4_IP = args.PS4_IP
port = int(args.port)

# ===== value format (rows for tbl_appbrowse_*) =====
# NOTE: 12.50 uses the 6.72-ish schema but with extra columns. We'll insert only first 53 columns.
value_format = ""
if args.fw == "5.05":
    value_format = """("%s", "%s", "%s", "/user/appmeta/%s", "2018-07-27 15:06:46.822", "0", "0", "5", "1", "100", "0", "151", "5", "1", "gd", "0", "0", "0", "0", NULL, NULL, NULL, "%d", "2018-07-27 15:06:46.802", "0", "game", NULL, "0", "0", NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "0", NULL, NULL, NULL, NULL, NULL, "0", "0", NULL, "2018-07-27 15:06:46.757")"""
else:
    # 6.72 + (and works for 9.00/11.00/12.50 when we limit columns)
    value_format = """("%s", "%s", "%s", "/user/appmeta/%s", "2018-07-27 15:06:46.822", "0", "0", "5", "1", "100", "0", "151","5", "1", "gd", "0", "0", "0", "0",NULL, NULL, NULL, "%d", "2018-07-27 15:06:46.802", "0", "game", NULL, "0", "0", NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "0", NULL,NULL, NULL, NULL, NULL, "0", "0", NULL, "2018-07-27 15:06:46.757","0","0","0","0","0",NULL)"""

# This is the number of VALUES the format supplies for 6.72/12.50 insert.
# For 12.50 table may have more columns, but we insert only first 53.
NV_672 = 53

if not os.path.exists('tmp'):
    os.makedirs('tmp')

class CUSA:
    def __init__(self):
        self.sfo = None
        self.size = 10000000
        self.is_usable = False

info = {}
files = []

def sort_files(line):
    # line is a string from ftp.dir callback
    # The original script used file[-9] and file[-9:], keep same behavior but safer:
    if len(line) >= 9:
        tail = line[-9:]
        if re.search(r"^[A-Z]", tail[0]):
            files.append("'%s'" % tail)

def get_game_info_by_id(GameID):
    if GameID not in info:
        info[GameID] = CUSA()
        try:
            buffer = io.BytesIO()
            ftp.cwd('/system_data/priv/appmeta/%s/' % GameID)
            ftp.retrbinary("RETR param.sfo", buffer.write)
            buffer.seek(0)

            sfo = SfoFile.from_reader(buffer)
            info[GameID].sfo = sfo

            # app.pkg size
            info[GameID].size = ftp.size("/user/app/%s/app.pkg" % GameID)
            info[GameID].is_usable = True
        except Exception as e:
            print("Error processing %s, ignoring..." % GameID)
            print("type error: " + str(e))
    return info[GameID]

def insert_appbrowse_rows(cursor, table_name, sql_list, nvals=NV_672):
    """
    Fix for 12.50 schema mismatch:
    - If table has more columns than values, insert only first nvals columns.
    """
    if not sql_list:
        return

    cols = [r[1] for r in cursor.execute("PRAGMA table_info(%s);" % table_name).fetchall()]
    if len(cols) >= nvals:
        cursor.execute(
            "INSERT INTO %s (%s) VALUES %s;" %
            (table_name, ', '.join(cols[:nvals]), ', '.join(sql_list))
        )
    else:
        # Fallback: original behavior
        cursor.execute("INSERT INTO %s VALUES %s;" % (table_name, ', '.join(sql_list)))

# ===== FTP connect =====
ftp = FTP()
ftp.connect(PS4_IP, port, timeout=30)

# PS4 FTP غالباً ما كيحتاجش username/password
# إذا عندك سيرفر كيطلب، بدّل هنا:
ftp.login()  # anonymous

# Build CUSA list from /user/app/
if len(files) == 0:
    ftp.cwd('/user/app/')
    ftp.dir(sort_files)
    print(files)

# Download app.db
ftp.cwd('/system_data/priv/mms/')
with open(app_db, "wb") as lf:
    ftp.retrbinary("RETR app.db", lf.write)

# SQLite open
conn = sqlite3.connect(app_db)
cursor = conn.cursor()

# Find tbl_appbrowse_* tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'tbl_appbrowse_%';")
tables = cursor.fetchall()

files_joined = "SELECT %s AS titleid " % ' AS titleid UNION SELECT '.join(files)
tbl_appbrowse = []

# ===== Process tbl_appbrowse_* =====
for tbl in tables:
    tbl_name = tbl[0]
    tbl_appbrowse.append(tbl_name)

    print("Processing table: %s" % tbl_name)
    cursor.execute(
        "SELECT T.titleid FROM (%s) T WHERE T.titleid NOT IN (SELECT titleid FROM %s);" %
        (files_joined, tbl_name)
    )
    list_id = cursor.fetchall()

    sql_list = []
    for tmp_GameID in list_id:
        GameID = tmp_GameID[0].replace("'", "")
        print(" Processing GameID: %s... " % GameID, end='')

        cusa = get_game_info_by_id(GameID)
        if cusa.is_usable:
            sql_list.append(
                value_format % (
                    cusa.sfo['TITLE_ID'],
                    cusa.sfo['CONTENT_ID'],
                    cusa.sfo['TITLE'],
                    cusa.sfo['TITLE_ID'],
                    cusa.size
                )
            )
            print("Completed %d" % cusa.size)
        else:
            print("Skipping")

    if len(sql_list) > 0:
        insert_appbrowse_rows(cursor, tbl_name, sql_list, nvals=NV_672)

print("\n\n")
print("Processing table: tbl_appinfo")

# ===== Process tbl_appinfo =====
# Collect missing titleids across all tbl_appbrowse_* tables
union_tbls = " UNION SELECT titleid FROM ".join(tbl_appbrowse)
cursor.execute(
    "SELECT DISTINCT T.titleid FROM (SELECT titleid FROM %s) T "
    "WHERE T.titleid NOT IN (SELECT DISTINCT titleid FROM tbl_appinfo);" % union_tbls
)
missing_appinfo_cusa_id = cursor.fetchall()

for tmp_cusa_id in missing_appinfo_cusa_id:
    game_id = tmp_cusa_id[0]
    print(" Processing GameID: %s... " % game_id, end='')

    cusa = get_game_info_by_id(game_id)
    if cusa.is_usable:
        sql_items = appinfo.get_pseudo_appinfo(cusa.sfo, cusa.size)

        # Insert key/value rows into tbl_appinfo
        # Use OR IGNORE to avoid duplicates
        for key, value in sql_items.items():
            cursor.execute(
                "INSERT OR IGNORE INTO tbl_appinfo (titleid, key, val) VALUES (?, ?, ?);",
                (cusa.sfo['TITLE_ID'], str(key), str(value))
            )
        print("Completed")
    else:
        print("Skipped")

# Save and close db
conn.commit()
conn.close()

# Upload app.db back to PS4
ftp.cwd('/system_data/priv/mms/')
with open(app_db, 'rb') as f:
    ftp.storbinary('STOR app.db', f)

ftp.quit()
print("\nDone.")
