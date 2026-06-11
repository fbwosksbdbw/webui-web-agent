# -*- coding: utf-8 -*-
import pymysql
import json

# 数据库连接
conn = pymysql.connect(
    host='10.20.29.245',
    port=3306,
    user='bta60',
    password='handsome',
    database='ui_bc',
    charset='utf8mb4'
)

try:
    with conn.cursor(pymysql.cursors.DictCursor) as cursor:
        # 先查看表结构
        cursor.execute("DESCRIBE tsys_menu")
        columns = cursor.fetchall()
        print("表结构:")
        for col in columns:
            print(f"  {col['Field']}: {col['Type']}")

        # 查询所有数据
        cursor.execute("SELECT * FROM tsys_menu")
        rows = cursor.fetchall()

        # 过滤掉 ifmcounter 相关的数据
        filtered_rows = []
        for row in rows:
            # 检查所有字段是否包含 ifmcounter（不区分大小写）
            row_str = json.dumps(row, ensure_ascii=False, default=str).lower()
            if 'ifmcounter' not in row_str:
                filtered_rows.append(row)

        print(f"\n原始数据量: {len(rows)}")
        print(f"过滤后数据量: {len(filtered_rows)}")

        # 保存到 JSON 文件
        output_path = r'E:\WebUI\code\claudeskill\.claude\skills\hui-playwright-test\config\111.json'
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(filtered_rows, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n数据已保存到: {output_path}")

finally:
    conn.close()
